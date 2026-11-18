// Small building blocks for the app's own panels (QuickMenu, Tutorial, NotesPanel): rounded rectangles with the
// Bubble shader and world-space text with the built-in font, the same way CaptionView draws the speech bubbles.
// Everything is drawn with ZTest Always, so the order is the render queue.

using System.Collections.Generic;
using UnityEngine;

public static class Ui
{
    public static bool German => Application.systemLanguage == SystemLanguage.German;

    // One look for every panel: dark glass with a fine light edge, cyan for what is on or to be pressed
    public static readonly Color Glass = new Color(0.05f, 0.07f, 0.11f, 0.86f);
    public static readonly Color Edge = new Color(1, 1, 1, 0.16f);
    public static readonly Color Accent = new Color(0.30f, 0.76f, 1f);          // #4cc3ff
    public const string AccentHex = "#4cc3ff", InkHex = "#0b1220", SoftHex = "#a9b4c6";
    public const float EdgeW = 0.004f;

    /// <summary>A white-on-clear picture from Resources/Icons, tinted, on a square of `size` metres.</summary>
    public static Renderer Icon(Transform parent, string name, Material textMaterial, float size, int queue, Color tint)
    {
        var go = GameObject.CreatePrimitive(PrimitiveType.Quad);
        go.name = "Icon " + name;
        Object.Destroy(go.GetComponent<Collider>());
        go.transform.SetParent(parent, false);
        go.transform.localScale = new Vector3(size, size, 1);
        var r = go.GetComponent<Renderer>();
        r.sharedMaterial = new Material(textMaterial) { renderQueue = queue, mainTexture = Resources.Load<Texture2D>("Icons/" + name), color = tint };
        return r;
    }

    /// <summary>Three dots, one after the other lit: someone is talking, the text is coming.</summary>
    public static string Dots()
    {
        int on = (int)(Time.unscaledTime * 3f) % 3;
        var s = new System.Text.StringBuilder();
        for (int i = 0; i < 3; i++) s.Append(i == on ? "<color=#ffffff>●</color> " : "<color=#6b7280>●</color> ");
        return s.ToString();
    }

    static Font font;
    static readonly List<Material> textMaterials = new List<Material>();

    public static Font Font
    {
        get
        {
            if (font != null) return font;
            font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            Font.textureRebuilt += f => { if (f == font) foreach (var m in textMaterials) m.mainTexture = f.material.mainTexture; };
            return font;
        }
    }

    /// <summary>A copy of the text material that keeps up with the font atlas.</summary>
    public static Material TextMaterial(Material source, int queue)
    {
        var m = new Material(source) { renderQueue = queue, mainTexture = Font.material.mainTexture };
        textMaterials.Add(m);
        return m;
    }

    public static TextMesh Text(Transform parent, string name, Material material, int size, float scale,
        TextAnchor anchor = TextAnchor.MiddleCenter, FontStyle style = FontStyle.Normal)
    {
        var go = new GameObject(name);
        go.transform.SetParent(parent, false);
        go.transform.localScale = Vector3.one * scale;
        var t = go.AddComponent<TextMesh>();
        t.font = Font;
        t.fontSize = size;
        t.fontStyle = style;
        t.characterSize = 0.01f;
        t.anchor = anchor;
        t.alignment = anchor == TextAnchor.UpperLeft || anchor == TextAnchor.MiddleLeft || anchor == TextAnchor.LowerLeft
            ? TextAlignment.Left : TextAlignment.Center;
        t.richText = true;
        t.color = Color.white;
        go.GetComponent<MeshRenderer>().sharedMaterial = material;
        return t;
    }

    /// <summary>Size of a text in its parent's metres.</summary>
    public static Vector3 Size(TextMesh t) => Vector3.Scale(t.GetComponent<MeshRenderer>().localBounds.size, t.transform.localScale);

    public struct Rect
    {
        public Transform T;
        public Material M;

