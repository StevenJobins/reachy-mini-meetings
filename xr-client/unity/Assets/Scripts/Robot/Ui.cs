// Small building blocks for the app's own panels (QuickMenu, Tutorial, NotesPanel): rounded rectangles with the
// Bubble shader and world-space text with the built-in font, the same way CaptionView draws the speech bubbles.
// Everything is drawn with ZTest Always, so the order is the render queue.

using System.Collections.Generic;
using UnityEngine;

public static class Ui
{
    public static bool German => Application.systemLanguage == SystemLanguage.German;

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

    /// <summary>Where the gaze (eyes, else head: XrInput) meets the plane of `t`, in its local coordinates; null if not.</summary>
    public static Vector3? GazeOn(Transform t, Transform head) => PointOn(t, XrInput.Gaze(head));

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
        readonly Rect ring, dot;

        public Cursor(Transform parent, Material panel)
        {
            ring = Quad(parent, "CursorRing", panel, 3050);
            dot = Quad(parent, "CursorDot", panel, 3051);
            Hide();
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

    public static bool Inside(Vector3? p, Vector3 centre, float w, float h) =>
        p is Vector3 q && Mathf.Abs(q.x - centre.x) < w / 2 && Mathf.Abs(q.y - centre.y) < h / 2;
}
