// Live meeting notes from the backend (message "summary": summary bullets, action items, next steps; Gemini, in the
// bubble language, see backend/README.md "Meeting notes"). A card in the room, by default 45° right of straight ahead,
// like xr-client/pages/notes.js: look there to read it. It moves like a window in the headset: point at the handle
// under it with either hand (a dot and a closing ring show where, as in the headset's menus), pinch, hold, and move
// that hand: the card goes along, a hand movement of 10° moves it
// 25° around you. Only the hand moves it, not the head (it needs hand tracking). The place is kept relative to
// straight ahead, also after a restart. When new notes arrive, QuickMenu says so for a moment.
// New meeting: the button top right (look + pinch left, then once more to confirm) asks the backend to drop the
// transcript; it also does that by itself after 20 min without speech. Either way empty notes arrive, the card clears.

using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using UnityEngine;

public class NotesPanel : MonoBehaviour
{
    [Serializable] public class ActionMsg { public string who, what, when; }
    [Serializable]
    public class SummaryMsg
    {
        public string[] summary;
        public ActionMsg[] actions;
        public string[] next_steps;
        public double t;
        public bool new_meeting;
    }

    const string YawKey = "notes_yaw", UpKey = "notes_up", FoldKey = "notes_folded";
    const float DefaultYaw = 45f, DefaultUp = -3.5f;   // degrees right of / above straight ahead
    const float Distance = 1.7f;                        // m from the eye
    const float Width = 0.95f, Pad = 0.06f, StripW = 0.012f;
    const float TextScale = 0.7f;
    const int Chars = 46, ItemLines = 3, MaxItems = 5;
    const float HandleW = 0.2f, HandleH = 0.022f, HandleGap = 0.04f;
    const float DragGain = 2.5f, MaxUp = 40f;
    const float ConfirmS = 3f;
    static readonly Color Panel = new Color(0.07f, 0.08f, 0.11f, 0.93f);
    static readonly Color Accent = new Color(1f, 0.58f, 0f);   // #ff9500
    static readonly Color Drag = new Color(0.22f, 0.74f, 0.97f);

    public RobotControl control;
    public Transform head;
    public QuickMenu menu;
    public RoomSound room;
    public Material textMaterial, panelMaterial;
    /// <summary>Not there at all (Curtain).</summary>
    public bool Hidden;

    Transform card;
    Ui.Rect panel, strip, handle, button, fold;
    TextMesh body, buttonText, foldText;
    bool folded;   // only the title: the notes take a lot of room
    readonly bool[] onFold = new bool[2];
    float armedAt = -10f;   // first pinch on New meeting; the second within ConfirmS clears
    float w;
    SummaryMsg notes;
    float yaw, up, h;
    readonly bool[] onHandle = new bool[2], onButton = new bool[2];   // per hand: left, right
    readonly Ui.Cursor[] cursors = new Ui.Cursor[2];
    bool dragging;
    float startYaw, startUp;
    Vector2? handStart;   // hand direction (yaw, up) when the drag began; null until the hand is tracked
    bool dragRight;
    // Opening the app is a new meeting: the notes of the last one are cleared, unless the app was only away for a
    // moment (it closes itself when the headset is taken off, AppLifecycle)
    const string SeenKey = "notes_seen";
    const long ResumeS = 300;
    const float SeenEveryS = 30f;
    bool askedNew;
    float seenAt = -100f;

    /// <summary>The card, for the tutorial ("look to your right").</summary>
    public Transform Card => card;
    /// <summary>That hand points at the handle or the button: its pinch goes here (RobotControl).</summary>
    public bool Hovered(bool right) => onHandle[right ? 1 : 0] || onButton[right ? 1 : 0] || onFold[right ? 1 : 0];

