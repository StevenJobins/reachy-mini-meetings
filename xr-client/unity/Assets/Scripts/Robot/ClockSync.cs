// Clock offset to the laptop over ws://<laptop>:8444 (any message, reply = its Unix time in ms). Best of 12 round
// trips, like clocksync.js; the laptop side is robot/scripts/fake_camera.py or flash_clock.py. Repeats every 60 s.

using System;
using System.Diagnostics;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Debug = UnityEngine.Debug;

public class ClockSync
{
    const int ResyncMs = 60000;

    long offsetMs, nextSyncMs;
    volatile bool busy, ok;

    public bool Ok => ok;
    public double RttMs { get; private set; }

    static long LocalMs() => Stopwatch.GetTimestamp() * 1000 / Stopwatch.Frequency;

    /// <summary>The laptop's clock now, in Unix ms.</summary>
    public long SenderNowMs() => LocalMs() + offsetMs;

    /// <summary>Call every frame; syncs when due.</summary>
    public void Tick(string signallingUrl)
    {
        if (busy || string.IsNullOrEmpty(signallingUrl) || LocalMs() < nextSyncMs) return;
        busy = true;
        nextSyncMs = LocalMs() + ResyncMs;
        _ = Sync(new UriBuilder(signallingUrl) { Port = 8444 }.Uri);
    }

    async Task Sync(Uri uri)
    {
        try
        {
            using var ws = new ClientWebSocket();
            using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
            await ws.ConnectAsync(uri, cts.Token);
            var buffer = new byte[64];
            double bestRtt = double.MaxValue;
            long bestOffset = 0;
            for (int i = 0; i < 12; i++)
            {
                long t0 = LocalMs();
                await ws.SendAsync(new ArraySegment<byte>(Encoding.ASCII.GetBytes("t")), WebSocketMessageType.Text, true, cts.Token);
                var r = await ws.ReceiveAsync(new ArraySegment<byte>(buffer), cts.Token);
                long t1 = LocalMs();
                long sender = long.Parse(Encoding.ASCII.GetString(buffer, 0, r.Count));
                if (t1 - t0 < bestRtt) { bestRtt = t1 - t0; bestOffset = sender - (t0 + t1) / 2; }
            }
            offsetMs = bestOffset;
            RttMs = bestRtt;
            ok = true;
        }
        catch (Exception e)
        {
            Debug.LogWarning($"[clock] sync with {uri}: {e.Message}");
            nextSyncMs = LocalMs() + 5000;
        }
        busy = false;
    }
}
