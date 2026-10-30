// Video latency with the real camera: robot/scripts/flash_clock.html flips a screen black/white on exact clock ticks
// and Reachy films it. Here the video is shrunk to 64x36; cells that swing strongly are the screen, wherever it is in
// the picture. Latency = laptop clock when the mean of those cells crosses the middle − the tick of that flip.
// Includes the screen, camera, daemon, encoding, network, jitter buffer, decoding and the texture copy; not rendering
// and display. Same scope as the QR method in xr-client/pages/latency.js. Keep the robot still while measuring.

using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Rendering;

public class FlashLatency : MonoBehaviour
{
    const int W = 64, H = 36;
    const float MinSwing = 80f;     // luminance 0..255: a cell must swing this much to count as the screen
    const float Decay = 0.3f;       // per frame: forget old brightness extremes in ~5 s
    const int MinCells = 8;
    const int Window = 120;         // edges, 60 s

    public RobotVideo video;

    readonly ClockSync clock = new ClockSync();
    readonly float[] hi = new float[W * H], lo = new float[W * H];
    readonly List<double> samples = new List<double>();
    readonly List<double> sorted = new List<double>();
    RenderTexture small;
    bool white, primed;
    int pending, newSinceLog;

    public string Summary { get; private set; } = "";

    void Awake()
    {
        for (int i = 0; i < lo.Length; i++) lo[i] = 255f;
    }

    void OnDestroy()
    {
        if (small != null) small.Release();
    }

    void Update()
    {
        clock.Tick(video.SignallingUrl);
        var tex = video.VideoTexture;
        if (!clock.Ok || tex == null || pending > 2) return;
        if (small == null) small = new RenderTexture(W, H, 0, RenderTextureFormat.ARGB32);
        Graphics.Blit(tex, small);
        long at = clock.SenderNowMs();
        pending++;
        AsyncGPUReadback.Request(small, 0, TextureFormat.RGBA32, r => OnFrame(r, at));
    }

    void OnFrame(AsyncGPUReadbackRequest r, long at)
    {
        pending--;
        if (r.hasError) return;
        var pixels = r.GetData<Color32>();
        float sum = 0;
        int cells = 0;
        for (int i = 0; i < pixels.Length; i++)
        {
            var c = pixels[i];
            float lum = (c.r + c.g + c.b) / 3f;
            hi[i] = Mathf.Max(lum, hi[i] - Decay);
            lo[i] = Mathf.Min(lum, lo[i] + Decay);
            if (hi[i] - lo[i] < MinSwing) continue;
            sum += (lum - lo[i]) / (hi[i] - lo[i]);
            cells++;
        }
        if (cells < MinCells) { Summary = "flash: no flashing screen in view"; primed = false; return; }
        float level = sum / cells;
        if (!primed) { white = level > 0.5f; primed = true; return; }
        if (white ? level > 0.4f : level < 0.6f) return;
        white = !white;

        // White starts at t mod 1000 = 500, black at 0 (flash_clock.html)
        long tick = white ? 500 : 0;
        Add(((at - tick) % 1000 + 1000) % 1000, cells);
    }

    void Add(double latency, int cells)
    {
        samples.Add(latency);
        if (samples.Count > Window) samples.RemoveAt(0);
        sorted.Clear();
        sorted.AddRange(samples);
        sorted.Sort();
        double P(double q) => sorted[(int)System.Math.Round(q * (sorted.Count - 1))];
        Summary = $"flash latency {P(0.5):0} ms (p10 {P(0.1):0} - p90 {P(0.9):0}, n {sorted.Count}, {cells} cells, sync ±{clock.RttMs / 2:0})";
        if (++newSinceLog >= 60)
        {
            newSinceLog = 0;
            Debug.Log($"[flash] median {P(0.5):0} p10 {P(0.1):0} p90 {P(0.9):0} min {sorted[0]:0} max {sorted[^1]:0} " +
                      $"n {sorted.Count} cells {cells} sync ±{clock.RttMs / 2:0}");
        }
    }
}