    void Awake()
    {
        yaw = PlayerPrefs.GetFloat(YawKey, DefaultYaw);
        up = PlayerPrefs.GetFloat(UpKey, DefaultUp);
        var text = Ui.TextMaterial(textMaterial, 3017);
        card = new GameObject("Card").transform;
        card.SetParent(transform, false);
        panel = Ui.Quad(card, "Panel", panelMaterial, 3015);
        strip = Ui.Quad(card, "Strip", panelMaterial, 3016);
        handle = Ui.Quad(card, "Handle", panelMaterial, 3016);
        button = Ui.Quad(card, "NewMeeting", panelMaterial, 3016);
        buttonText = Ui.Text(card, "NewMeetingText", text, 36, TextScale);
        fold = Ui.Quad(card, "Fold", panelMaterial, 3016);
        foldText = Ui.Text(card, "FoldText", text, 44, TextScale, TextAnchor.MiddleCenter, FontStyle.Bold);
        folded = PlayerPrefs.GetInt(FoldKey, 0) == 1;
        for (int i = 0; i < 2; i++) cursors[i] = new Ui.Cursor(card, panelMaterial);
        body = Ui.Text(card, "Text", text, 48, TextScale, TextAnchor.UpperLeft);
        Redraw();
    }

    static Quaternion ToUnity(Quat q) => new Quaternion(-(float)q.X, -(float)q.Y, (float)q.Z, (float)q.W);

    float FrameYaw => ToUnity(control.Frame.ToWorld(Quat.Identity)).eulerAngles.y;

    void Update()
    {
        long unix = System.DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        var backend = room != null ? room.Backend : null;
        if (!askedNew && backend != null && backend.Connected)
        {
            askedNew = true;
            long.TryParse(PlayerPrefs.GetString(SeenKey, "0"), out long last);
            if (unix - last > ResumeS)
            {
                backend.Send("{\"type\": \"new_meeting\"}");
                Debug.Log("[notes] app opened: new meeting");
            }
        }
        if (Time.unscaledTime - seenAt > SeenEveryS)
        {
            seenAt = Time.unscaledTime;
            PlayerPrefs.SetString(SeenKey, unix.ToString());
            PlayerPrefs.Save();
        }
        card.localScale = Hidden ? Vector3.zero : Vector3.one;
        if (Hidden)
        {
            for (int i = 0; i < 2; i++) { onHandle[i] = onButton[i] = onFold[i] = false; cursors[i].Hide(); }
            return;
        }
        if (dragging)
        {
            if (dragRight ? control.RightHeld : control.LeftHeld)
            {
                var hand = HandAngles(dragRight);
                if (handStart == null && hand != null) { handStart = hand; startYaw = yaw; startUp = up; }
                if (handStart is Vector2 h0 && hand is Vector2 h1)
                {
                    // the card goes along with the hand, around you, at the same distance
                    yaw = startYaw + DragGain * Mathf.DeltaAngle(h0.x, h1.x);
                    up = Mathf.Clamp(startUp + DragGain * (h1.y - h0.y), -MaxUp, MaxUp);
                }
            }
            else
            {
                dragging = false;
                PlayerPrefs.SetFloat(YawKey, yaw);
                PlayerPrefs.SetFloat(UpKey, up);
                PlayerPrefs.Save();
                Debug.Log($"[notes] moved to {yaw:0}° right, {up:0}° up");
            }
        }
        // follows straight ahead (recenter) by heading only, so it stays upright
        var dir = Quaternion.Euler(-up, FrameYaw + yaw, 0) * Vector3.forward;
        card.localPosition = head.localPosition + dir * Distance;
        card.localRotation = Quaternion.LookRotation(dir, Vector3.up);

        bool armed = Time.unscaledTime - armedAt < ConfirmS;
        bool de = Ui.German;
        buttonText.text = armed ? (de ? "Wirklich? Nochmals pinchen" : "Sure? Pinch again") : (de ? "Neues Meeting" : "New meeting");
        var bs = Ui.Size(buttonText);
        // top right: fold / unfold, and left of it New meeting (not while folded)
        var foldAt = new Vector3(w / 2 - Pad - 0.01f, h / 2 - Pad - 0.025f, -0.003f);
        var buttonAt = foldAt + new Vector3(-0.07f - bs.x / 2, 0, 0);
        foldText.text = folded ? "+" : "–";
        button.Show(!folded);
        buttonText.gameObject.SetActive(!folded);
        var handleAt = new Vector3(0, -h / 2 - HandleGap, -0.001f);
        for (int hand = 0; hand < 2; hand++)
        {
            bool right = hand == 1;
            var aim = XrInput.Aim(right, head);
            var p = Ui.Pick(card, aim, head, q => Ui.Inside(q, handleAt, HandleW + 0.12f, 0.09f)
                || !folded && Ui.Inside(q, buttonAt, bs.x + 0.06f, 0.08f) || Ui.Inside(q, foldAt, 0.09f, 0.09f), out var byHand);
            onHandle[hand] = !dragging && Ui.Inside(p, handleAt, HandleW + 0.12f, 0.09f);
            onButton[hand] = !dragging && !folded && Ui.Inside(p, buttonAt, bs.x + 0.06f, 0.08f);
            onFold[hand] = !dragging && Ui.Inside(p, foldAt, 0.09f, 0.09f);
            bool onCard = byHand is Vector3 hq && Ui.Inside(hq, new Vector3(0, -HandleGap / 2, 0), w + 0.1f, h + 2 * HandleGap + 0.1f);
            if (aim != null && onCard) cursors[hand].Show(byHand.Value, right ? control.RightLevel : control.LeftLevel);
            else cursors[hand].Hide();
        }
        bool overHandle = onHandle[0] || onHandle[1], overButton = onButton[0] || onButton[1];
        handle.Set(HandleW, HandleH, HandleH / 2, handleAt,
            dragging ? Drag : new Color(1, 1, 1, overHandle ? 0.95f : 0.35f));
        button.Set(bs.x + 0.04f, 0.055f, 0.0275f, buttonAt + new Vector3(0, 0, 0.001f),
            armed ? new Color(0.94f, 0.27f, 0.27f, 0.9f) : new Color(1, 1, 1, overButton ? 0.3f : 0.1f));
        buttonText.transform.localPosition = buttonAt;
        fold.Set(0.06f, 0.055f, 0.0275f, foldAt + new Vector3(0, 0, 0.001f), new Color(1, 1, 1, onFold[0] || onFold[1] ? 0.3f : 0.1f));
        foldText.transform.localPosition = foldAt;
    }

