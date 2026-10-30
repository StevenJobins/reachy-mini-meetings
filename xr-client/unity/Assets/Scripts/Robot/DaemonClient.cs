// Direct line to the Reachy daemon on the robot laptop (port 8000), no helper process on the laptop:
//   ws  /api/move/ws/set_target   head + body targets (radians), latest value wins, nothing queues up
//   ws  /api/state/ws/full        measured pose
//   http POST for wake-up and sleep (RobotControl)
// Both sockets reconnect by themselves. No rendering in here.

using System;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Globalization;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;
using Debug = UnityEngine.Debug;

public class DaemonClient : IDisposable
{
    [Serializable] public class Rpy { public double roll, pitch, yaw; }
    [Serializable]
    public class State
    {
        public string control_mode;
        public Rpy head_pose;
        public double body_yaw;
        public string timestamp;                  // when the daemon read it, ISO 8601 UTC
        [NonSerialized] public double arrivedS;   // NowS() when it arrived
        [NonSerialized] public double senderS;    // timestamp in seconds, 0 if the daemon sent none

        public RobotAngles Head => new RobotAngles(head_pose.roll * PoseMath.Deg, head_pose.pitch * PoseMath.Deg, head_pose.yaw * PoseMath.Deg);
    }

    const int ReconnectMs = 1000;

    readonly string host;
    readonly CancellationTokenSource cts = new CancellationTokenSource();
    readonly SemaphoreSlim wake = new SemaphoreSlim(0);
    readonly ConcurrentQueue<State> received = new ConcurrentQueue<State>();
    string pending;
    volatile State state;

    /// <summary>Monotonic clock for pairing poses with video, also usable off the main thread.</summary>
    public static double NowS() => (double)Stopwatch.GetTimestamp() / Stopwatch.Frequency;

    public string HttpBase => $"http://{host}:8000";
    public bool TargetConnected { get; private set; }
    /// <summary>Latest measured state (angles in radians), null until the first message.</summary>
    public State Latest => state;
    public string LastError { get; private set; }
    /// <summary>Every state as it arrived; the view needs all of them, not just the latest of each rendered frame.</summary>
    public bool TryDequeueState(out State s) => received.TryDequeue(out s);

    public DaemonClient(string host)
    {
        this.host = host;
        _ = TargetLoop();
        _ = StateLoop();
    }

    /// <summary>Queue a target in degrees; replaces one that has not gone out yet.</summary>
    public void SendTarget(HeadTarget t)
    {
        pending = t.ToDaemonJson();
        if (wake.CurrentCount == 0) wake.Release();
    }

    async Task TargetLoop()
    {
        while (!cts.IsCancellationRequested)
        {
            try
            {
                using var ws = new ClientWebSocket();
                await ws.ConnectAsync(new Uri($"ws://{host}:8000/api/move/ws/set_target"), cts.Token);
                TargetConnected = true;
                _ = ReadReplies(ws);
                while (ws.State == WebSocketState.Open)
                {
                    await wake.WaitAsync(cts.Token);
                    var message = Interlocked.Exchange(ref pending, null);
                    if (message == null) continue;
                    var bytes = Encoding.UTF8.GetBytes(message);
                    await ws.SendAsync(new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, cts.Token);
                }
            }
            catch (OperationCanceledException) { return; }
            catch (Exception e) { LastError = e.Message; }
            TargetConnected = false;
            await Delay();
        }
    }

    /// <summary>The daemon only answers set_target with errors, e.g. a message it cannot parse.</summary>
    async Task ReadReplies(ClientWebSocket ws)
    {
        var buffer = new byte[4 * 1024];
        try
        {
            while (ws.State == WebSocketState.Open)
            {
                var r = await ws.ReceiveAsync(new ArraySegment<byte>(buffer), cts.Token);
                if (r.MessageType == WebSocketMessageType.Close) return;
                LastError = Encoding.UTF8.GetString(buffer, 0, r.Count);
                Debug.LogWarning($"[control] daemon: {LastError}");
            }
        }
        catch (Exception) { }   // the send loop sees the closed socket and reconnects
    }

    async Task StateLoop()
    {
        var uri = new Uri($"ws://{host}:8000/api/state/ws/full?frequency=60&with_head_pose=true&with_body_yaw=true&with_antenna_positions=false");
        var buffer = new byte[16 * 1024];
        while (!cts.IsCancellationRequested)
        {
            try
            {
                using var ws = new ClientWebSocket();
                await ws.ConnectAsync(uri, cts.Token);
                var text = new StringBuilder();
                while (ws.State == WebSocketState.Open)
                {
                    var r = await ws.ReceiveAsync(new ArraySegment<byte>(buffer), cts.Token);
                    if (r.MessageType == WebSocketMessageType.Close) break;
                    text.Append(Encoding.UTF8.GetString(buffer, 0, r.Count));
                    if (!r.EndOfMessage) continue;
                    var s = JsonUtility.FromJson<State>(text.ToString());
                    s.arrivedS = NowS();
                    if (DateTime.TryParse(s.timestamp, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out var stamp))
                        s.senderS = stamp.ToUniversalTime().Ticks / (double)TimeSpan.TicksPerSecond;
                    state = s;
                    if (s.head_pose != null) received.Enqueue(s);
                    text.Clear();
                }
            }
            catch (OperationCanceledException) { return; }
            catch (Exception e) { LastError = e.Message; }
            await Delay();
        }
    }

    async Task Delay()
    {
        try { await Task.Delay(ReconnectMs, cts.Token); }
        catch (OperationCanceledException) { }
    }

    public void Dispose() => cts.Cancel();
}
