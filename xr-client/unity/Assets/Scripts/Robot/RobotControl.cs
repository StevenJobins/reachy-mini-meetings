// Reachy follows the user's head. Starts by itself once the app has the robot: wake if needed, take the current head
// direction as "straight ahead", follow. Hands: hold the right pinch (or trigger) to talk, like a walkie-talkie, so the
// mic can never stay on by accident (a right pinch on a menu tile or the notes works those instead); a left pinch held for 0.7 s recenters, or skips the room scan while it runs; a short one, while you look
// at an entry of the QuickMenu, switches that; on the notes card's handle, drags it; while the Tutorial is open, it
// goes to the tutorial, and Reachy neither follows nor hears you (practice): it wakes up and scans the room meanwhile
// (RoomPanorama, behind the Curtain), then waits looking straight ahead; closing the tutorial recenters. Reachy only turns
// across its front half (YawRange): a user turning all the way round does not drag it round its cables.

using System.Collections;
using System.Globalization;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.Networking;

public class RobotControl : MonoBehaviour
{
    enum Mode { Idle, Waking, Following }

    const float PressLevel = 0.8f, ReleaseLevel = 0.5f;
    const float MicHoldS = 0.15f;      // a tracking dropout for a few frames must not cut a word
    // Recentering and skipping the scan need the left pinch held this long: a short left pinch happens by accident
    // (headset test 2026-11-11: nine recenters in eleven seconds, and the scan skipped, while aiming at the menu)
    const float RecenterHoldS = 0.7f;
    const double SleepPitchDeg = 18;   // measured pitch above this = still in the sleep pose (see robot.js wake())
    const float MoveTimeoutS = 8f;
    // adb ... -e sweep 1: bench test without a person: slow yaw sweep, see VideoWindow's recording
    const float SweepS = 24f, SweepPeriodS = 4f, SweepYawDeg = 30f;

    public RobotVideo video;
    public Transform head;
    public QuickMenu menu;
    public Tutorial tutorial;
    public NotesPanel notes;

    readonly Recenter recenter = new Recenter();
    InputAction talk, recenterPress;
    DaemonClient daemon;
    HeadMirror mirror = new HeadMirror();
    readonly HeadModel headModel = new HeadModel();
    HeadTarget lastSent;
    Mode mode = Mode.Idle;
    bool talking, recenterDown, started, skip, wasPaused;
    bool rightDown, rightUi;   // rightUi: this right pinch went to the menu or the notes, so it is not talking
    bool leftUsed;             // this left pinch already did something
    float leftSince;
    float talkReleasedAt = float.NegativeInfinity;
    int sent, sentAtMark;
    float markAt;
    float sendHz;
    string note = "";
    bool sweep;
    float sweepAt = -1f;

    public string Summary { get; private set; } = "";
    public bool IsFollowing => mode == Mode.Following;
    public bool Waking => mode == Mode.Waking;
    /// <summary>Sent to sleep from the menu: stays asleep until WakeUp().</summary>
    public bool Asleep { get; private set; }
    public bool GoingToSleep { get; private set; }
    public bool MicOn { get; private set; }
    /// <summary>Pinch strength 0..1, for the tutorial's live view of the fingers.</summary>
    public float RightLevel { get; private set; }
    public float LeftLevel { get; private set; }
    /// <summary>Left pinch held, for dragging the notes card.</summary>
    public bool LeftHeld => recenterDown;
    public bool RightHeld => rightDown;
    bool Paused => tutorial != null && tutorial.Open;   // the tutorial is practice: Reachy stays still meanwhile
    /// <summary>Set by the room scan: where to look instead of following the head.</summary>
    public RobotAngles? Override { get; set; }
    public bool Sweeping => sweep && sweepAt >= 0;
    public Recenter Frame => recenter;
    public Quat HeadRotation => HeadNow();
    public DaemonClient Daemon => daemon;
    /// <summary>What we commanded, for comparing with the measured pose (VideoWindow).</summary>
    public PoseHistory Commanded { get; } = new PoseHistory();
    /// <summary>The command through the head's mechanical play: where the head should end up, for the video window.</summary>
    public PoseHistory Modelled { get; } = new PoseHistory();

    void Awake()
    {
        talk = Press("Talk", "RightHand");
        recenterPress = Press("Recenter", "LeftHand");
        sweep = RobotVideo.ReadIntentExtra("sweep") == "1";
    }

