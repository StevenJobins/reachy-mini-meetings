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

    public RobotControl control;
    public Tutorial tutorial;
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
        bool hide = tour || !lifted || panorama.Scanning;
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
            label.SetVisible(!hide && PlayerPrefs.GetString("hud", "on") != "off");   // as RobotVideo sets it
        }
        if (!hide) { card.gameObject.SetActive(false); return; }

        // the panorama keeps saving frames and its mean colour behind the curtain
        foreach (var r in panorama.GetComponentsInChildren<Renderer>(true)) r.enabled = false;
        floor.enabled = false;
        if (eye.backgroundColor != Color.black) background = eye.backgroundColor;
        eye.backgroundColor = Color.black;

        card.gameObject.SetActive(!tour);
        if (tour) return;
        var (done, total) = panorama.ScanProgress;
        string say = panorama.Scanning
            ? (Ui.German ? $"Reachy schaut sich im Raum um … {done + 1}/{total}" : $"Reachy is looking around the room … {done + 1}/{total}")
            : control.Waking ? (Ui.German ? "Reachy wacht auf …" : "Reachy is waking up …")
            : control.IsFollowing ? (Ui.German ? "Gleich geht es los …" : "Starting in a moment …")
            : (Ui.German ? "Verbinde mit Reachy …" : "Connecting to Reachy …");
        if (say != shown)
        {
            shown = say;
            text.text = say;
            var size = Ui.Size(text);
            panel.Set(size.x + 2 * Pad, size.y + 2 * Pad, 0.05f, Vector3.zero, new Color(0.06f, 0.09f, 0.16f, 0.95f));
            text.transform.localPosition = new Vector3(0, 0, -0.002f);
        }
        // in front of you, yaw only, gliding after your gaze
        yaw = Mathf.LerpAngle(yaw, head.localEulerAngles.y, 1 - Mathf.Exp(-Time.unscaledDeltaTime * FollowRate));
        card.localRotation = Quaternion.Euler(0, yaw, 0);
        card.localPosition = head.localPosition + card.localRotation * new Vector3(0, 0, Distance);
    }
}
