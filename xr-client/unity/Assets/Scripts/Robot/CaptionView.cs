// Speech bubbles from the backend's live captions above the speaker's head, and a live marker on whoever speaks.
// Same id = update (partial -> final -> + translation), protocol at the top of backend/.../captions.py.
//
// Who talks: FaceSpeakers, from mouth movement while the words were said (judged again with every update), the mic
// direction only breaking ties. The bubble is fixed in the room where that person is (robot head pose when the face
// was seen + face direction), so it stays on them when Reachy looks elsewhere and the panorama shows them. Nobody in
// view talking: the bubble waits at the top of the window, towards the voice. One bubble per person.
//
// Speaking marker, from the backend's voice detection (~0.1 s, long before any text): a ring around the face whose
// mouth moves, or an arrow at the window edge towards the voice when nobody in the picture is talking.
// Speaker numbers and colours are not shown for now: telling people apart is not reliable enough yet.

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
        public double doa_deg, azimuth_deg, t_start, t_end;
        [NonSerialized] public double? Doa, Azimuth;
    }
    [Serializable] class VadMsg { public bool speaking; public double doa_deg, azimuth_deg; }
    [Serializable] class PersonMsg { public double cx, cy, top, w, h, mouth; }
    [Serializable] class FacesMsg { public PersonMsg[] people; public double t; }

    const float ShowS = 8f, PartialS = 5f;   // a final / partial bubble stays this long after its last update
    const int MaxBubbles = 3;
    const float Radius = 2.95f;              // just in front of the video sphere
    const float Follow = 0.25f;              // per frame towards the target
    const int MainChars = 34, SubChars = 52, MainLines = 3, SubLines = 2;
    const float Pad = 0.05f, TailH = 0.05f, StripW = 0.012f;
    const float TextScale = 1.3f;
    const string Accent = "#ff9500";
    const float HeadLift = 0.25f;            // bubble tip this far above the top of the face, in face heights
    const double JudgeMaxS = 8;              // who said an utterance: judged over at most its last this many seconds
    const double LiveS = 0.6;                // the speaking marker judges mouth movement over this window
    const float VadHoldS = 0.6f;             // speech goes on this long after the last voice message saying so
    const float OffDelayS = 0.4f;            // the arrow waits this long for a moving mouth: no flash at every start
    const float FadeS = 0.15f;
    const float RingScale = 1.3f, RingLine = 0.012f, ArrowBar = 0.07f;

    class Bubble
    {
        public CaptionMsg Msg;
        public Transform Root;
        public TextMesh Text;
        public Renderer TextRenderer;
        public Material Panel, Strip, Tail;
        public Transform PanelT, StripT, TailT;
        public float Until, W, H;
        public int? TrackId, Pid;
        public bool Off, Fresh = true;       // Off: nobody in view talked
        public Vector3? Room, Cam;           // where the head was: in the room (world-locked only), in the camera
    }

    enum Marker { None, Face, Arrow }

    public RoomSound room;
    public RobotControl control;
    public VideoWindow view;
    public Material textMaterial, panelMaterial;

    readonly Dictionary<int, Bubble> bubbles = new Dictionary<int, Bubble>();
    readonly CameraModel cam = CameraModel.Calibrated();
    Font font;
    Material text;
    double clockOffsetS;
    Transform roomT;

    Transform markerT, ringT, arrowT;
    Material ringMat, arrowMat;
    Marker marker, lastMode;   // marker: what is drawn, also while it fades out
    float markerAlpha, vadUntil, offSince = -1;
    double? liveDoa, liveAz;
    int? markerTrack, markerPid;

    public FaceSpeakers Speakers { get; private set; }
    public string Status { get; private set; } = "";

    void Awake()
    {
        Speakers = new FaceSpeakers(cam);
        font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
        text = new Material(textMaterial);
        UpdateAtlas(font);
        Font.textureRebuilt += UpdateAtlas;
        // the room around the eye, like the panorama: robot base frame in the user's "straight ahead"
        roomT = new GameObject("Speech").transform;
        roomT.SetParent(view.transform.parent, false);
        CreateMarker();
    }

    void OnDestroy() => Font.textureRebuilt -= UpdateAtlas;

    void UpdateAtlas(Font f)
    {
        if (f == font) text.mainTexture = font.material.mainTexture;
    }

    static double WallS() => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0;

    static Quaternion ToUnity(Quat q) => new Quaternion(-(float)q.X, -(float)q.Y, (float)q.Z, (float)q.W);

    static Quaternion HeadRotation(RobotAngles a) => ToUnity(PoseMath.RobotToHeadset(a.Roll, a.Pitch, a.Yaw));

    /// <summary>A number that may be null or missing (older backend): JsonUtility reads both as 0.</summary>
    static double? Num(string json, string key, double value) =>
        json.Contains($"\"{key}\": ") && !json.Contains($"\"{key}\": null") ? value : (double?)null;

    void Update()
    {
        var backend = room.Backend;
        if (backend == null) return;
        roomT.localRotation = ToUnity(control.Frame.ToWorld(Quat.Identity));
        roomT.localPosition = view.transform.localPosition;
        while (backend.TryGetMessage(out var json)) Handle(json);

        foreach (var id in bubbles.Where(b => Time.unscaledTime > b.Value.Until).Select(b => b.Key).ToList()) Remove(id);
        // still talking and nobody in view was: the speaker may come into the picture
        foreach (var b in bubbles.Values) if (b.Off && !b.Msg.final) Judge(b, true);

        // one bubble per person, the newest wins
        var shown = new List<Bubble>();
        var owners = new HashSet<int>();
        foreach (var b in bubbles.Values.OrderByDescending(b => b.Msg.id).ToList())
        {
            if (b.Pid != null && !owners.Add(b.Pid.Value)) { Remove(b.Msg.id); continue; }
            shown.Add(b);
        }
        Place(shown);
        UpdateMarker();
        Status = $"{Speakers.Tracks.Count} faces{(marker == Marker.Face ? ", speaking" : marker == Marker.Arrow ? ", voice off picture" : "")}";
    }

    void Handle(string json)
    {
        switch (JsonUtility.FromJson<TypeMsg>(json).type)
        {
            case "hello":
                clockOffsetS = WallS() - JsonUtility.FromJson<HelloMsg>(json).t;
                break;
            case "faces":
                var msg = JsonUtility.FromJson<FacesMsg>(json);
                var s = control.Daemon?.Latest;
                Speakers.Update(msg.people.Select(p => new Face
                {
                    Cx = p.cx, Cy = p.cy, Top = p.top, W = p.w, H = p.h, Mouth = p.mouth > 0 ? p.mouth : (double?)null,
                }), msg.t, s?.head_pose != null ? s.Head.Yaw : (double?)null);
                break;
            case "vad":
                var v = JsonUtility.FromJson<VadMsg>(json);
                vadUntil = v.speaking ? Time.unscaledTime + VadHoldS : 0;
                liveDoa = Num(json, "doa_deg", v.doa_deg);
                liveAz = Num(json, "azimuth_deg", v.azimuth_deg);
                break;
            case "caption":
                var c = JsonUtility.FromJson<CaptionMsg>(json);
                c.Doa = Num(json, "doa_deg", c.doa_deg);
                c.Azimuth = Num(json, "azimuth_deg", c.azimuth_deg);
                OnCaption(c);
                break;
        }
    }

    void OnCaption(CaptionMsg msg)
    {
        if (string.IsNullOrEmpty(msg.text)) { Remove(msg.id); return; }
        // replayed history on (re)connect
        if (msg.final && msg.t_end < WallS() - clockOffsetS - ShowS) return;

        if (!bubbles.TryGetValue(msg.id, out var b)) b = bubbles[msg.id] = Create();
        b.Msg = msg;
        b.Until = Time.unscaledTime + (msg.final ? ShowS : PartialS);
        Judge(b, false);
        // One bubble per speaker: the new utterance replaces the old one
        foreach (var id in bubbles.Keys.Where(id => id < msg.id && bubbles[id].Pid == b.Pid).ToList()) Remove(id);
        foreach (var id in bubbles.Keys.OrderByDescending(id => id).Skip(MaxBubbles).ToList()) Remove(id);
        Redraw(b);
        Debug.Log($"[captions] {(msg.final ? "final" : "partial")} #{msg.id} {msg.lang}: {msg.text}");
    }

    string Mouths(double from, double to) =>
        string.Join(", ", Speakers.Tracks.Select(t => $"p{t.Pid} {FaceSpeakers.Activity(t, from, to):0.000}"));

    /// <summary>Who said this bubble's words. `live`: over the last moments, for a bubble still waiting for a face.</summary>
    void Judge(Bubble b, bool live)
    {
        var m = b.Msg;
        double to = live ? Speakers.LastT ?? m.t_end : m.t_end;
        double from = Math.Max(m.t_start, to - (live ? 2 * LiveS : JudgeMaxS));
        var current = b.TrackId != null ? Speakers.Get(b.TrackId.Value) : null;
        var tr = Speakers.Talker(from, to, m.Doa, current);
        if (tr == null)
        {
            // keep an earlier answer: the first part of the utterance said who it was
            if (b.TrackId == null && !b.Off && !live)
            {
                b.Off = true;
                Debug.Log($"[speakers] #{m.id}: nobody in view talking (mouth {Mouths(from, to)}), voice at {m.Doa?.ToString("0") ?? "?"}°");
            }
            return;
        }
        int pid = tr.Pid ?? tr.Id;
        b.TrackId = tr.Id;
        b.Off = false;
        if (b.Pid == pid) return;
        Debug.Log($"[speakers] #{m.id}: {(b.Pid == null ? "" : $"p{b.Pid} -> ")}p{pid} (mouth {Mouths(from, to)}), voice at {m.Doa?.ToString("0") ?? "?"}°");
        b.Pid = pid;
        b.Room = b.Cam = null;
    }

    Bubble Create()
    {
        var root = new GameObject("Bubble").transform;
        root.SetParent(roomT, false);
        var b = new Bubble { Root = root };
        b.TailT = Part("Tail", root, out b.Tail, 3010);
        b.PanelT = Part("Panel", root, out b.Panel, 3011);
        b.StripT = Part("Strip", root, out b.Strip, 3012);
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

    /// <summary>A quad drawn with the bubble shader; its own material unless one is given.</summary>
    Transform Part(string name, Transform parent, out Material m, int queue, Material shared = null)
    {
        var go = GameObject.CreatePrimitive(PrimitiveType.Quad);
        go.name = name;
        Destroy(go.GetComponent<Collider>());
        go.transform.SetParent(parent, false);
        m = shared != null ? shared : new Material(panelMaterial) { renderQueue = queue };
        go.GetComponent<Renderer>().sharedMaterial = m;
        return go.transform;
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

    static void Rect(Material mat, Transform t, float rw, float rh, float r, Vector3 pos, Color c, float line = 0)
    {
        t.localScale = new Vector3(rw, rh, 1);
        t.localPosition = pos;
        mat.SetVector("_Rect", new Vector4(rw, rh, r, line));
        mat.color = c;
    }

    void Redraw(Bubble b)
    {
        var m = b.Msg;
        bool translated = !string.IsNullOrEmpty(m.translation);
        var main = Wrap(Escape(translated ? m.translation : m.text), MainChars, MainLines);
        var sub = translated ? Wrap(Escape($"{m.lang}: {m.text}"), SubChars, SubLines) : Enumerable.Empty<string>();
        var body = new StringBuilder();
        var color = m.final ? "#111111" : "#444444";
        var mainText = string.Join("\n", main);
        body.Append($"<color={color}>{(m.final ? mainText : $"<i>{mainText}</i>")}</color>");
        foreach (var line in sub) body.Append($"\n<size=38><color=#666666>{line}</color></size>");
        b.Text.text = body.ToString();

        var size = b.TextRenderer.localBounds.size * TextScale;
        float w = Mathf.Max(0.3f, size.x) + 2 * Pad + 2 * StripW, h = size.y + 2 * Pad;
        b.W = w;
        b.H = h;
        ColorUtility.TryParseHtmlString(Accent, out var strip);
        var panel = new Color(1, 1, 1, m.final ? 0.95f : 0.8f);
        Rect(b.Panel, b.PanelT, w, h, 0.03f, new Vector3(0, TailH + h / 2, 0), panel);
        Rect(b.Strip, b.StripT, StripW, h - 0.04f, StripW / 2, new Vector3(-w / 2 + 0.02f, TailH + h / 2, -0.001f), strip);
        Rect(b.Tail, b.TailT, 0.05f, 0.05f, 0.005f, new Vector3(0, TailH, 0.001f), panel);
        b.TailT.localRotation = Quaternion.Euler(0, 0, 45);
        b.Text.transform.localPosition = new Vector3(-w / 2 + Pad + StripW, TailH + Pad, -0.002f);
    }

    /// <summary>Image point (0..1) to a direction in the camera, Unity axes (x right, y up, z forward).</summary>
    Vector3 CamDir(double u, double v) =>
        cam.Unproject(Math.Max(0.02, Math.Min(0.98, u)), Math.Max(0, Math.Min(1, v)), out var dx, out var dy, out var dz)
            ? new Vector3((float)dx, -(float)dy, (float)dz) : Vector3.forward;

    float FaceHeightDeg(FaceTrack tr) => Vector3.Angle(CamDir(tr.Cx, tr.Top), CamDir(tr.Cx, tr.Top + tr.H));

    /// <summary>Just above the head, in the camera.</summary>
    Vector3 HeadDir(FaceTrack tr) => Quaternion.AngleAxis(-HeadLift * FaceHeightDeg(tr), Vector3.right) * CamDir(tr.Cx, tr.Top);

    /// <summary>A camera direction in the room, by the measured head pose when the face was seen (laptop clock). The
    /// faces come straight from the camera, ahead of the video in the window, so not the window's pose.</summary>
    Vector3? ToRoom(Vector3 camDir, double laptopS)
    {
        if (!view.Locked || !view.TryLocal(laptopS, out var local) || !view.Measured.TryAt(local, out var a)) return null;
        return HeadRotation(a) * camDir;
    }

    /// <summary>A camera direction in the window as it is shown now, in the room frame.</summary>
    Vector3 WindowToRoom(Vector3 camDir) => view.Locked
        ? HeadRotation(view.Shown) * camDir
        : Quaternion.Inverse(roomT.localRotation) * view.transform.localRotation * camDir;

    /// <summary>The voice relative to what the window shows now, degrees + = left; null if unknown.</summary>
    double? VoiceDeg(double? doa, double? azimuth)
    {
        double? rel = view.Locked && azimuth != null ? azimuth - view.Shown.Yaw : doa;
        return rel == null ? (double?)null : Mathf.DeltaAngle(0, (float)rel.Value);
    }

    /// <summary>Where the bubble's tip goes, in the room frame.</summary>
    Vector3 Target(Bubble b)
    {
        var tr = b.TrackId != null ? Speakers.Get(b.TrackId.Value) : null;
        if (tr == null && b.Pid != null)
        {
            // face lost for a moment (blur, head turn, robot turning): back with a new track -> same person
            tr = Speakers.Tracks.Find(t => t.Pid == b.Pid);
            if (tr != null) b.TrackId = tr.Id;
        }
        if (tr != null && tr.Seen != null)
        {
            b.Cam = HeadDir(tr);
            b.Room = ToRoom(b.Cam.Value, tr.Seen.Value) ?? b.Room;
        }
        // out of the picture: stays where the person was in the room
        if (view.Locked && b.Room != null) return b.Room.Value;
        if (b.Cam != null) return WindowToRoom(b.Cam.Value);
        // nobody in view talking: at the top of the window, towards the voice
        var voice = VoiceDeg(b.Msg.Doa, b.Msg.Azimuth);
        double u = voice != null ? cam.UForYaw(voice.Value) : 0.5;
        return WindowToRoom(CamDir(Math.Max(0.1, Math.Min(0.9, u)), 0.02));
    }

    /// <summary>Newest first: an older bubble that would cover a newer one moves up above it.</summary>
    void Place(List<Bubble> newestFirst)
    {
        var placed = new List<(float yaw, float lo, float hi, float half)>();
        foreach (var b in newestFirst)
        {
            var d = Target(b).normalized;
            float yaw = Mathf.Atan2(d.x, d.z), pitch = Mathf.Asin(Mathf.Clamp(d.y, -1, 1));
            float half = b.W / 2 / Radius, height = (TailH + b.H) / Radius;
            foreach (var p in placed)
                if (Mathf.Abs(Mathf.DeltaAngle(yaw * Mathf.Rad2Deg, p.yaw * Mathf.Rad2Deg)) * Mathf.Deg2Rad < half + p.half
                    && pitch < p.hi && pitch + height > p.lo)
                    pitch = p.hi + 0.01f;
            placed.Add((yaw, pitch, pitch + height, half));
            var target = new Vector3(Mathf.Sin(yaw) * Mathf.Cos(pitch), Mathf.Sin(pitch), Mathf.Cos(yaw) * Mathf.Cos(pitch))
                         * (Radius - b.Msg.id % 5 * 0.005f);
            b.Root.localPosition = b.Fresh ? target : Vector3.Lerp(b.Root.localPosition, target, Follow);
            b.Fresh = false;
            b.Root.localRotation = Quaternion.LookRotation(b.Root.localPosition);   // faces the eye, upright in the room
        }
    }

    void CreateMarker()
    {
        markerT = new GameObject("Speaking").transform;
        markerT.SetParent(roomT, false);
        ringT = Part("Ring", markerT, out ringMat, 3005);
        arrowT = new GameObject("Arrow").transform;
        arrowT.SetParent(markerT, false);
        // a chevron pointing right (+x), tip at the centre: two bars at ±45°
        float c = ArrowBar / 2 * Mathf.Sqrt(0.5f);
        for (int i = 0; i < 2; i++)
        {
            float s = i == 0 ? 1 : -1;
            var bar = Part("Bar", arrowT, out var m, 3005, arrowMat);
            arrowMat = m;
            bar.localRotation = Quaternion.Euler(0, 0, -45 * s);
            Rect(arrowMat, bar, ArrowBar, 0.022f, 0.011f, new Vector3(-c, s * c, 0), Color.white);
        }
        markerT.gameObject.SetActive(false);
    }

    /// <summary>Ring around the face that is talking now, or an arrow at the window edge towards the voice.</summary>
    void UpdateMarker()
    {
        bool speech = Time.unscaledTime < vadUntil && !control.MicOn;   // own voice from Reachy's speaker: not the room
        FaceTrack talker = null;
        if (speech && Speakers.LastT != null)
        {
            double t = Speakers.LastT.Value;
            talker = Speakers.Talker(t - LiveS, t, liveDoa, markerTrack != null ? Speakers.Get(markerTrack.Value) : null);
        }
        markerTrack = talker?.Id;
        if (talker != null || !speech) offSince = -1;
        else if (offSince < 0) offSince = Time.unscaledTime;
        var voice = VoiceDeg(liveDoa, liveAz);
        var mode = talker != null ? Marker.Face
            : offSince >= 0 && Time.unscaledTime - offSince > OffDelayS && voice != null ? Marker.Arrow : Marker.None;

        int? pid = talker?.Pid ?? talker?.Id;
        if (mode != lastMode || pid != markerPid)
            Debug.Log(mode == Marker.Face ? $"[speaking] p{pid}" : mode == Marker.Arrow ? $"[speaking] out of the picture at {voice:0}°" : "[speaking] stopped");
        bool jump = markerAlpha <= 0 || (mode != Marker.None && mode != marker);
        if (mode != Marker.None) marker = mode;
        else if (marker != Marker.None && markerAlpha <= 0) marker = Marker.None;
        markerPid = pid;
        lastMode = mode;

        Vector3? dir = null;
        if (mode == Marker.Face)
        {
            var c = CamDir(talker.Cx, talker.Cy);
            dir = ToRoom(c, talker.Seen.Value) ?? WindowToRoom(c);
            float faceDeg = Mathf.Max(FaceHeightDeg(talker), Vector3.Angle(CamDir(talker.Cx - talker.W / 2, talker.Cy),
                                                                          CamDir(talker.Cx + talker.W / 2, talker.Cy)));
            float d = Mathf.Clamp(RingScale * faceDeg * Mathf.Deg2Rad * Radius, 0.12f, 0.8f);
            Rect(ringMat, ringT, d, d, d / 2, Vector3.zero, Color.white, RingLine);
        }
        else if (mode == Marker.Arrow)
        {
            // inside the picture's width: at the top edge, pointing down; else at the side edge, pointing out
            double u = cam.UForYaw(voice.Value);
            float angle = u < 0.1 ? 180 : u > 0.9 ? 0 : -90;
            dir = WindowToRoom(angle == -90 ? CamDir(u, 0.05) : CamDir(angle == 0 ? 0.97 : 0.03, 0.5));
            arrowT.localRotation = Quaternion.Euler(0, 0, angle);
        }
        if (dir != null)
        {
            var target = dir.Value.normalized * Radius;
            markerT.localPosition = jump ? target : Vector3.Lerp(markerT.localPosition, target, Follow);
            markerT.localRotation = Quaternion.LookRotation(markerT.localPosition);
        }
        ringT.gameObject.SetActive(marker == Marker.Face);
        arrowT.gameObject.SetActive(marker == Marker.Arrow);

        markerAlpha = Mathf.MoveTowards(markerAlpha, mode != Marker.None ? 1 : 0, Time.unscaledDeltaTime / FadeS);
        markerT.gameObject.SetActive(markerAlpha > 0);
        ColorUtility.TryParseHtmlString(Accent, out var accent);
        accent.a = markerAlpha * (0.75f + 0.25f * Mathf.Sin(Time.unscaledTime * 2 * Mathf.PI * 1.2f));
        ringMat.color = arrowMat.color = accent;
    }
}
