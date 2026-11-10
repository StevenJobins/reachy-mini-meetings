// Speech bubbles next to the speaker: when the backend knows where the speaker's face is (caption "at", backend
// people.py: the voice tied to a person in the room), the text stands above that person's head and a frame in the
// speaker's colour marks the face. Everything else stays in the subtitle panel (CaptionView), which asks here first.
//
// The bubble hangs on the video window, at the place where that direction is in the picture shown now (robot head
// pose from the video delay ago). While the window is world-locked that is a fixed place in the room, also outside
// the live picture (the room panorama shows the person there); while it stands in front of the user, only people
// inside the picture get a bubble.
// One bubble per person: a new utterance replaces the old one.

using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;

public class SpeakerBubbles : MonoBehaviour
{
    [Serializable]
    public class At
    {
        public int person;
        public float yaw_deg, pitch_deg, top_deg, h_deg, seen_s;
    }

    class Bubble
    {
        public int CaptionId;
        public At At;
        public double Until;
        public string Body;
        public Transform Root;
        public Ui.Rect Panel;
        public Ui.Rect[] Frame;
        public TextMesh Text;
        public Color Color;
    }

    const float Distance = 2.9f;                   // just inside the video sphere (3 m)
    const int RowChars = 34, MaxRows = 3;
    const float Pad = 0.05f, TextScale = 1.2f, Gap = 0.1f, Line = 0.014f, MinFace = 0.2f;

    public VideoWindow view;
    public RobotVideo video;
    public Material textMaterial, panelMaterial;

    readonly Dictionary<int, Bubble> bubbles = new Dictionary<int, Bubble>();   // by person
    readonly CameraModel cam = CameraModel.Calibrated();
    Material text;

    public string Status => bubbles.Count > 0 ? $"bubbles {bubbles.Count}" : "";

    /// <summary>A caption with the speaker's place. True = shown here; false = not possible now (the panel shows it).</summary>
    public bool Show(int id, bool final, string textNow, string translation, string lang, string who, string color,
        At at, double now)
    {
        if (at == null || string.IsNullOrEmpty(textNow) || !TryPlace(at, out _)) { Remove(id); return false; }
        foreach (var other in bubbles.Where(kv => kv.Value.CaptionId == id && kv.Key != at.person).ToList())
            Drop(other.Key);   // the same words, now known to be someone else's
        if (!bubbles.TryGetValue(at.person, out var b)) b = bubbles[at.person] = Create();
        b.CaptionId = id;
        b.At = at;
        b.Until = now + (final ? Subtitles.FinalS : Subtitles.PartialS);
        ColorUtility.TryParseHtmlString(color, out b.Color);

        bool translated = !string.IsNullOrEmpty(translation);
        var rows = CaptionView.Wrap(CaptionView.Escape(translated ? translation : textNow), RowChars, MaxRows);
        var body = (who.Length > 0 ? $"<color={color}><b>{who}</b></color>\n" : "")
                   + string.Join("\n", rows.Select(r => final ? r : $"<color=#d1d5db><i>{r}</i></color>"));
        if (translated)
            foreach (var r in CaptionView.Wrap(CaptionView.Escape($"{lang}: {textNow}"), RowChars * 3 / 2, 1))
                body += $"\n<size=40><color=#9ca3af>{r}</color></size>";
        if (body != b.Body) Redraw(b, body);
        return true;
    }

    void Remove(int captionId)
    {
        foreach (var kv in bubbles.Where(kv => kv.Value.CaptionId == captionId).ToList()) Drop(kv.Key);
    }

    void Drop(int person)
    {
        var b = bubbles[person];
        Destroy(b.Panel.M);
        foreach (var f in b.Frame) Destroy(f.M);
        Destroy(b.Root.gameObject);
        bubbles.Remove(person);
    }

    Bubble Create()
    {
        if (text == null) text = Ui.TextMaterial(textMaterial, 3013);
        var root = new GameObject("Speaker Bubble").transform;
        root.SetParent(view.transform, false);
        var b = new Bubble { Root = root, Panel = Ui.Quad(root, "Panel", panelMaterial, 3011), Frame = new Ui.Rect[4] };
        for (int i = 0; i < 4; i++) b.Frame[i] = Ui.Quad(root, "Frame", panelMaterial, 3010);
        b.Text = Ui.Text(root, "Text", text, 64, TextScale, TextAnchor.LowerCenter);
        b.Text.alignment = TextAlignment.Left;
        return b;
    }

    /// <summary>Layout around the face centre (the root): frame around the face, text above the head.</summary>
    void Redraw(Bubble b, string body)
    {
        b.Body = body;
        b.Text.text = body;
        float metres = Distance * Mathf.Deg2Rad;                                   // per degree, near the centre
        float faceH = Mathf.Max(MinFace, b.At.h_deg * metres), faceW = 0.8f * faceH;
        float top = Mathf.Max(faceH / 2, (b.At.top_deg - b.At.pitch_deg) * metres);
        var c = new Color(b.Color.r, b.Color.g, b.Color.b, 0.9f);
        b.Frame[0].Set(faceW + Line, Line, Line / 2, new Vector3(0, faceH / 2, 0), c);
        b.Frame[1].Set(faceW + Line, Line, Line / 2, new Vector3(0, -faceH / 2, 0), c);
        b.Frame[2].Set(Line, faceH + Line, Line / 2, new Vector3(-faceW / 2, 0, 0), c);
        b.Frame[3].Set(Line, faceH + Line, Line / 2, new Vector3(faceW / 2, 0, 0), c);
        var size = Ui.Size(b.Text);
        float w = size.x + 2 * Pad, h = size.y + 2 * Pad, bottom = top + Gap;
        b.Panel.Set(w, h, 0.04f, new Vector3(0, bottom + h / 2, 0), new Color(0, 0, 0, 0.6f));
        b.Text.transform.localPosition = new Vector3(0, bottom + Pad, -0.002f);
    }

    /// <summary>Where the face is on the video window now (its local frame); false = cannot be shown.</summary>
    bool TryPlace(At at, out Vector3 dir)
    {
        dir = Vector3.forward;
        if (!view.Measured.TryAt(DaemonClient.NowS() - video.VideoDelayS, out var head)) return false;
        SpeakerPlace.InCamera(at.yaw_deg, at.pitch_deg, head, out var x, out var y, out var z);
        dir = new Vector3((float)x, (float)y, (float)z);
        return view.Locked ? z > -0.2 : SpeakerPlace.InPicture(cam, x, y, z);
    }

    void LateUpdate()
    {
        double now = Time.unscaledTimeAsDouble;
        foreach (var person in bubbles.Where(kv => now > kv.Value.Until).Select(kv => kv.Key).ToList()) Drop(person);
        foreach (var b in bubbles.Values)
        {
            bool on = TryPlace(b.At, out var dir);
            if (b.Root.gameObject.activeSelf != on) b.Root.gameObject.SetActive(on);
            if (!on) continue;
            b.Root.localPosition = dir * Distance;
            b.Root.localRotation = Quaternion.LookRotation(dir, Vector3.up);
        }
    }
}
