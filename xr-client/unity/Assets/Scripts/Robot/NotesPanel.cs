// Live meeting notes from the backend (message "summary": summary bullets, action items, next steps; Gemini, in the
// bubble language, see backend/README.md "Meeting notes"). A card in the room to the right of "straight ahead", like
// xr-client/pages/notes.js: look right to read it, no button. When new notes arrive, QuickMenu says so for a moment.

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

    const float SideDeg = 45f;           // right of straight ahead
    const float Distance = 1.7f, Drop = 0.1f;   // m from the eye, m below eye height
    const float Width = 0.95f, Pad = 0.06f, StripW = 0.012f;
    const float TextScale = 0.7f;
    const int Chars = 46, ItemLines = 3, MaxItems = 5;
    static readonly Color Panel = new Color(0.07f, 0.08f, 0.11f, 0.93f);
    static readonly Color Accent = new Color(1f, 0.58f, 0f);   // #ff9500

    public RobotControl control;
    public Transform head;
    public QuickMenu menu;
    public Material textMaterial, panelMaterial;

    Transform card, panelT, stripT;
    Material panel, strip, text;
    TextMesh body;
    Renderer bodyR;
    Font font;
    SummaryMsg notes;

    void Awake()
    {
        font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
        text = new Material(textMaterial) { renderQueue = 3017 };
        UpdateAtlas(font);
        Font.textureRebuilt += UpdateAtlas;

        card = new GameObject("Card").transform;
        card.SetParent(transform, false);
        (panelT, panel) = Quad("Panel", 3015);
        (stripT, strip) = Quad("Strip", 3016);
        var go = new GameObject("Text");
        go.transform.SetParent(card, false);
        go.transform.localScale = Vector3.one * TextScale;
        body = go.AddComponent<TextMesh>();
        body.font = font;
        body.fontSize = 48;
        body.characterSize = 0.01f;
        body.anchor = TextAnchor.UpperLeft;
        body.alignment = TextAlignment.Left;
        body.richText = true;
        body.color = Color.white;
        bodyR = go.GetComponent<MeshRenderer>();
        bodyR.sharedMaterial = text;
        Redraw();
    }

    void OnDestroy() => Font.textureRebuilt -= UpdateAtlas;

    void UpdateAtlas(Font f)
    {
        if (f == font) text.mainTexture = font.material.mainTexture;
    }

    (Transform, Material) Quad(string name, int queue)
    {
        var go = GameObject.CreatePrimitive(PrimitiveType.Quad);
        go.name = name;
        Destroy(go.GetComponent<Collider>());
        go.transform.SetParent(card, false);
        var m = new Material(panelMaterial) { renderQueue = queue };
        go.GetComponent<Renderer>().sharedMaterial = m;
        return (go.transform, m);
    }

    static Quaternion ToUnity(Quat q) => new Quaternion(-(float)q.X, -(float)q.Y, (float)q.Z, (float)q.W);

    void Update()
    {
        // follows "straight ahead" (recenter), only its heading: the card stays upright
        float yaw = ToUnity(control.Frame.ToWorld(Quat.Identity)).eulerAngles.y + SideDeg;
        var eye = head.localPosition;
        var dir = Quaternion.Euler(0, yaw, 0) * Vector3.forward;
        card.localPosition = eye + dir * Distance + Vector3.down * Drop;
        card.localRotation = Quaternion.LookRotation(dir, Vector3.up);
    }

    /// <summary>New notes from the backend (CaptionView).</summary>
    public void Show(SummaryMsg msg)
    {
        notes = msg;
        Redraw();
        if (menu != null) menu.Flash(Tutorial.German ? "Meeting-Notizen aktualisiert  →" : "Meeting notes updated  →");
        Debug.Log($"[notes] {msg.summary?.Length ?? 0} bullets, {msg.actions?.Length ?? 0} actions, {msg.next_steps?.Length ?? 0} next steps");
    }

    void Redraw()
    {
        bool de = Tutorial.German;
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

        var size = bodyR.localBounds.size * TextScale;
        float w = Mathf.Max(Width, size.x + 2 * Pad + StripW), h = size.y + 2 * Pad;
        Rect(panel, panelT, w, h, 0.04f, Vector3.zero, Panel);
        Rect(strip, stripT, StripW, h - 0.08f, StripW / 2, new Vector3(-w / 2 + 0.025f, 0, -0.001f), Accent);
        body.transform.localPosition = new Vector3(-w / 2 + Pad + StripW, h / 2 - Pad, -0.002f);
    }

    static void Rect(Material m, Transform t, float w, float h, float r, Vector3 at, Color c)
    {
        t.localScale = new Vector3(w, h, 1);
        t.localPosition = at;
        m.SetVector("_Rect", new Vector4(w, h, r, 0));
        m.color = c;
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