        /// <summary>w x h metres, corner radius r, centred at `at` (z towards the eye is negative).</summary>
        public void Set(float w, float h, float r, Vector3 at, Color c)
        {
            T.localScale = new Vector3(w, h, 1);
            T.localPosition = at;
            M.SetVector("_Rect", new Vector4(w, h, r, 0));
            M.color = c;
        }

        public void Show(bool on) => T.gameObject.SetActive(on);

        /// <summary>A line along the inside of the edge.</summary>
        public void Border(Color c, float w = EdgeW)
        {
            M.SetColor("_Border", c);
            M.SetFloat("_BorderW", w);
        }
    }

    public static Rect Quad(Transform parent, string name, Material panel, int queue)
    {
        var go = GameObject.CreatePrimitive(PrimitiveType.Quad);
        go.name = name;
        Object.Destroy(go.GetComponent<Collider>());
        go.transform.SetParent(parent, false);
        var m = new Material(panel) { renderQueue = queue };
        go.GetComponent<Renderer>().sharedMaterial = m;
        return new Rect { T = go.transform, M = m };
    }

    /// <summary>Where the head looks (XrInput.Gaze) meets the plane of `t`, in its local coordinates; null if not.</summary>
    public static Vector3? GazeOn(Transform t, Transform head) => PointOn(t, XrInput.Gaze(head));

    /// <summary>What a hand picks on a panel: where its ray points if that is on something to press (`hits`), else
    /// nothing. Only the hand picks, never the eyes (2026-11-16: looking at something must not choose it); until then
    /// the eyes picked wherever no hand pointed.</summary>
    public static Vector3? Pick(Transform panel, Ray? aim, Transform head, System.Func<Vector3?, bool> hits, out Vector3? byHand)
    {
        byHand = aim != null ? PointOn(panel, aim.Value) : null;
        return hits(byHand) ? byHand : null;
    }

    /// <summary>Where a ray meets the plane of `t`, in its local coordinates; null if it does not.</summary>
    public static Vector3? PointOn(Transform t, Ray ray)
    {
        if (!new Plane(t.forward, t.position).Raycast(ray, out float d)) return null;
        return t.InverseTransformPoint(ray.GetPoint(d));
    }

    /// <summary>
    /// The hand cursor on a panel, like in the headset's own menus: a dot where the hand points and a ring around it
    /// that closes as thumb and index finger come together; the dot turns the accent colour once the pinch counts.
    /// </summary>
    public class Cursor
    {
        static readonly Color Ring = new Color(1, 1, 1, 0.28f), Dot = new Color(1, 1, 1, 0.95f), Pinched = new Color(0.98f, 0.8f, 0.08f);
        const float DotD = 0.016f, OpenD = 0.06f;
        static readonly System.Collections.Generic.List<Cursor> all = new System.Collections.Generic.List<Cursor>();
        readonly Rect ring, dot;

        public Cursor(Transform parent, Material panel)
        {
            ring = Quad(parent, "CursorRing", panel, 3050);
            dot = Quad(parent, "CursorDot", panel, 3051);
            Hide();
            all.Add(this);
        }

        /// <summary>Does another cursor already show within `deg` of that ray (a panel's own, for QuickMenu's free one)?</summary>
        public bool OtherNear(Ray ray, float deg)
        {
            all.RemoveAll(c => c.dot.T == null);
            foreach (var c in all)
                if (c != this && c.dot.T.gameObject.activeInHierarchy && c.dot.T.lossyScale.x > 0
                    && Vector3.Angle(ray.direction, c.dot.T.position - ray.origin) < deg) return true;
            return false;
        }

        /// <summary>At a point in the panel's local plane, pinch 0..1.</summary>
        public void Show(Vector3 at, float pinch)
        {
            float d = Mathf.Lerp(OpenD, DotD * 1.2f, Mathf.Clamp01(pinch));
            ring.Set(d, d, d / 2, new Vector3(at.x, at.y, -0.012f), Ring);
            dot.Set(DotD, DotD, DotD / 2, new Vector3(at.x, at.y, -0.013f), pinch > 0.8f ? Pinched : Dot);
            ring.Show(true);
            dot.Show(true);
        }

        public void Hide()
        {
            ring.Show(false);
            dot.Show(false);
        }
    }

