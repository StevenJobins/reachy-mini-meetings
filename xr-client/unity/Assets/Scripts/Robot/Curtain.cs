// No picture while it would only disturb: during the tutorial (a picture behind the card distracts), while Reachy
// wakes up, and during the room scan at the start, when Reachy turns by itself and a picture moving without your own
// head makes you feel sick. The video window, the room panorama and the status text are hidden and the background is
// black; without the tutorial a small card says what Reachy is doing. Opens once the scan is over, or after a few
// seconds when there is no robot to wait for (fake camera, daemon not reachable).

using UnityEngine;

public class Curtain : MonoBehaviour
{
    const float NoRobotS = 8f;       // nothing woke up within this: show the picture as it is
    const float NoScanS = 4f;        // following for this long without a scan starting: do not wait for one
    const float Distance = 1.5f, FollowRate = 3f, TextScale = 0.8f, Pad = 0.07f;
    // A calm dark blue instead of black, and one line without counting: a black screen with "3/9" over it felt like
    // a loading screen for developers (headset test 2026-11-11)
    static readonly Color Calm = new Color(0.05f, 0.08f, 0.14f);

    public RobotControl control;
    public Tutorial tutorial;
    public RoomSound room;
    public QuickMenu menu;
    public NotesPanel notes;
    public CaptionView captions;
    public SpeakerBubbles bubbles;
    public RoomPanorama panorama;
    public Renderer window, floor;
    public FpsLabel label;
    public Transform head;
    public Material textMaterial, panelMaterial;

    Camera eye;
    Color background;
    bool lifted, hidden, floorWas;
    float startedAt, followingSince = -1f, yaw, rawAt;
    const float SettleS = 1.0f;   // the picture comes this long after the last motion: Reachy turns back to you first
    Transform card, mascot;
    readonly Transform[] antennas = new Transform[2];
    readonly Ui.Rect[] eyes = new Ui.Rect[2];
    // Asleep: no picture, only a large button to wake Reachy up (either hand's ray + pinch)
    Ui.Rect wake;
    TextMesh wakeText;
    readonly bool[] onWake = new bool[2];
    readonly Ui.Cursor[] cursors = new Ui.Cursor[2];
    const float WakeW = 0.62f, WakeH = 0.16f;
    static readonly Vector3 WakeAt = new Vector3(0, -0.26f, -0.001f);

    /// <summary>That hand points at the wake-up button: its pinch goes here (RobotControl).</summary>
    public bool Hovered(bool right) => onWake[right ? 1 : 0];

    public void Press(bool right)
    {
        if (onWake[right ? 1 : 0]) control.WakeUp();
    }
    Ui.Rect panel;
    TextMesh text;
    string shown;

    void Awake()
    {
        window.enabled = false;   // not one camera frame before the loading screen
    }

    /// <summary>A little Reachy for the loading screen: head, two eyes, two antennas, drawn with rounded rectangles.</summary>
    void BuildMascot()
    {
        mascot = new GameObject("Mascot").transform;
        mascot.SetParent(card, false);
        mascot.localPosition = new Vector3(0, 0.3f, 0);
        mascot.localScale = Vector3.one * 1.5f;
        var white = new Color(0.93f, 0.95f, 0.98f);
        var dark = new Color(0.06f, 0.09f, 0.16f);
        for (int i = 0; i < 2; i++)
        {
            float side = i == 0 ? -1 : 1;
            antennas[i] = new GameObject("Antenna").transform;
            antennas[i].SetParent(mascot, false);
            antennas[i].localPosition = new Vector3(side * 0.09f, 0.085f, 0.001f);
            Ui.Quad(antennas[i], "Rod", panelMaterial, 3030).Set(0.012f, 0.13f, 0.006f, new Vector3(0, 0.065f, 0), white);
            Ui.Quad(antennas[i], "Tip", panelMaterial, 3030).Set(0.032f, 0.032f, 0.016f, new Vector3(0, 0.135f, 0), new Color(0.22f, 0.74f, 0.97f));
        }
        Ui.Quad(mascot, "Head", panelMaterial, 3031).Set(0.3f, 0.2f, 0.085f, Vector3.zero, white);
        for (int i = 0; i < 2; i++)
        {
            float side = i == 0 ? -1 : 1;
            eyes[i] = Ui.Quad(mascot, "Eye", panelMaterial, 3032);
            eyes[i].Set(0.075f, 0.075f, 0.0375f, new Vector3(side * 0.07f, 0.005f, -0.001f), dark);
            Ui.Quad(mascot, "Glint", panelMaterial, 3033).Set(0.02f, 0.02f, 0.01f, new Vector3(side * 0.07f + 0.014f, 0.02f, -0.002f), white);
        }
    }

