// Two small things that are always there (2026-11-16):
// - The time of day, small at the top left of the view, moving with the head (the headset's local time).
// - Feedback for a pinch: when a pinch counts (RobotControl's own threshold, the same one that presses tiles), a small
//   dot in the accent colour pops up between thumb and index finger, stays a little smaller while you hold, and fades
//   out when you let go. Anywhere, not only on the menu, so you know the headset saw the pinch. Hands only (with
//   controllers there is no pinch point).

using System;
using UnityEngine;

public class ViewOverlay : MonoBehaviour
{
    // top left of the view: about 22° left and 16° up, 1.2 m away
    static readonly Vector3 ClockAt = new Vector3(-0.48f, 0.34f, 1.2f);
    static readonly Color ClockColor = new Color(1, 1, 1, 0.75f);
    const float PopD = 0.024f, HoldD = 0.014f, PopS = 0.12f, FadeS = 0.2f, DotAlpha = 0.85f;

    public RobotControl control;
    public Transform head;
    public Material textMaterial, panelMaterial;

    TextMesh clock;
    string shown;
    readonly Transform[] holders = new Transform[2];
    readonly Ui.Rect[] dots = new Ui.Rect[2];
    readonly float[] since = { -1f, -1f }, releasedAt = { -10f, -10f };
    readonly bool[] was = new bool[2];
    readonly Vector3[] lastAt = new Vector3[2];

    void Awake()
    {
        var holder = new GameObject("Clock").transform;
        holder.SetParent(head, false);
        holder.localPosition = ClockAt;
        clock = Ui.Text(holder, "Time", Ui.TextMaterial(textMaterial, 3060), 34, 1f, TextAnchor.UpperLeft);
        clock.color = ClockColor;

        for (int i = 0; i < 2; i++)
        {
            holders[i] = new GameObject(i == 0 ? "Pinch Left" : "Pinch Right").transform;
            holders[i].SetParent(head.parent, false);
            dots[i] = Ui.Quad(holders[i], "Dot", panelMaterial, 3061);
            dots[i].Show(false);
        }
    }

    void Update()
    {
        var now = DateTime.Now.ToString("HH:mm");
        if (now != shown) clock.text = shown = now;

        for (int i = 0; i < 2; i++) Pinch(i, i == 0 ? control.LeftHeld : control.RightHeld);
    }

    void Pinch(int i, bool held)
    {
        float t = Time.unscaledTime;
        if (held && !was[i]) since[i] = t;
        if (!held && was[i]) releasedAt[i] = t;
        was[i] = held;

        var at = XrInput.PinchPoint(i == 0, head);
        if (at is Vector3 p) lastAt[i] = p;
        float fade = held ? 1 : 1 - (t - releasedAt[i]) / FadeS;
        if (fade <= 0 || at == null && held)
        {
            dots[i].Show(false);
            return;
        }
        // pops from PopD to HoldD in PopS after the pinch starts, then holds; fades out on release
        float k = Mathf.Clamp01((t - since[i]) / PopS);
        float d = Mathf.Lerp(PopD, HoldD, k * k * (3 - 2 * k));
        var h = holders[i];
        h.position = lastAt[i];
        h.rotation = Quaternion.LookRotation(lastAt[i] - head.position, head.up);
        var c = Ui.Accent;
        dots[i].Set(d, d, d / 2, Vector3.zero, new Color(c.r, c.g, c.b, DotAlpha * Mathf.Clamp01(fade)));
        dots[i].Show(true);
    }
}
