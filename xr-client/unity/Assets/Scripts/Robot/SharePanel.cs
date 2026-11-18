// The shared screen of the laptop next to Reachy, on a panel in the room: there only while someone shares
// (ScreenShare.Texture), by default 45° left of straight ahead. It moves like
// the notes card: point at the handle under it with either hand, pinch, hold, and move that hand. The place is kept
// relative to straight ahead, also after a restart. − and + beside the handle make it smaller and larger; pulling the
// hand towards you while dragging brings it closer (Ui.Zoom).

using UnityEngine;

public class SharePanel : MonoBehaviour
{
    const string YawKey = "share_yaw", UpKey = "share_up";
    const float DefaultYaw = -45f, DefaultUp = 0f;   // degrees right of / above straight ahead
    const float Distance = 1.7f;                      // m from the eye
    const float Width = 1.3f, Frame = 0.02f;
    const float HandleW = 0.2f, HandleH = 0.022f, HandleGap = 0.04f;
    const float DragGain = 2.5f, MaxUp = 40f;
    static readonly Color Drag = new Color(0.22f, 0.74f, 0.97f);

    public RobotControl control;
    public Transform head;
    public ScreenShare share;
    public Material panelMaterial, screenMaterial;
    /// <summary>Not there at all (Curtain).</summary>
    public bool Hidden;

    Transform card;
    Ui.Rect frame, handle;
    Transform picture;
    Material pictureMaterial;
    readonly bool[] onHandle = new bool[2];   // per hand: left, right
    readonly Ui.Cursor[] cursors = new Ui.Cursor[2];
    Ui.Zoom zoom;
    float yaw, up, h = Width * 9 / 16;
    bool dragging, dragRight, shown;
    float startYaw, startUp;
    Vector2? handStart;

    /// <summary>That hand points at the handle: its pinch goes here (RobotControl).</summary>
    public bool Hovered(bool right) => onHandle[right ? 1 : 0] || zoom.Hovered(right ? 1 : 0);

    void Awake()
    {
        yaw = PlayerPrefs.GetFloat(YawKey, DefaultYaw);
        up = PlayerPrefs.GetFloat(UpKey, DefaultUp);
        card = new GameObject("Card").transform;
        card.SetParent(transform, false);
        frame = Ui.Quad(card, "Frame", panelMaterial, 3018);
        handle = Ui.Quad(card, "Handle", panelMaterial, 3019);
        zoom = new Ui.Zoom(card, panelMaterial, 3019, "share", Distance);
        var go = GameObject.CreatePrimitive(PrimitiveType.Quad);
        go.name = "Picture";
        Destroy(go.GetComponent<Collider>());
        picture = go.transform;
        picture.SetParent(card, false);
        pictureMaterial = new Material(screenMaterial) { renderQueue = 3019 };
        go.GetComponent<Renderer>().sharedMaterial = pictureMaterial;
        for (int i = 0; i < 2; i++) cursors[i] = new Ui.Cursor(card, panelMaterial);
        card.localScale = Vector3.zero;
    }

    static Quaternion ToUnity(Quat q) => new Quaternion(-(float)q.X, -(float)q.Y, (float)q.Z, (float)q.W);

    float FrameYaw => ToUnity(control.Frame.ToWorld(Quat.Identity)).eulerAngles.y;

    void Update()
    {
        var tex = share != null ? share.Texture : null;
        bool show = tex != null && !Hidden;
        if (show != shown) Debug.Log($"[screen] panel {((shown = show) ? "shown" : "hidden")}");
        card.localScale = show ? Vector3.one * zoom.Size : Vector3.zero;
        if (!show)
        {
            dragging = false;
            for (int i = 0; i < 2; i++) { onHandle[i] = false; zoom.Point(i, null, Vector3.zero); cursors[i].Hide(); }
            return;
        }
        if (pictureMaterial.mainTexture != tex) pictureMaterial.mainTexture = tex;
        h = Width * tex.height / Mathf.Max(1, tex.width);

        if (dragging)
        {
            if (dragRight ? control.RightHeld : control.LeftHeld)
            {
                var hand = HandAngles(dragRight);
                zoom.Drag(Ui.Zoom.Reach(dragRight, head));
                if (handStart == null && hand != null) { handStart = hand; startYaw = yaw; startUp = up; }
                if (handStart is Vector2 h0 && hand is Vector2 h1)
                {
                    yaw = startYaw + DragGain * Mathf.DeltaAngle(h0.x, h1.x);
                    up = Mathf.Clamp(startUp + DragGain * (h1.y - h0.y), -MaxUp, MaxUp);
                }
            }
            else
            {
                dragging = false;
                zoom.EndDrag();
                PlayerPrefs.SetFloat(YawKey, yaw);
                PlayerPrefs.SetFloat(UpKey, up);
                PlayerPrefs.Save();
            }
        }
        // follows straight ahead (recenter) by heading only, so it stays upright
        var dir = Quaternion.Euler(-up, FrameYaw + yaw, 0) * Vector3.forward;
        card.localPosition = head.localPosition + dir * zoom.Dist;
        card.localRotation = Quaternion.LookRotation(dir, Vector3.up);

        frame.Set(Width + 2 * Frame, h + 2 * Frame, 0.03f, Vector3.zero, Ui.Glass);
        frame.Border(Ui.Edge);
        picture.localScale = new Vector3(Width, h, 1);
        picture.localPosition = new Vector3(0, 0, -0.001f);
        pictureMaterial.SetVector("_Rect", new Vector4(Width, h, 0.015f, 0));

        var handleAt = new Vector3(0, -h / 2 - Frame - HandleGap, -0.001f);
        for (int hand = 0; hand < 2; hand++)
        {
            bool right = hand == 1;
            var aim = XrInput.Aim(right, head);
            var p = Ui.Pick(card, aim, head, q => Ui.Inside(q, handleAt, HandleW + 0.12f, 0.09f) || zoom.On(q), out var byHand);
            zoom.Point(hand, dragging ? null : p, handleAt);
            onHandle[hand] = !dragging && Ui.Inside(p, handleAt, HandleW + 0.12f, 0.09f);
            if (aim != null && (onHandle[hand] || zoom.Hovered(hand))) cursors[hand].Show(byHand.Value, right ? control.RightLevel : control.LeftLevel);
            else cursors[hand].Hide();
        }
        zoom.Draw();
        handle.Set(HandleW, HandleH, HandleH / 2, handleAt,
            dragging ? Drag : new Color(1, 1, 1, onHandle[0] || onHandle[1] ? 0.95f : 0.35f));
    }

    /// <summary>Pinch (either hand) on the handle: drag while held.</summary>
    public void Press(bool right)
    {
        if (zoom.Hovered(right ? 1 : 0)) { zoom.Press(right ? 1 : 0); return; }
        if (!onHandle[right ? 1 : 0]) return;
        dragging = true;
        dragRight = right;
        startYaw = yaw;
        startUp = up;
        handStart = HandAngles(right);
    }

    /// <summary>Direction from the eye to that hand's pinch, degrees in the tracking space; null without hand tracking.</summary>
    Vector2? HandAngles(bool right)
    {
        if (!(XrInput.PinchPoint(!right, head) is Vector3 p)) return null;
        var d = head.parent.InverseTransformDirection(p - head.position).normalized;
        return new Vector2(Mathf.Atan2(d.x, d.z) * Mathf.Rad2Deg, Mathf.Asin(Mathf.Clamp(d.y, -1, 1)) * Mathf.Rad2Deg);
    }
}