    void Animate(bool sleeping)
    {
        float t = Time.unscaledTime;
        mascot.localRotation = Quaternion.Euler(0, 0, sleeping ? 10f : 7f * Mathf.Sin(t * 1.3f));   // the head tilts from side to side
        for (int i = 0; i < 2; i++)
        {
            float side = i == 0 ? 1 : -1;
            antennas[i].localRotation = Quaternion.Euler(0, 0, side * 16f + 12f * Mathf.Sin(t * 3.1f + i * 1.3f));
            float blink = sleeping || t % 3.4f < 0.12f ? 0.12f : 1f;                              // a blink now and then; closed asleep
            if (sleeping) antennas[i].localRotation = Quaternion.Euler(0, 0, side * 70f);         // antennas down
            eyes[i].T.localScale = new Vector3(0.075f, 0.075f * blink, 1);
        }
    }

    void Start()
    {
        eye = head.GetComponent<Camera>();
        background = eye.backgroundColor;
        eye.backgroundColor = Calm;
        startedAt = Time.unscaledTime;
        yaw = head.localEulerAngles.y;
        card = new GameObject("Card").transform;
        card.SetParent(transform, false);
        panel = Ui.Quad(card, "Panel", panelMaterial, 3030);
        text = Ui.Text(card, "Text", Ui.TextMaterial(textMaterial, 3031), 64, TextScale, TextAnchor.MiddleCenter, FontStyle.Bold);
        text.transform.localPosition = new Vector3(0, -0.04f, 0);
        BuildMascot();
        wake = Ui.Quad(card, "Wake", panelMaterial, 3030);
        wakeText = Ui.Text(card, "WakeText", Ui.TextMaterial(textMaterial, 3031), 64, TextScale, TextAnchor.MiddleCenter, FontStyle.Bold);
        wakeText.transform.localPosition = WakeAt + new Vector3(0, 0, -0.002f);
        for (int i = 0; i < 2; i++) cursors[i] = new Ui.Cursor(card, panelMaterial);
        card.gameObject.SetActive(false);
    }

    void LateUpdate()
    {
        bool tour = tutorial != null && tutorial.Open;
        if (control.IsFollowing) { if (followingSince < 0) followingSince = Time.unscaledTime; }
        else followingSince = -1f;
        if (!lifted && !tour)
        {
            bool scanOver = panorama.ScanStarted && !panorama.Scanning;
            bool noRobot = !control.IsFollowing && !control.Waking && Time.unscaledTime - startedAt > NoRobotS;
            bool noScan = followingSince >= 0 && Time.unscaledTime - followingSince > NoScanS && !panorama.ScanStarted;
            if (scanOver || noRobot || noScan || control.Sweeping)
            {
                lifted = true;
                Debug.Log($"[curtain] open ({(scanOver ? "scan over" : noRobot ? "no robot" : noScan ? "no scan" : "sweep")})");
            }
        }
        bool asleep = control.Asleep && !control.GoingToSleep && !tour;
        bool raw = tour || !lifted || panorama.Scanning || control.Waking || control.Asleep;   // also the wake-up and lie-down from the menu
        if (raw) rawAt = Time.unscaledTime;
        bool hide = raw || Time.unscaledTime - rawAt < SettleS;
        if (hide != hidden)
        {
            hidden = hide;
            if (hide) floorWas = floor.enabled;
            else
            {
                floor.enabled = floorWas && !panorama.ScanStarted;   // the scanned room replaces the virtual floor
                eye.backgroundColor = background;
                foreach (var r in panorama.GetComponentsInChildren<Renderer>(true)) r.enabled = true;
            }
            window.enabled = !hide;
            if (room != null) room.Muted = hide;
            label.SetVisible(!hide && PlayerPrefs.GetString("hud", "off") == "on");   // as RobotVideo sets it
        }
        // While Reachy gets ready there is nothing to work yet: no menu, notes or subtitles, a plain loading screen.
        // In the tutorial they are there: it shows them.
        menu.Hidden = notes.Hidden = captions.Hidden = bubbles.Hidden = hide && !tour;
        string problem = Problem();
        if (!hide)
        {
            card.gameObject.SetActive(problem != null);
            if (problem != null) Say(problem, true);
            mascot.gameObject.SetActive(false);
            wake.Show(false);
            wakeText.gameObject.SetActive(false);
            onWake[0] = onWake[1] = false;
            foreach (var cur in cursors) cur.Hide();
            return;
        }

        // the panorama keeps saving frames and its mean colour behind the curtain
        foreach (var r in panorama.GetComponentsInChildren<Renderer>(true)) r.enabled = false;
        floor.enabled = false;
        if (eye.backgroundColor != Calm) background = eye.backgroundColor;
        eye.backgroundColor = Calm;

        card.gameObject.SetActive(!tour);
        if (tour) { onWake[0] = onWake[1] = false; return; }
        string dots = new string('.', 1 + (int)(Time.unscaledTime * 1.5f) % 3).PadRight(3, ' ');
        string say = control.GoingToSleep ? (Ui.German ? "Reachy legt sich schlafen" : "Reachy is going to sleep")
            : (Ui.German ? "Reachy macht sich bereit" : "Reachy is getting ready");
        bool button = asleep && problem == null;
        Say(problem ?? (button ? (Ui.German ? "Reachy schläft" : "Reachy is asleep") : $"{say}\n<color=#38bdf8>{dots}</color>"),
            problem != null);
        mascot.gameObject.SetActive(problem == null);
        if (problem == null) Animate(asleep);

        wake.Show(button);
        wakeText.gameObject.SetActive(button);
        for (int hand = 0; hand < 2; hand++)
        {
            bool right = hand == 1;
            var aim = XrInput.Aim(right, head);
            var p = Ui.PointOn(card, aim ?? XrInput.Gaze(head));
            onWake[hand] = button && Ui.Inside(p, WakeAt, WakeW + 0.06f, WakeH + 0.06f);
            if (button && aim != null && Ui.Inside(p, new Vector3(0, -0.05f, 0), 1.2f, 0.9f))
                cursors[hand].Show(p.Value, right ? control.RightLevel : control.LeftLevel);
            else cursors[hand].Hide();
        }
        if (button)
        {
            bool over = onWake[0] || onWake[1];
            wake.Set(WakeW, WakeH, WakeH / 2, WakeAt, new Color(0.22f, 0.74f, 0.97f, over ? 1f : 0.8f));
            wakeText.text = $"<color=#0b1220>{(Ui.German ? "Aufwecken" : "Wake up")}</color>";
        }
    }

