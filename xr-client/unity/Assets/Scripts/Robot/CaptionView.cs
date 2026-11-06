// Subtitles: the backend's live captions and your own words, on a panel low in front of you. Protocol at the top of
// backend/.../captions.py; what is shown when: Subtitles.cs.
//
// Each line starts with who said it: "Speaker A", "B", ... in its own colour, from the voice (backend speakers.py),
// or "You" (push to talk: the backend transcribes the headset mic). Italic text is still changing; with translation
// the translation is the main text and the original stands small underneath.
// The panel stays where it is while you look around and glides after you once you look clearly elsewhere, like the
// video window when it stands in front of you: text fixed to the head is uncomfortable.
// Bubbles above the speaker's head (faces + mic direction) were tried first: git 130ee11.

using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using UnityEngine;

public class CaptionView : MonoBehaviour
{
    [Serializable] class TypeMsg { public string type; }
    [Serializable] class HelloMsg { public double t; }
    [Serializable]
    class CaptionMsg
    {
        public int id;
        public bool final;
        public string text, lang, translation;
        public int speaker;
        public double t_end;
    }
    [Serializable] class MeMsg { public string text; }

    const float Distance = 2f, PitchDeg = 20f;    // below eye height, over the lower part of the video
    const float GlideAwayDeg = 25f, GlideAfterS = 0.5f, GlideS = 0.25f;
    const int RowChars = 56, MaxRows = 2;          // per line, keeping the end: that is what is being said now
    const float Pad = 0.04f, TextScale = 0.85f, MinWidth = 0.6f;
    const float FadeS = 0.3f;
    static readonly string[] Colors = { "#ff9500", "#38bdf8", "#4ade80", "#f472b6", "#a78bfa", "#facc15" };
    const string YouColor = "#ffffff";

    public RoomSound room;
    public RobotControl control;
    public VideoWindow view;
    public Transform head;
    public Material textMaterial, panelMaterial;

    readonly Subtitles subs = new Subtitles();
    Font font;
    Material text, panel;
    Transform root, panelT;
    TextMesh mesh;
    MeshRenderer meshRenderer;
    string shownText;
    double clockOffsetS;
    float frontYaw, awaySince = -1f, alpha;
    bool glide, haveFront;

    public string Status { get; private set; } = "";

    void Awake()
    {
        font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
        text = new Material(textMaterial) { renderQueue = 3013 };
        UpdateAtlas(font);
        Font.textureRebuilt += UpdateAtlas;

        root = new GameObject("Subtitles").transform;
        root.SetParent(view.transform.parent, false);
        var quad = GameObject.CreatePrimitive(PrimitiveType.Quad);
        quad.name = "Panel";
        Destroy(quad.GetComponent<Collider>());
        panelT = quad.transform;
        panelT.SetParent(root, false);
        panel = new Material(panelMaterial) { renderQueue = 3012 };
        quad.GetComponent<Renderer>().sharedMaterial = panel;
        var t = new GameObject("Text");
        t.transform.SetParent(root, false);
        t.transform.localScale = Vector3.one * TextScale;
        mesh = t.AddComponent<TextMesh>();
        mesh.font = font;
        mesh.fontSize = 64;
        mesh.characterSize = 0.01f;
        mesh.anchor = TextAnchor.LowerCenter;
        mesh.alignment = TextAlignment.Left;
        mesh.richText = true;
        meshRenderer = t.GetComponent<MeshRenderer>();
        meshRenderer.sharedMaterial = text;
        root.gameObject.SetActive(false);
    }

    void OnDestroy() => Font.textureRebuilt -= UpdateAtlas;

    void UpdateAtlas(Font f)
    {
        if (f == font) text.mainTexture = font.material.mainTexture;
    }

    static double WallS() => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0;

    void Update()
    {
        double now = Time.unscaledTimeAsDouble;
        var backend = room.Backend;
        if (backend != null)
            while (backend.TryGetMessage(out var json)) Handle(json, now);
        subs.Mic(control.MicOn, now);

        var lines = subs.Shown(now);
        var body = Compose(lines);
        if (body != shownText) Redraw(body);
        alpha = Mathf.MoveTowards(alpha, lines.Count > 0 ? 1 : 0, Time.unscaledDeltaTime / FadeS);
        root.gameObject.SetActive(alpha > 0);
        panel.color = new Color(0, 0, 0, 0.6f * alpha);
        meshRenderer.enabled = alpha > 0.5f;
        Place();
        Status = backend == null ? "" : $"subtitles {lines.Count(l => !l.Live)}";
    }