    static InputAction Press(string name, string hand)
    {
        var action = new InputAction(name, InputActionType.Value, expectedControlType: "Axis");
        action.AddBinding($"<HandInteraction>{{{hand}}}/pinchValue");
        action.AddBinding($"<XRController>{{{hand}}}/trigger");
        action.Enable();
        return action;
    }

    void OnDestroy()
    {
        talk.Dispose();
        recenterPress.Dispose();
        daemon?.Dispose();
    }

    /// <summary>True once after a left pinch during the scan.</summary>
    public bool ConsumeSkip()
    {
        bool s = skip;
        skip = false;
        return s;
    }

    Quat HeadNow()
    {
        var q = head.localRotation;
        return Quat.FromUnity(q.x, q.y, q.z, q.w);
    }

    void Update()
    {
        if (daemon == null)
        {
            if (string.IsNullOrEmpty(video.SignallingUrl)) return;
            daemon = new DaemonClient(new System.Uri(video.SignallingUrl).Host);
        }
        HandlePress();
        if (sweep && sweepAt < 0 && daemon.TargetConnected && daemon.Latest != null)
        {
            sweepAt = Time.unscaledTime;
            mirror = new HeadMirror();
            mode = Mode.Following;
        }
        if (wasPaused && !Paused && mode == Mode.Following) Recenter();   // the tutorial closed: where you look is straight ahead
        wasPaused = Paused;
        if (!sweep && !started && !Asleep && Application.isFocused && daemon.TargetConnected && daemon.Latest?.head_pose != null
            && video.VideoTexture != null)
        {
            started = true;
            StartCoroutine(Wake());
        }
        if (Sweeping && Time.unscaledTime - sweepAt >= SweepS) { sweep = false; sweepAt = -1f; mode = Mode.Idle; }
        if (mode == Mode.Following && (Application.isFocused || Sweeping))
        {
            var raw = Sweeping
                ? new RobotAngles(0, 0, SweepYawDeg * Mathf.Sin(2 * Mathf.PI * (Time.unscaledTime - sweepAt) / SweepPeriodS))
                : Override ?? (Paused ? new RobotAngles(0, 0, 0)
                    : YawRange.Fold(PoseMath.HeadsetToRobot(recenter.ToRelative(HeadNow()))));
            lastSent = mirror.Step(raw, Time.unscaledDeltaTime);
            daemon.SendTarget(lastSent);
            double nowS = DaemonClient.NowS();
            var commanded = new RobotAngles(lastSent.Roll, lastSent.Pitch, lastSent.Yaw);
            Commanded.Add(nowS, commanded);
            Modelled.Add(nowS, headModel.Step(commanded));
            sent++;
        }
        UpdateSummary();
    }

    void HandlePress()
    {
        float level = Mathf.Max(talk.ReadValue<float>(), XrInput.PinchRight);   // profile or hand joints
        RightLevel = level;
        bool rightNow = level > PressLevel || rightDown && level > ReleaseLevel;
        if (rightNow && !rightDown)
        {
            // a right pinch on the notes or a menu tile works them, like the left one, and does not talk
            if (notes != null && notes.Hovered(true)) { notes.Press(true); rightUi = true; }
            else if (menu != null && menu.Hovered(true)) { menu.Select(true); rightUi = true; }
        }
        if (!rightNow) rightUi = false;
        rightDown = rightNow;
        talking = rightNow && !rightUi;
        if (talking) talkReleasedAt = Time.unscaledTime;
        bool mic = !Paused && (talking || Time.unscaledTime - talkReleasedAt < MicHoldS);
        if (mic != MicOn)
        {
            MicOn = mic;
            Debug.Log($"[control] mic {(mic ? "on" : "off")}");
        }

        float left = Mathf.Max(recenterPress.ReadValue<float>(), XrInput.PinchLeft);
        LeftLevel = left;
        bool down = left > PressLevel || recenterDown && left > ReleaseLevel;
        bool pressed = down && !recenterDown;
        if (pressed)
        {
            leftSince = Time.unscaledTime;
            leftUsed = true;
            if (notes != null && notes.Hovered(false)) notes.Press(false);
            else if (menu != null && menu.Hovered(false)) menu.Select(false);
            else if (Paused && !tutorial.WantsHold) tutorial.LeftPinch();
            else leftUsed = false;   // nothing under it: held long enough, it recenters
        }
        if (down && !leftUsed && Time.unscaledTime - leftSince >= RecenterHoldS)
        {
            leftUsed = true;
            if (Paused) tutorial.LeftPinch();
            else if (mode == Mode.Following)
            {
                if (Override != null) skip = true;
                else Recenter();
            }
        }
        recenterDown = down;
    }