    /// <summary>Pinch (either hand) on the handle (drag while held) or on New meeting (twice).</summary>
    public void Press(bool right)
    {
        int hand = right ? 1 : 0;
        if (onHandle[hand]) { BeginDrag(right); return; }
        if (onFold[hand])
        {
            folded = !folded;
            PlayerPrefs.SetInt(FoldKey, folded ? 1 : 0);
            PlayerPrefs.Save();
            Redraw();
            return;
        }
        if (!onButton[hand]) return;
        if (Time.unscaledTime - armedAt < ConfirmS)
        {
            armedAt = -10f;
            room?.Backend?.Send("{\"type\": \"new_meeting\"}");
            Debug.Log("[notes] new meeting asked");
        }
        else armedAt = Time.unscaledTime;
    }

    /// <summary>The drag lasts while that hand's pinch is held.</summary>
    void BeginDrag(bool right)
    {
        dragging = true;
        dragRight = right;
        startYaw = yaw;
        startUp = up;
        handStart = HandAngles(right);
        Debug.Log($"[notes] drag with the {(right ? "right" : "left")} hand{(handStart == null ? ", not tracked yet" : "")}");
    }

    /// <summary>Direction from the eye to that hand's pinch, degrees in the tracking space; null without hand tracking.</summary>
    Vector2? HandAngles(bool right)
    {
        if (!(XrInput.PinchPoint(!right, head) is Vector3 p)) return null;
        var d = head.parent.InverseTransformDirection(p - head.position).normalized;
        return new Vector2(Mathf.Atan2(d.x, d.z) * Mathf.Rad2Deg, Mathf.Asin(Mathf.Clamp(d.y, -1, 1)) * Mathf.Rad2Deg);
    }