    void Handle(string json, double now)
    {
        switch (JsonUtility.FromJson<TypeMsg>(json).type)
        {
            case "hello":
                clockOffsetS = WallS() - JsonUtility.FromJson<HelloMsg>(json).t;
                break;
            case "caption":
                var c = JsonUtility.FromJson<CaptionMsg>(json);
                if (c.final && c.t_end < WallS() - clockOffsetS - Subtitles.FinalS) return;   // replayed history
                int? speaker = json.Contains("\"speaker\": ") && !json.Contains("\"speaker\": null") ? c.speaker : (int?)null;
                subs.Caption(c.id, c.final, c.text, c.translation, c.lang, speaker, now);
                Debug.Log($"[captions] {(c.final ? "final" : "partial")} #{c.id} speaker {speaker?.ToString() ?? "?"} {c.lang}: {c.text}");
                break;
            case "me":
                var m = JsonUtility.FromJson<MeMsg>(json);
                subs.Me(m.text, now);
                Debug.Log($"[captions] you: {m.text}");
                break;
        }
    }

    /// <summary>Word wrap; keeps the end, that is what is being said now.</summary>
    static List<string> Wrap(string s, int width, int maxLines)
    {
        var lines = new List<string>();
        var line = new StringBuilder();
        foreach (var word in s.Split(' ', StringSplitOptions.RemoveEmptyEntries))
        {
            if (line.Length > 0 && line.Length + 1 + word.Length > width) { lines.Add(line.ToString()); line.Clear(); }
            if (line.Length > 0) line.Append(' ');
            line.Append(word);
        }
        if (line.Length > 0) lines.Add(line.ToString());
        return lines.Skip(Math.Max(0, lines.Count - maxLines)).ToList();
    }

    static string Escape(string s) => s.Replace('<', '‹').Replace('>', '›');

    static string Who(SubtitleLine l, out string color)
    {
        color = l.You ? YouColor : l.Speaker != null ? Colors[(l.Speaker.Value - 1) % Colors.Length] : "#9ca3af";
        return l.You ? "You" : l.Speaker != null ? $"Speaker {(char)('A' + (l.Speaker.Value - 1) % 26)}" : "";
    }

    static string Compose(List<SubtitleLine> lines)
    {
        var body = new StringBuilder();
        foreach (var l in lines)
        {
            if (body.Length > 0) body.Append('\n');
            var who = Who(l, out var color);
            var name = who.Length > 0 ? $"<color={color}><b>{who}</b></color>  " : "";
            if (l.Live) { body.Append($"{name}<color=#9ca3af>● …</color>"); continue; }
            var rows = Wrap(Escape(l.Text), RowChars - who.Length - 2, MaxRows);   // the name takes room in the first row
            var textColor = l.Final ? "#ffffff" : "#d1d5db";
            body.Append(name);
            body.Append(string.Join("\n", rows.Select(row =>
                l.Final ? $"<color={textColor}>{row}</color>" : $"<color={textColor}><i>{row}</i></color>")));
            if (l.Original != null)
                foreach (var row in Wrap(Escape(l.Original), RowChars * 3 / 2, 1))
                    body.Append($"\n<size=40><color=#9ca3af>{row}</color></size>");
        }
        return body.ToString();
    }

    void Redraw(string body)
    {
        shownText = body;
        mesh.text = body;
        var size = meshRenderer.localBounds.size * TextScale;
        float w = Mathf.Max(MinWidth, size.x + 2 * Pad), h = size.y + 2 * Pad;
        panelT.localScale = new Vector3(w, h, 1);
        panelT.localPosition = new Vector3(0, h / 2, 0);
        panel.SetVector("_Rect", new Vector4(w, h, 0.04f, 0));
        mesh.transform.localPosition = new Vector3(0, Pad, -0.002f);
    }

    /// <summary>Below your gaze, yaw only; glides after you once you look away for a moment (VideoWindow.Front).</summary>
    void Place()
    {
        float yaw = head.localEulerAngles.y;
        if (!haveFront) { frontYaw = yaw; haveFront = true; }
        float diff = Mathf.DeltaAngle(frontYaw, yaw);
        if (Mathf.Abs(diff) > GlideAwayDeg)
        {
            if (awaySince < 0) awaySince = Time.unscaledTime;
            if (Time.unscaledTime - awaySince > GlideAfterS) glide = true;
        }
        else
        {
            awaySince = -1f;
            if (Mathf.Abs(diff) < 2f) glide = false;
        }
        if (glide) frontYaw += diff * (1 - Mathf.Exp(-Time.unscaledDeltaTime / GlideS));
        root.localRotation = Quaternion.Euler(PitchDeg, frontYaw, 0);
        root.localPosition = head.localPosition + root.localRotation * new Vector3(0, 0, Distance);
    }
}
