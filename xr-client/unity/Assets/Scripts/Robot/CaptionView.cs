// Speech bubbles from the backend's live captions, above the speaker's head in the video. Same id = update
// (partial -> final -> + translation), protocol in backend/README.md. Same behaviour as xr-client/pages/captions.js:
// FaceSpeakers picks who talks (mouth movement + mic direction), the bubble follows that person and points at the
// head; until someone is visible it waits at the top of the window in the mic direction. One bubble per person.

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
    public class CaptionMsg
    {
        public int id;
        public bool final;
        public string text, lang, translation;
        public double doa_deg, t_start, t_end;
        [NonSerialized] public double? Doa;
    }
    [Serializable] class PersonMsg { public double cx, cy, top, w, h, mouth; }
    [Serializable] class FacesMsg { public PersonMsg[] people; public double t; }
    [Serializable] class VadMsg { public bool speaking; }

    const float ShowS = 8f, PartialS = 5f;   // a final / partial bubble stays this long after its last update
    const int MaxBubbles = 3;
    const float Radius = 2.95f;              // just in front of the video sphere
    const float Follow = 0.25f;              // per frame towards the target
    const int MainChars = 34, SubChars = 52, MainLines = 3, SubLines = 2;
    const float Pad = 0.05f, TailH = 0.05f, StripW = 0.012f;
    const float TextScale = 1.3f;
    static readonly string[] Colors = { "#ff9500", "#38bdf8", "#4ade80", "#f472b6", "#a78bfa", "#facc15" };

    class Bubble
    {
        public CaptionMsg Msg;
        public Transform Root;
        public TextMesh Text;
        public Renderer TextRenderer;
        public Material Panel, Strip, Tail;
        public Transform PanelT, StripT, TailT;
        public float Until;
        public int? TrackId, Pid;
        public string Color = "#9ca3af", Label = "";
        public (double cx, double top)? Last;
    }

    public RoomSound room;
    public RobotControl control;
    public Transform window;
    public Material textMaterial, panelMaterial;

    readonly Dictionary<int, Bubble> bubbles = new Dictionary<int, Bubble>();
    readonly CameraModel cam = CameraModel.Calibrated();
    Font font;
    Material text;
    (double cx, double top) lastAnchor = (0.5, 0.02);
    double clockOffsetS, hostToLocalS;   // backend wall clock -> headset wall clock / DaemonClient.NowS()

    public FaceSpeakers Speakers { get; private set; }
    public string Status { get; private set; } = "";

    void Awake()
    {
        Speakers = new FaceSpeakers(cam);
        control.Follow = new SpeakerFollow(Speakers, cam);
        font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
        text = new Material(textMaterial);
        UpdateAtlas(font);
        Font.textureRebuilt += UpdateAtlas;
    }

    void OnDestroy() => Font.textureRebuilt -= UpdateAtlas;

    void UpdateAtlas(Font f)
    {
        if (f == font) text.mainTexture = font.material.mainTexture;
    }

    static double WallS() => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0;

    void Update()
    {
        var backend = room.Backend;
        if (backend == null) return;
        while (backend.TryGetMessage(out var json)) Handle(json);

        foreach (var id in bubbles.Where(b => Time.unscaledTime > b.Value.Until).Select(b => b.Key).ToList()) Remove(id);

        // one bubble per person, the newest wins
        var owners = new HashSet<int>();
        foreach (var b in bubbles.Values.OrderByDescending(b => b.Msg.id).ToList())
        {
            Place(b, false);
            if (b.Pid == null) continue;
            if (!owners.Add(b.Pid.Value)) Remove(b.Msg.id);
        }
        Status = $"{Speakers.Tracks.Count} faces";
    }

    void Handle(string json)
    {
        switch (JsonUtility.FromJson<TypeMsg>(json).type)
        {
            case "hello":
                double hostT = JsonUtility.FromJson<HelloMsg>(json).t;
                clockOffsetS = WallS() - hostT;
                hostToLocalS = DaemonClient.NowS() - hostT;
                break;
            case "faces":
                var msg = JsonUtility.FromJson<FacesMsg>(json);
                var s = control.Daemon?.Latest;
                var follow = control.Follow;
                double t = msg.t + hostToLocalS;   // stamped at capture
                Speakers.FocusPid = follow.FocusPid;
                Speakers.Update(msg.people.Select(p => new Face
                {
                    Cx = p.cx, Cy = p.cy, Top = p.top, W = p.w, H = p.h, Mouth = p.mouth > 0 ? p.mouth : (double?)null,
                }), t, s?.head_pose != null ? follow.Tracker.HeadYawAt(t, s.Head.Yaw) : (double?)null);
                follow.FrameFocus();
                break;
            case "vad":
                control.Follow.OnVad(JsonUtility.FromJson<VadMsg>(json).speaking, DaemonClient.NowS());
                break;
            case "caption":
                var c = JsonUtility.FromJson<CaptionMsg>(json);
                c.Doa = json.Contains("\"doa_deg\": null") ? (double?)null : c.doa_deg;
                OnCaption(c);
                break;
        }
    }

    void OnCaption(CaptionMsg msg)
    {
        if (string.IsNullOrEmpty(msg.text)) { Remove(msg.id); return; }
        // replayed history on (re)connect
        if (msg.final && msg.t_end < WallS() - clockOffsetS - ShowS) return;

        bool isNew = !bubbles.TryGetValue(msg.id, out var b);
        if (isNew) b = bubbles[msg.id] = Create();
        b.Msg = msg;
        b.Until = Time.unscaledTime + (msg.final ? ShowS : PartialS);
        Place(b, isNew);
        // One bubble per speaker: the new utterance replaces the old one
        foreach (var id in bubbles.Keys.Where(id => id < msg.id && bubbles[id].Pid == b.Pid).ToList()) Remove(id);
        foreach (var id in bubbles.Keys.OrderByDescending(id => id).Skip(MaxBubbles).ToList()) Remove(id);
        Redraw(b);
        var track = b.TrackId != null ? Speakers.Get(b.TrackId.Value) : null;
        control.Follow.OnCaption(msg.final && !string.IsNullOrEmpty(msg.translation), WallS() - clockOffsetS - msg.t_end,
            msg.t_end - msg.t_start, track, DaemonClient.NowS());
        Debug.Log($"[captions] {(msg.final ? "final" : "partial")} #{msg.id} {b.Label} {msg.lang}: {msg.text}");
    }

    Bubble Create()
    {
        var root = new GameObject("Bubble").transform;
        root.SetParent(window, false);
        var b = new Bubble { Root = root };
        Transform Part(string name, out Material m, int queue)
        {
            var go = GameObject.CreatePrimitive(PrimitiveType.Quad);
            go.name = name;
            Destroy(go.GetComponent<Collider>());
            go.transform.SetParent(root, false);
            m = new Material(panelMaterial) { renderQueue = queue };
            go.GetComponent<Renderer>().sharedMaterial = m;
            return go.transform;
        }
        b.TailT = Part("Tail", out b.Tail, 3010);
        b.PanelT = Part("Panel", out b.Panel, 3011);
        b.StripT = Part("Strip", out b.Strip, 3012);
        var t = new GameObject("Text");
        t.transform.SetParent(root, false);
        t.transform.localScale = Vector3.one * TextScale;
        b.Text = t.AddComponent<TextMesh>();
        b.Text.font = font;
        b.Text.fontSize = 64;
        b.Text.characterSize = 0.01f;
        b.Text.anchor = TextAnchor.LowerLeft;
        b.Text.alignment = TextAlignment.Left;
        b.Text.richText = true;
        b.TextRenderer = t.GetComponent<MeshRenderer>();
        text.renderQueue = 3013;
        b.TextRenderer.sharedMaterial = text;
        return b;
    }

    void Remove(int id)
    {
        if (!bubbles.TryGetValue(id, out var b)) return;
        Destroy(b.Panel); Destroy(b.Strip); Destroy(b.Tail);
        Destroy(b.Root.gameObject);
        bubbles.Remove(id);
    }

    /// <summary>Word wrap; keeps the end, that is what is being said now.</summary>
    static IEnumerable<string> Wrap(string s, int width, int maxLines)
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
        return lines.Skip(Math.Max(0, lines.Count - maxLines));
    }

    static string Escape(string s) => s.Replace('<', '‹').Replace('>', '›');

    void Redraw(Bubble b)
    {
        var m = b.Msg;
        bool translated = !string.IsNullOrEmpty(m.translation);
        var main = Wrap(Escape(translated ? m.translation : m.text), MainChars, MainLines);
        var sub = translated ? Wrap(Escape($"{m.lang}: {m.text}"), SubChars, SubLines) : Enumerable.Empty<string>();
        var body = new StringBuilder();
        if (!string.IsNullOrEmpty(b.Label)) body.Append($"<size=36><color={b.Color}><b>{b.Label}</b></color></size>\n");
        var color = m.final ? "#111111" : "#444444";
        var mainText = string.Join("\n", main);
        body.Append($"<color={color}>{(m.final ? mainText : $"<i>{mainText}</i>")}</color>");
        foreach (var line in sub) body.Append($"\n<size=38><color=#666666>{line}</color></size>");
        b.Text.text = body.ToString();

        var size = b.TextRenderer.localBounds.size * TextScale;
        float w = Mathf.Max(0.3f, size.x) + 2 * Pad + 2 * StripW, h = size.y + 2 * Pad;
        void Rect(Material mat, Transform t, float rw, float rh, float r, Vector3 pos, Color c)
        {
            t.localScale = new Vector3(rw, rh, 1);
            t.localPosition = pos;
            mat.SetVector("_Rect", new Vector4(rw, rh, r, 0));
            mat.color = c;
        }
        ColorUtility.TryParseHtmlString(b.Color, out var strip);
        var panel = new Color(1, 1, 1, m.final ? 0.95f : 0.8f);
        Rect(b.Panel, b.PanelT, w, h, 0.03f, new Vector3(0, TailH + h / 2, 0), panel);
        Rect(b.Strip, b.StripT, StripW, h - 0.04f, StripW / 2, new Vector3(-w / 2 + 0.02f, TailH + h / 2, -0.001f), strip);
        Rect(b.Tail, b.TailT, 0.05f, 0.05f, 0.005f, new Vector3(0, TailH, 0.001f), panel);
        b.TailT.localRotation = Quaternion.Euler(0, 0, 45);
        b.Text.transform.localPosition = new Vector3(-w / 2 + Pad + StripW, TailH + Pad, -0.002f);
    }

    void SetSpeaker(Bubble b, FaceTrack tr)
    {
        b.TrackId = tr.Id;
        b.Pid = tr.Pid ?? tr.Id;
        b.Color = Colors[(b.Pid.Value - 1) % Colors.Length];
        b.Label = $"Speaker {b.Pid}";
        Redraw(b);
    }

    /// <summary>The head this bubble belongs to, in the image (0..1). Never "nowhere".</summary>
    (double cx, double top) Anchor(Bubble b)
    {
        var tr = b.TrackId != null ? Speakers.Get(b.TrackId.Value) : null;
        if (tr == null && b.TrackId != null)
        {
            // face lost for a moment (blur, head turn, robot turning): back with a new track -> same person, else nearest
            var last = b.Last;
            var near = last == null ? null : Speakers.Tracks.OrderBy(t => Math.Abs(t.Cx - last.Value.cx)).FirstOrDefault();
            tr = Speakers.Tracks.Find(t => t.Pid == b.Pid)
                 ?? (near != null && Math.Abs(near.Cx - last.Value.cx) < 0.3 ? near : null);
            if (tr != null) b.TrackId = tr.Id;
        }
        if (tr == null && b.TrackId == null)
        {
            tr = Speakers.Pick(b.Msg.Doa);   // first time someone is visible for this utterance
            if (tr != null) SetSpeaker(b, tr);
        }
        if (tr != null) { b.Last = lastAnchor = (tr.Cx, tr.Top); return lastAnchor; }
        if (b.Last != null) return b.Last.Value;
        // nobody visible yet: towards the voice (mic direction), at the top of the window
        double cx = b.Msg.Doa != null ? cam.UForYaw(b.Msg.Doa.Value) : lastAnchor.cx;
        return (Math.Max(0.1, Math.Min(0.9, cx)), 0.02);
    }

    /// <summary>Just above the head in the video window; heads cut off at the top: above the window edge.</summary>
    void Place(Bubble b, bool jump)
    {
        var a = Anchor(b);
        double u = Math.Max(0.05, Math.Min(0.95, a.cx)), v = Math.Max(-0.05, a.top);
        if (!cam.Unproject(u, v, out var dx, out var dy, out var dz)) { dx = 0; dy = 0; dz = 1; }
        var target = new Vector3((float)dx, -(float)dy, (float)dz) * (Radius - b.Msg.id % 5 * 0.005f);
        b.Root.localPosition = jump ? target : Vector3.Lerp(b.Root.localPosition, target, Follow);
        b.Root.localRotation = Quaternion.LookRotation(b.Root.localPosition);   // faces the eye: the sphere is centred on it
    }
}
