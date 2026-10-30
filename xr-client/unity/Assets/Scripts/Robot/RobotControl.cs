// Reachy follows the user's head. It never moves by itself: nothing is sent until the user pinches (or pulls the
// trigger). Pinch = wake if needed, take this as "straight ahead", follow. Pinch again = recenter.

using System.Collections;
using System.Globalization;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.Networking;

public class RobotControl : MonoBehaviour
{
    enum Mode { Idle, Waking, Following }

    const float PressLevel = 0.8f;
    const double SleepPitchDeg = 18;   // measured pitch above this = still in the sleep pose (see robot.js wake())
    const float MoveTimeoutS = 8f;
    // adb ... -e sweep 1: bench test without a person: slow yaw sweep, see VideoWindow's recording
    const float SweepS = 24f, SweepPeriodS = 4f, SweepYawDeg = 30f;

    public RobotVideo video;
    public Transform head;

    readonly Recenter recenter = new Recenter();
    InputAction press;
    DaemonClient daemon;
    HeadMirror mirror = new HeadMirror();
    HeadTarget lastSent;
    Mode mode = Mode.Idle;
    bool pressed;
    int sent, sentAtMark;
    float markAt;
    float sendHz;
    string note = "";
    bool sweep;
    float sweepAt = -1f;

    public string Summary { get; private set; } = "";
    public bool IsFollowing => mode == Mode.Following;
    public bool Sweeping => sweep && sweepAt >= 0;
    public Recenter Frame => recenter;
    public Quat HeadRotation => HeadNow();
    public DaemonClient Daemon => daemon;
    /// <summary>What we commanded, for comparing with the measured pose (VideoWindow).</summary>
    public PoseHistory Commanded { get; } = new PoseHistory();

    void Awake()
    {
        press = new InputAction("Press", InputActionType.Value, expectedControlType: "Axis");
        press.AddBinding("<HandInteraction>/pinchValue");
        press.AddBinding("<XRController>/trigger");
        press.Enable();
        sweep = RobotVideo.ReadIntentExtra("sweep") == "1";
    }

    void OnDestroy()
    {
        press.Dispose();
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
        if (Sweeping && Time.unscaledTime - sweepAt >= SweepS) { sweep = false; sweepAt = -1f; mode = Mode.Idle; }
        if (mode == Mode.Following && (Application.isFocused || Sweeping))
        {
            var raw = Sweeping
                ? new RobotAngles(0, 0, SweepYawDeg * Mathf.Sin(2 * Mathf.PI * (Time.unscaledTime - sweepAt) / SweepPeriodS))
                : PoseMath.HeadsetToRobot(recenter.ToRelative(HeadNow()));
            lastSent = mirror.Step(raw, Time.unscaledDeltaTime);
            daemon.SendTarget(lastSent);
            Commanded.Add(DaemonClient.NowS(), new RobotAngles(lastSent.Roll, lastSent.Pitch, lastSent.Yaw));
            sent++;
        }
        UpdateSummary();
    }

    void HandlePress()
    {
        bool now = press.ReadValue<float>() > PressLevel;
        if (!now && pressed)
        {
            if (mode == Mode.Idle) StartCoroutine(Wake());
            else if (mode == Mode.Following) { recenter.Set(HeadNow()); note = "recentered"; Debug.Log("[control] recentered"); }
        }
        pressed = now;
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
            Mode.Idle => $"pinch to start{link}",
            Mode.Following => $"following {sendHz:0} Hz  yaw {lastSent.Yaw:0} pitch {lastSent.Pitch:0} {note}{link}",
            _ => $"{note}{link}",
        };
    }
}
