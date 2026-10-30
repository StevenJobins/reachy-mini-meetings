// Video latency against robot/scripts/fake_camera.py: each frame carries the sender's clock as a time strip
// (44 blocks of 40 px along the top of the 1920-wide frame, MSB first, white = 1, Unix ms mod 2^44).
// Latency = sender clock when this headset first has the frame in its texture − time in the strip.
// Includes encoding, network, jitter buffer, decoding and the texture copy; not rendering and display
// (add ~1 frame, 14 ms at 72 Hz, for glass to glass). Same definition as xr-client/pages/latency.js.
// Clocks: ClockSync.cs.

using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Rendering;

public class VideoLatency : MonoBehaviour
{
    const int Bits = 44;
    const long Modulus = 1L << Bits;
    const float StripBlock = 40f / 1920f;   // block width as a fraction of the frame width
    const float StripRow = 20f / 1080f;     // block centre row as a fraction of the frame height
    const int Window = 300;

    public RobotVideo video;

    readonly List<double> samples = new List<double>();
    readonly List<double> sorted = new List<double>();
    long lastValue = -1;
    int pending, newSinceLog;
    readonly ClockSync clock = new ClockSync();
    int rowFromTop = -1; // which readback row holds the strip (texture orientation), found on the first match

    public string Summary { get; private set; } = "";

    void Update()
    {
        clock.Tick(video.SignallingUrl);
        var tex = video.VideoTexture;
        if (!clock.Ok || tex == null || pending > 2) return;

        long at = clock.SenderNowMs();
        int y = Mathf.RoundToInt(StripRow * tex.height);
        foreach (var fromTop in new[] { true, false })
        {
            if (rowFromTop >= 0 && fromTop != (rowFromTop == 1)) continue;
            pending++;
            var top = fromTop;
            AsyncGPUReadback.Request(tex, 0, 0, tex.width, top ? tex.height - 1 - y : y, 1, 0, 1,
                TextureFormat.RGBA32, r => OnRow(r, at, top));
        }
    }

    void OnRow(AsyncGPUReadbackRequest r, long at, bool fromTop)
    {
        pending--;
        if (r.hasError) return;
        var row = r.GetData<Color32>();
        long value = 0;
        for (int i = 0; i < Bits; i++)
        {
            var c = row[Mathf.Clamp(Mathf.RoundToInt((i + 0.5f) * StripBlock * row.Length), 0, row.Length - 1)];
            value = (value << 1) | ((c.r + c.g + c.b) > 3 * 127 ? 1L : 0L);
        }
        double latency = ((at % Modulus) - value + Modulus) % Modulus;
        if (latency > 5000) return;                    // not a strip (other row, no fake camera) or garbage
        rowFromTop = fromTop ? 1 : 0;
        if (value == lastValue) return;                // same frame as before: only count new frames
        lastValue = value;

        samples.Add(latency);
        if (samples.Count > Window) samples.RemoveAt(0);
        sorted.Clear();
        sorted.AddRange(samples);
        sorted.Sort();
        double P(double q) => sorted[(int)Math.Round(q * (sorted.Count - 1))];
        Summary = $"latency {P(0.5):0} ms (p10 {P(0.1):0} - p90 {P(0.9):0}, n {sorted.Count}, sync ±{clock.RttMs / 2:0})";
        if (++newSinceLog >= Window)
        {
            newSinceLog = 0;
            Debug.Log($"[latency] median {P(0.5):0} p10 {P(0.1):0} p90 {P(0.9):0} min {sorted[0]:0} max {sorted[^1]:0} " +
                      $"n {sorted.Count} sync ±{clock.RttMs / 2:0}");
        }
    }
}
