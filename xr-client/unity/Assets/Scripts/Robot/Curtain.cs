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
    float startedAt, followingSince = -1f, yaw;
    Transform card;
    Ui.Rect panel;
    TextMesh text;
    string shown;

    void Start()
    {
        eye = head.GetComponent<Camera>();
        background = eye.backgroundColor;
        startedAt = Time.unscaledTime;
        yaw = head.localEulerAngles.y;
        card = new GameObject("Card").transform;
        card.SetParent(transform, false);
        panel = Ui.Quad(card, "Panel", panelMaterial, 3030);
        text = Ui.Text(card, "Text", Ui.TextMaterial(textMaterial, 3031), 56, TextScale);
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
        bool hide = tour || !lifted || panorama.Scanning || control.Waking;   // also the wake-up and lie-down from the menu
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
            return;
        }

        // the panorama keeps saving frames and its mean colour behind the curtain
        foreach (var r in panorama.GetComponentsInChildren<Renderer>(true)) r.enabled = false;
        floor.enabled = false;
        if (eye.backgroundColor != Calm) background = eye.backgroundColor;
        eye.backgroundColor = Calm;

        card.gameObject.SetActive(!tour);
        if (tour) return;
        string dots = new string('.', 1 + (int)(Time.unscaledTime * 1.5f) % 3).PadRight(3, ' ');
        string say = control.GoingToSleep ? (Ui.German ? "Reachy legt sich schlafen" : "Reachy is going to sleep")
            : (Ui.German ? "Reachy macht sich bereit" : "Reachy is getting ready");
        Say(problem ?? $"{say}\n<color=#38bdf8>{dots}</color>", problem != null);
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
            text.transform.localPosition = new Vector3(0, 0, -0.002f);
        }
        // in front of you, yaw only, gliding after your gaze
        yaw = Mathf.LerpAngle(yaw, head.localEulerAngles.y, 1 - Mathf.Exp(-Time.unscaledDeltaTime * FollowRate));
        card.localRotation = Quaternion.Euler(0, yaw, 0);
        card.localPosition = head.localPosition + card.localRotation * new Vector3(0, 0, Distance);
    }
}
