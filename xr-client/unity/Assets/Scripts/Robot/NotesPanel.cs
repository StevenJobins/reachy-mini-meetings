// Live meeting notes from the backend (message "summary": summary bullets, action items, next steps; Gemini, in the
// bubble language, see backend/README.md "Meeting notes"). A card in the room, by default 45° right of straight ahead,
// like xr-client/pages/notes.js: look there to read it. It moves like a window in the headset: look at the handle
// under it, hold the left pinch and look where it should go (RobotControl). The place is kept relative to straight
// ahead, also after a restart. When new notes arrive, QuickMenu says so for a moment.

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
    }

    const string YawKey = "notes_yaw", UpKey = "notes_up";
    const float DefaultYaw = 45f, DefaultUp = -3.5f;   // degrees right of / above straight ahead
    const float Distance = 1.7f;                        // m from the eye
    const float Width = 0.95f, Pad = 0.06f, StripW = 0.012f;
    const float TextScale = 0.7f;
    const int Chars = 46, ItemLines = 3, MaxItems = 5;
    const float HandleW = 0.2f, HandleH = 0.022f, HandleGap = 0.04f;
    static readonly Color Panel = new Color(0.07f, 0.08f, 0.11f, 0.93f);
    static readonly Color Accent = new Color(1f, 0.58f, 0f);   // #ff9500
    static readonly Color Drag = new Color(0.22f, 0.74f, 0.97f);

    public RobotControl control;
    public Transform head;
    public QuickMenu menu;
    public Material textMaterial, panelMaterial;

    Transform card;
    Ui.Rect panel, strip, handle;
    TextMesh body;
    SummaryMsg notes;
    float yaw, up, h;
    bool handleGazed, dragging;

    /// <summary>The card, for the tutorial ("look to your right").</summary>
    public Transform Card => card;
    public bool HandleGazed => handleGazed;

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
        body = Ui.Text(card, "Text", text, 48, TextScale, TextAnchor.UpperLeft);
        Redraw();
    }

    static Quaternion ToUnity(Quat q) => new Quaternion(-(float)q.X, -(float)q.Y, (float)q.Z, (float)q.W);

    float FrameYaw => ToUnity(control.Frame.ToWorld(Quat.Identity)).eulerAngles.y;

    void Update()
    {
        if (dragging)
        {
            if (control.LeftHeld)
            {
                // the card hangs on the gaze, same distance
                var f = head.localRotation * Vector3.forward;
                yaw = Mathf.DeltaAngle(0, Mathf.Atan2(f.x, f.z) * Mathf.Rad2Deg - FrameYaw);
                up = Mathf.Asin(Mathf.Clamp(f.y, -1, 1)) * Mathf.Rad2Deg;
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

        var p = Ui.GazeOn(card, head);
        var handleAt = new Vector3(0, -h / 2 - HandleGap, -0.001f);
        handleGazed = !dragging && Ui.Inside(p, handleAt, HandleW + 0.12f, 0.09f);
        handle.Set(HandleW, HandleH, HandleH / 2, handleAt,
            dragging ? Drag : new Color(1, 1, 1, handleGazed ? 0.95f : 0.35f));
    }

    /// <summary>Left pinch on the handle (RobotControl); the drag lasts while the pinch is held.</summary>
    public void BeginDrag()
    {
        dragging = true;
        Debug.Log("[notes] drag");
    }

    /// <summary>New notes from the backend (CaptionView).</summary>
    public void Show(SummaryMsg msg)
    {
        notes = msg;
        Redraw();
        if (menu != null) menu.Flash(Ui.German ? "Meeting-Notizen\naktualisiert  →" : "Meeting notes\nupdated  →");
        Debug.Log($"[notes] {msg.summary?.Length ?? 0} bullets, {msg.actions?.Length ?? 0} actions, {msg.next_steps?.Length ?? 0} next steps");
    }

    void Redraw()
    {
        bool de = Ui.German;
        var s = new StringBuilder();
        s.Append($"<size=60><b>{(de ? "Meeting-Notizen" : "Meeting notes")}</b></size>\n");
        if (notes == null)
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
        float w = Mathf.Max(Width, size.x + 2 * Pad + StripW);
        h = size.y + 2 * Pad;
        panel.Set(w, h, 0.04f, Vector3.zero, Panel);
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
