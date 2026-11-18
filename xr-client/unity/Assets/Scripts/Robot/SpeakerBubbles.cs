// Who is who, above their heads: every person the backend's camera has found gets a frame around the face and a
// name tag above the head, "Speaker A", "Speaker B", ... in their own colour, or the name they gave of themselves
// ("I'm Anna", backend names.py). Always there, also while nobody talks; while that person talks the frame is thicker
// and a dot stands before the name. What is said stands only in the subtitles (CaptionView), under the same name and
// colour. (Until 2026-11-18 the text itself stood here in a bubble, only while someone talked: git 33ff389. The class
// kept its name, the scene refers to it.)
//
// The backend sends everyone it has seen 5 times a second (message "people", backend captions.py): the participant's
// number, the name, and where the face is as a direction in the robot base frame.
// The tag hangs on the video window, at the place where that direction is in the picture shown now (robot head
// pose from the video delay ago). While the window is world-locked that is a fixed place in the room, also outside
// the live picture (the room panorama shows the person there); while it stands in front of the user, only people
// inside the picture get a tag.

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

    [Serializable]
    public class PersonMsg
    {
        public int id;
        public string name;
        public float yaw_deg, pitch_deg, top_deg, h_deg, seen_s;
        public bool speaking;
    }
    [Serializable] public class PeopleMsg { public PersonMsg[] people; }

    class Tag
    {
        public PersonMsg Now;             // as the backend last said
        public float Yaw, Pitch, Top, H;  // eased towards it: the backend's numbers come 5 times a second
        public string Body;
        public bool Thick;
        public float DrawnH, DrawnTop;    // the face size and head top the frame was last drawn for
        public Transform Root;
        public Ui.Rect Panel, Frame;
        public TextMesh Text;
        public Color Color;
    }

    const float Distance = 2.9f;                   // just inside the video sphere (3 m)
    const float Pad = 0.03f, TextScale = 1.0f, Gap = 0.05f, Line = 0.008f, ThickLine = 0.02f, MinFace = 0.2f;
    const float EaseS = 0.25f;
    const double StaleS = 2.0;                     // no word from the backend this long: no tags
    const float SeenS = 3f;                        // in the live picture, a face not seen this long is not marked

    public VideoWindow view;
    public RobotVideo video;
    public Material textMaterial, panelMaterial;
    /// <summary>Not shown (Curtain).</summary>
    public bool Hidden;

    readonly Dictionary<int, Tag> tags = new Dictionary<int, Tag>();   // by participant
    readonly CameraModel cam = CameraModel.Calibrated();
    Material text;
    double heardAt = double.NegativeInfinity;

    public string Status => tags.Count > 0 ? $"people {tags.Count}" : "";

    /// <summary>Everyone the backend's camera has seen, and where (CaptionView hands the message over).</summary>
    public void People(PeopleMsg msg, double now)
    {
        heardAt = now;
        var people = msg.people ?? Array.Empty<PersonMsg>();
        foreach (var gone in tags.Keys.Where(id => people.All(p => p.id != id)).ToList()) Drop(gone);
        foreach (var p in people)
        {
            if (!tags.TryGetValue(p.id, out var t))
            {
                t = tags[p.id] = Create();
                t.Yaw = p.yaw_deg; t.Pitch = p.pitch_deg; t.Top = p.top_deg; t.H = p.h_deg;
            }
            t.Now = p;
            ColorUtility.TryParseHtmlString(SpeakerLabel.Color(p.id), out t.Color);
        }
    }

    void Drop(int id)
    {
        var t = tags[id];
        Destroy(t.Panel.M);
        Destroy(t.Frame.M);
        Destroy(t.Root.gameObject);
        tags.Remove(id);
    }

    Tag Create()
    {
        if (text == null) text = Ui.TextMaterial(textMaterial, 3013);
        var root = new GameObject("Speaker Tag").transform;
        root.SetParent(view.transform, false);
        var t = new Tag { Root = root, Panel = Ui.Quad(root, "Panel", panelMaterial, 3011), Frame = Ui.Quad(root, "Face", panelMaterial, 3010) };
        t.Text = Ui.Text(root, "Text", text, 64, TextScale, TextAnchor.LowerCenter);
        t.Text.alignment = TextAlignment.Center;
        return t;
    }

    /// <summary>Layout around the face centre (the root): frame around the face, the name above the head.</summary>
    void Redraw(Tag t, string body, bool thick)
    {
        t.Body = body;
        t.Thick = thick;
        t.DrawnH = t.H;
        t.DrawnTop = t.Top - t.Pitch;
        t.Text.text = body;
        float metres = Distance * Mathf.Deg2Rad;                                   // per degree, near the centre
        float faceH = Mathf.Max(MinFace, t.H * metres), faceW = 0.8f * faceH;
        float top = Mathf.Max(faceH / 2, (t.Top - t.Pitch) * metres);
        t.Frame.Set(faceW, faceH, 0.25f * faceW, Vector3.zero, Color.clear);
        t.Frame.Border(new Color(t.Color.r, t.Color.g, t.Color.b, thick ? 1f : 0.8f), thick ? ThickLine : Line);
        var size = Ui.Size(t.Text);
        float w = size.x + 2 * Pad, h = size.y + 2 * Pad, bottom = top + Gap;
        t.Panel.Set(w, h, h / 2, new Vector3(0, bottom + h / 2, 0), Ui.Glass);
        t.Panel.Border(new Color(t.Color.r, t.Color.g, t.Color.b, thick ? 0.9f : 0.45f));
        t.Text.transform.localPosition = new Vector3(0, bottom + Pad, -0.002f);
    }

    /// <summary>Where that direction is on the video window now (its local frame); false = cannot be shown.</summary>
    bool TryPlace(float yaw, float pitch, out Vector3 dir)
    {
        dir = Vector3.forward;
        if (!view.Measured.TryAt(DaemonClient.NowS() - video.VideoDelayS, out var head)) return false;
        SpeakerPlace.InCamera(yaw, pitch, head, out var x, out var y, out var z);
        dir = new Vector3((float)x, (float)y, (float)z);
        return view.Locked ? z > -0.2 : SpeakerPlace.InPicture(cam, x, y, z);
    }

    void LateUpdate()
    {
        double now = Time.unscaledTimeAsDouble;
        if (now - heardAt > StaleS)
            foreach (var id in tags.Keys.ToList()) Drop(id);
        float k = 1 - Mathf.Exp(-Time.unscaledDeltaTime / EaseS);
        foreach (var t in tags.Values)
        {
            var p = t.Now;
            t.Yaw += Mathf.DeltaAngle(t.Yaw, p.yaw_deg) * k;
            t.Pitch += (p.pitch_deg - t.Pitch) * k;
            t.Top += (p.top_deg - t.Top) * k;
            t.H += (p.h_deg - t.H) * k;
            // In the live picture a face that is not found any more is not marked (the person turned away or left);
            // on the world-locked window the tag stays at their place, as the panorama shows them there.
            var dir = Vector3.forward;
            bool on = !Hidden && (view.Locked || p.seen_s < SeenS) && TryPlace(t.Yaw, t.Pitch, out dir);
            var name = CaptionView.Escape(SpeakerLabel.Name(p.id, p.name));
            var col = SpeakerLabel.Color(p.id);
            var body = p.speaking ? $"<color={col}>● <b>{name}</b></color>" : $"<color={col}><b>{name}</b></color>";
            if (body != t.Body || p.speaking != t.Thick || Mathf.Abs(t.H - t.DrawnH) > 0.3f
                || Mathf.Abs(t.Top - t.Pitch - t.DrawnTop) > 0.3f) Redraw(t, body, p.speaking);
            if (t.Root.gameObject.activeSelf != on) t.Root.gameObject.SetActive(on);
            if (!on) continue;
            t.Root.localPosition = dir * Distance;
            t.Root.localRotation = Quaternion.LookRotation(dir, Vector3.up);
        }
    }
}