    /// <summary>What keeps Reachy from working, in words for the user; null = nothing.</summary>
    string Problem()
    {
        bool de = Ui.German;
        if (string.IsNullOrEmpty(control.video.SignallingUrl))
            return de ? "Diese Brille kennt den Laptop bei Reachy noch nicht.\nBitte einmal am Laptop einrichten."
                      : "This headset does not know the laptop next to Reachy yet.\nPlease set it up once at the laptop.";
        if (control.Restarting) return de ? "Reachy startet neu …" : "Reachy is restarting …";
        if (control.DaemonState == "unreachable")
            return de ? "Keine Verbindung zum Laptop bei Reachy.\nLäuft dort das Meeting-Programm?\nSind beide im gleichen WLAN?"
                      : "No connection to the laptop next to Reachy.\nIs the meeting program running there?\nAre both on the same Wi-Fi?";
        if (control.DaemonState != null && control.DaemonState != "running")
            return (de ? "Reachy meldet ein Problem" : "Reachy reports a problem")
                   + (string.IsNullOrEmpty(control.DaemonError) ? "." : $":\n{control.DaemonError}")
                   + (de ? "\nZeig im Menü auf «Reachy» und pinche, um neu zu starten."
                         : "\nPoint at \"Reachy\" in the menu and pinch to restart.");
        return null;
    }

    /// <summary>boxed: on a panel (a problem, possibly over the picture); else plain text on the calm background.</summary>
    void Say(string say, bool boxed)
    {
        if (say != shown)
        {
            shown = say;
            text.text = say;
            var size = Ui.Size(text);
            panel.Show(boxed);
            panel.Set(size.x + 2 * Pad, size.y + 2 * Pad, 0.05f, Vector3.zero, new Color(0.06f, 0.09f, 0.16f, 0.95f));
            text.transform.localPosition = new Vector3(0, boxed ? 0 : -0.04f, -0.002f);
        }
        // in front of you, yaw only, gliding after your gaze
        yaw = Mathf.LerpAngle(yaw, head.localEulerAngles.y, 1 - Mathf.Exp(-Time.unscaledDeltaTime * FollowRate));
        card.localRotation = Quaternion.Euler(0, yaw, 0);
        card.localPosition = head.localPosition + card.localRotation * new Vector3(0, 0, Distance);
    }
}