    /// <summary>
    /// A window's size and distance (NotesPanel, SharePanel), kept on the headset: − and + left and right of the
    /// handle make it smaller and larger, and while it is dragged by the handle, pulling the hand towards you brings
    /// it closer, pushing it away moves it back (20 cm of hand = 1 m of window).
    /// </summary>
    public class Zoom
    {
        const float D = 0.055f, Off = 0.24f, Hit = 0.09f, Bar = 0.026f, Thick = 0.006f;
        const float Step = 1.15f, MinSize = 0.5f, MaxSize = 2.5f, MinDist = 0.7f, MaxDist = 3.5f, ReachGain = 5f;
        readonly string key;
        readonly Rect[] back = new Rect[2], bars = new Rect[3];
        readonly bool[] onLess = new bool[2], onMore = new bool[2];   // per hand
        Vector3 less, more;
        float startDist, startReach = -1;
        public float Size { get; private set; }
        public float Dist { get; private set; }

        public Zoom(Transform parent, Material panel, int queue, string key, float dist)
        {
            this.key = key;
            Size = PlayerPrefs.GetFloat(key + "_size", 1f);
            Dist = PlayerPrefs.GetFloat(key + "_dist", dist);
            for (int i = 0; i < 2; i++) back[i] = Quad(parent, "Zoom" + i, panel, queue);
            for (int i = 0; i < 3; i++) bars[i] = Quad(parent, "ZoomBar" + i, panel, queue + 1);
        }

        public bool On(Vector3? p) => Inside(p, less, Hit, Hit) || Inside(p, more, Hit, Hit);
        public bool Hovered(int hand) => onLess[hand] || onMore[hand];

        /// <summary>Where that hand points (null: nowhere on the window), with the handle at `handleAt`.</summary>
        public void Point(int hand, Vector3? p, Vector3 handleAt)
        {
            less = handleAt + new Vector3(-Off, 0, 0);
            more = handleAt + new Vector3(Off, 0, 0);
            onLess[hand] = Inside(p, less, Hit, Hit);
            onMore[hand] = Inside(p, more, Hit, Hit);
        }

        public void Draw()
        {
            back[0].Set(D, D, D / 2, less, new Color(1, 1, 1, onLess[0] || onLess[1] ? 0.3f : 0.1f));
            back[1].Set(D, D, D / 2, more, new Color(1, 1, 1, onMore[0] || onMore[1] ? 0.3f : 0.1f));
            var up = new Vector3(0, 0, -0.001f);
            bars[0].Set(Bar, Thick, Thick / 2, less + up, Color.white);
            bars[1].Set(Bar, Thick, Thick / 2, more + up, Color.white);
            bars[2].Set(Thick, Bar, Thick / 2, more + up, Color.white);
        }

        /// <summary>A pinch of that hand on − or +.</summary>
        public void Press(int hand)
        {
            Size = Mathf.Clamp(Size * (onMore[hand] ? Step : 1 / Step), MinSize, MaxSize);
            PlayerPrefs.SetFloat(key + "_size", Size);
            PlayerPrefs.Save();
        }

        /// <summary>While the window is dragged: `reach` is how far that hand is from the eye, null if not tracked.</summary>
        public void Drag(float? reach)
        {
            if (!(reach is float r)) return;
            if (startReach < 0) { startReach = r; startDist = Dist; }
            Dist = Mathf.Clamp(startDist + ReachGain * (r - startReach), MinDist, MaxDist);
        }

        public void EndDrag()
        {
            startReach = -1;
            PlayerPrefs.SetFloat(key + "_dist", Dist);
            PlayerPrefs.Save();
        }

        public static float? Reach(bool right, Transform head) =>
            XrInput.PinchPoint(!right, head) is Vector3 p ? (p - head.position).magnitude : (float?)null;
    }

    public static bool Inside(Vector3? p, Vector3 centre, float w, float h) =>
        p is Vector3 q && Mathf.Abs(q.x - centre.x) < w / 2 && Mathf.Abs(q.y - centre.y) < h / 2;
}