    /// <summary>New notes from the backend (CaptionView).</summary>
    public void Show(SummaryMsg msg)
    {
        bool empty = (msg.summary?.Length ?? 0) + (msg.actions?.Length ?? 0) + (msg.next_steps?.Length ?? 0) == 0;
        notes = empty ? null : msg;
        Redraw();
        if (menu != null)
            menu.Flash(empty ? Ui.German ? "Neues Meeting:\nNotizen geleert" : "New meeting:\nnotes cleared"
                             : Ui.German ? "Meeting-Notizen\naktualisiert  →" : "Meeting notes\nupdated  →");
        Debug.Log($"[notes] {msg.summary?.Length ?? 0} bullets, {msg.actions?.Length ?? 0} actions, {msg.next_steps?.Length ?? 0} next steps");
    }

    void Redraw()
    {
        bool de = Ui.German;
        var s = new StringBuilder();
        s.Append($"<size=60><b>{(de ? "Meeting-Notizen" : "Meeting notes")}</b></size>\n");
        if (folded) { }
        else if (notes == null)
        {
            s.Append($"\n<color=#9ca3af>{(de ? "Die Zusammenfassung erscheint nach der ersten Minute Gespräch." : "The summary appears after the first minute of talking.")}</color>");
        }
        else
        {
            Section(s, de ? "Zusammenfassung" : "Summary", "•", notes.summary);
            Section(s, de ? "Aufgaben" : "Action items", "□", notes.actions?.Select(ActionText));
            Section(s, de ? "Nächste Schritte" : "Next steps", "→", notes.next_steps);
            var at = DateTimeOffset.FromUnixTimeMilliseconds((long)(notes.t * 1000)).ToLocalTime();
            s.Append($"\n<size=36><color=#6b7280>{(de ? "aktualisiert" : "updated")} {at:HH:mm}</color></size>");
        }
        body.text = s.ToString();

        var size = Ui.Size(body);
        w = folded ? size.x + 2 * Pad + StripW + 0.12f : Mathf.Max(Width, size.x + 2 * Pad + StripW);
        h = size.y + 2 * Pad;
        panel.Set(w, h, 0.06f, Vector3.zero, Ui.Glass);
        panel.Border(Ui.Edge);
        strip.Set(StripW, h - 0.08f, StripW / 2, new Vector3(-w / 2 + 0.025f, 0, -0.001f), Accent);
        body.transform.localPosition = new Vector3(-w / 2 + Pad + StripW, h / 2 - Pad, -0.002f);
    }

    static string ActionText(ActionMsg a) =>
        (string.IsNullOrEmpty(a.who) ? a.what : $"{a.who}: {a.what}") + (string.IsNullOrEmpty(a.when) ? "" : $" ({a.when})");

    /// <summary>Heading in the accent colour, then the items with a bullet; continuation lines indented.</summary>
    static void Section(StringBuilder s, string title, string bullet, IEnumerable<string> items)
    {
        var list = items?.Where(i => !string.IsNullOrWhiteSpace(i)).Take(MaxItems).ToList();
        if (list == null || list.Count == 0) return;
        s.Append($"\n<color=#ff9500><b>{title}</b></color>\n");
        foreach (var item in list)
        {
            var all = CaptionView.Wrap(CaptionView.Escape(item), Chars, int.MaxValue).ToList();
            var lines = all.Take(ItemLines).ToList();
            if (all.Count > ItemLines) lines[ItemLines - 1] += "…";
            for (int i = 0; i < lines.Count; i++)
                s.Append(i == 0 ? $"<color=#ff9500>{bullet}</color> <color=#e5e7eb>{lines[i]}</color>\n" : $"   <color=#e5e7eb>{lines[i]}</color>\n");
        }
    }
}
