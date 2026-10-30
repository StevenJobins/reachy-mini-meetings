// Reachy follows the user's head. Starts by itself once the app has the robot: wake if needed, take the current head
// direction as "straight ahead", follow. Hands: hold the right pinch (or trigger) to talk, like a walkie-talkie, so the
// mic can never stay on by accident; left pinch recenters.

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
    const double SleepPitchDeg = 18;   // measured pitch above this = still in the sleep pose (see robot.js wake())
    const float MoveTimeoutS = 8f;
    // adb ... -e sweep 1: bench test without a person: slow yaw sweep, see VideoWindow's recording
    const float SweepS = 24f, SweepPeriodS = 4f, SweepYawDeg = 30f;

    public RobotVideo video;
    public Transform head;

    readonly Recenter recenter = new Recenter();
    InputAction talk, recenterPress;
    DaemonClient daemon;
    HeadMirror mirror = new HeadMirror();
    readonly HeadModel headModel = new HeadModel();
    HeadTarget lastSent;
    Mode mode = Mode.Idle;
    bool talking, recenterDown, started;
    float talkReleasedAt = float.NegativeInfinity;
    int sent, sentAtMark;
    float markAt;
    float sendHz;
    string note = "";
    double lastBaseYaw, lastBasePitch, loggedYaw, loggedPitch;
    bool sweep;
    float sweepAt = -1f;

    public string Summary { get; private set; } = "";
    public bool IsFollowing => mode == Mode.Following;
    public bool MicOn { get; private set; }
    public bool Sweeping => sweep && sweepAt >= 0;
    public Recenter Frame => recenter;
    public Quat HeadRotation => HeadNow();
    public DaemonClient Daemon => daemon;
    /// <summary>What we commanded, for comparing with the measured pose (VideoWindow).</summary>
    public PoseHistory Commanded { get; } = new PoseHistory();
    /// <summary>The command through the head's mechanical play: where the head should end up, for the video window.</summary>
    public PoseHistory Modelled { get; } = new PoseHistory();
    /// <summary>Speaker following; set by CaptionView, which owns the faces.</summary>
    public SpeakerFollow Follow { get; set; }
    /// <summary>The base yaw and pitch that went into each command (Yaw, Pitch), for the video window.</summary>
    public PoseHistory BaseHistory { get; } = new PoseHistory();
    /// <summary>How fast the base turns right now, °/s: the picture moves without the user's own head motion.</summary>
    public double BaseRate { get; private set; }

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
        if (!sweep && !started && Application.isFocused && daemon.TargetConnected && daemon.Latest?.head_pose != null
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
                : PoseMath.HeadsetToRobot(recenter.ToRelative(HeadNow()));
            double baseYaw = 0, basePitch = 0;
            if (Follow != null && !Sweeping)
            {
                double dt = Time.unscaledDeltaTime;
                baseYaw = Follow.Step(dt, DaemonClient.NowS());
                basePitch = Follow.BasePitch;
                if (dt > 0)
                {
                    double dy = baseYaw - lastBaseYaw, dp = basePitch - lastBasePitch;
                    BaseRate = System.Math.Sqrt(dy * dy + dp * dp) / dt;
                }
                lastBaseYaw = baseYaw;
                lastBasePitch = basePitch;
                raw.Yaw += baseYaw;       // the user's head rotation on top of the speaker direction ...
                raw.Pitch += basePitch;   // ... and on top of the framing pitch
                LogFollow();
            }
            lastSent = mirror.Step(raw, Time.unscaledDeltaTime);
            daemon.SendTarget(lastSent);
            double nowS = DaemonClient.NowS();
            var commanded = new RobotAngles(lastSent.Roll, lastSent.Pitch, lastSent.Yaw);
            Commanded.Add(nowS, commanded);
            Modelled.Add(nowS, headModel.Step(commanded));
            BaseHistory.Add(nowS, new RobotAngles(0, basePitch, baseYaw));
            sent++;
        }
        UpdateSummary();
    }

    /// <summary>Who moved the target, when it jumps more than 4°.</summary>
    void LogFollow()
    {
        if (System.Math.Abs(Follow.Tracker.Target - loggedYaw) <= 4 && System.Math.Abs(Follow.TargetPitch - loggedPitch) <= 4) return;
        loggedYaw = Follow.Tracker.Target;
        loggedPitch = Follow.TargetPitch;
        Debug.Log($"[follow] yaw {loggedYaw:0} pitch {loggedPitch:0} by {Follow.Source}");
    }

    void HandlePress()
    {
        float level = talk.ReadValue<float>();
        if (level > PressLevel) talking = true;
        else if (level < ReleaseLevel) talking = false;
        if (talking) talkReleasedAt = Time.unscaledTime;
        bool mic = talking || Time.unscaledTime - talkReleasedAt < MicHoldS;
        if (mic != MicOn)
        {
            MicOn = mic;
            Debug.Log($"[control] mic {(mic ? "on" : "off")}");
        }

        bool down = recenterPress.ReadValue<float>() > PressLevel;
        if (down && !recenterDown && mode == Mode.Following)
        {
            recenter.Set(HeadNow());
            Debug.Log("[control] recentered");
        }
        recenterDown = down;
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
        if (Follow != null) { Follow.Reset(); Follow.Active = true; }
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
            Mode.Idle => $"waiting for the robot{link}",
            Mode.Following => $"following {sendHz:0} Hz  yaw {lastSent.Yaw:0} pitch {lastSent.Pitch:0}{link}",
            _ => $"{note}{link}",
        };
    }
}