    /// <summary>Your current head direction becomes straight ahead.</summary>
    public void Recenter()
    {
        recenter.Set(HeadNow());
        Debug.Log("[control] recentered");
    }

    IEnumerator Post(string path)
    {
        using var r = new UnityWebRequest(daemon.HttpBase + path, "POST") { downloadHandler = new DownloadHandlerBuffer() };
        yield return r.SendWebRequest();
        if (r.result != UnityWebRequest.Result.Success) { note = $"{path}: {r.error}"; Debug.LogWarning($"[control] {path}: {r.error}"); }
    }

    /// <summary>Wait until no daemon move is running: it ignores targets during a move.</summary>
    IEnumerator WaitForMoves()
    {
        float deadline = Time.unscaledTime + MoveTimeoutS;
        while (Time.unscaledTime < deadline)
        {
            yield return new WaitForSecondsRealtime(0.2f);
            using var r = UnityWebRequest.Get(daemon.HttpBase + "/api/move/running");
            yield return r.SendWebRequest();
            if (r.result == UnityWebRequest.Result.Success && r.downloadHandler.text.Trim() == "[]") yield break;
        }
    }

    /// <summary>From the menu: Reachy lies down and its motors go off, until WakeUp().</summary>
    public void Sleep()
    {
        if (mode == Mode.Following && !Asleep) StartCoroutine(GoToSleep());
    }

    /// <summary>From the menu: the normal start again (wake-up motion, straight ahead = where you look).</summary>
    public void WakeUp()
    {
        if (!Asleep || GoingToSleep) return;
        Asleep = false;
        started = false;
    }

    IEnumerator GoToSleep()
    {
        Asleep = GoingToSleep = true;
        mode = Mode.Waking;   // no targets meanwhile: the daemon ignores them during a move
        note = "going to sleep";
        Override = null;
        Debug.Log("[control] going to sleep");
        yield return Post("/api/move/play/goto_sleep");
        yield return WaitForMoves();
        yield return Post("/api/motors/set_mode/disabled");
        note = "";
        mode = Mode.Idle;
        GoingToSleep = false;
    }

    IEnumerator Wake()
    {
        mode = Mode.Waking;
        note = "waking";
        var s = daemon.Latest;
        // Motors on with the head still in the sleep pose happens after a daemon restart: play the motion then too
        bool asleep = s == null || s.control_mode != "enabled" || s.head_pose.pitch * PoseMath.Deg > SleepPitchDeg;
        Debug.Log($"[control] start, control_mode {s?.control_mode}, needs wake-up {asleep}");
        if (asleep)
        {
            yield return Post("/api/motors/set_mode/enabled");
            yield return Post("/api/move/play/wake_up");
            yield return WaitForMoves();
        }
        mirror = new HeadMirror();   // start from neutral, where the wake-up motion ends
        recenter.Set(HeadNow());
        note = "";
        mode = Mode.Following;
    }

    void UpdateSummary()
    {
        if (Time.unscaledTime - markAt >= 1f)
        {
            sendHz = (sent - sentAtMark) / (Time.unscaledTime - markAt);
            sentAtMark = sent;
            markAt = Time.unscaledTime;
            var m = daemon.Latest;
            if (mode == Mode.Following && m?.head_pose != null)
                Debug.Log(string.Format(CultureInfo.InvariantCulture,
                    "[control] {0:0} Hz sent y{1:0.0} p{2:0.0} r{3:0.0} b{4:0.0} measured y{5:0.0} p{6:0.0} r{7:0.0} b{8:0.0}",
                    sendHz, lastSent.Yaw, lastSent.Pitch, lastSent.Roll, lastSent.BodyYaw,
                    m.head_pose.yaw * PoseMath.Deg, m.head_pose.pitch * PoseMath.Deg, m.head_pose.roll * PoseMath.Deg, m.body_yaw * PoseMath.Deg));
        }
        var link = daemon.TargetConnected ? daemon.LastError ?? "" : $"  no daemon link {daemon.LastError}";
        Summary = mode switch
        {
            Mode.Idle => $"{(Asleep ? "asleep (menu: wake up)" : "waiting for the robot")}{link}",
            Mode.Following => $"following {sendHz:0} Hz  yaw {lastSent.Yaw:0} pitch {lastSent.Pitch:0}{link}",
            _ => $"{note}{link}",
        };
    }
}
