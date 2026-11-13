// Connection to the caption backend on the laptop (backend/, port 8766 there, reached through the gateway:
// Endpoints.Backend; protocol in captions.py).
// Binary frames are the room sound; JSON messages wait in a queue for the main thread. Reconnects by itself.
// Out: JSON, and binary frames = the headset mic while push to talk is held (MicSender), for the "You" subtitles
// and for Translate me; one queue, so they keep their order.

using System;
using System.Collections.Concurrent;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;

public class BackendClient : IDisposable
{
    const int ReconnectMs = 2000;

    readonly Func<Endpoints> where;   // asked on every (re)connect: the laptop's address or the token may be new
    Endpoints laptop => where();
    readonly CancellationTokenSource cts = new CancellationTokenSource();
    readonly ConcurrentQueue<string> messages = new ConcurrentQueue<string>();
    readonly ConcurrentQueue<object> outgoing = new ConcurrentQueue<object>();   // string = JSON, byte[] = binary
    readonly string onConnect;

    public AudioJitterBuffer Audio { get; } = new AudioJitterBuffer();
    public bool Connected { get; private set; }
    public string LastError { get; private set; }
    /// <summary>Counts up on every new connection, so settings can be sent again after a reconnect.</summary>
    public int Connects { get; private set; }

    /// <summary>onConnect: JSON sent first on every (re)connect (settings the backend forgets).</summary>
    public BackendClient(Func<Endpoints> laptop, string onConnect = null)
    {
        where = laptop;
        this.onConnect = onConnect;
        _ = Loop();
    }

    public bool TryGetMessage(out string json) => messages.TryDequeue(out json);

    volatile ClientWebSocket socket;

    /// <summary>The laptop is somewhere else now (or the token is new): drop the line, it comes back there.</summary>
    public void Restart()
    {
        try { socket?.Abort(); }
        catch (ObjectDisposedException) { }
    }

    /// <summary>JSON to the backend, sent in order.</summary>
    public void Send(string json) => outgoing.Enqueue(json);

    /// <summary>A binary frame; dropped while disconnected, so no stale audio arrives after a reconnect.</summary>
    public void SendBinary(byte[] data)
    {
        if (Connected) outgoing.Enqueue(data);
    }

    async Task Loop()
    {
        var buffer = new byte[64 * 1024];
        while (!cts.IsCancellationRequested)
        {
            try
            {
                using var ws = new ClientWebSocket();
                socket = ws;
                laptop.Sign(ws);
                await ws.ConnectAsync(laptop.Backend, cts.Token);
                while (outgoing.TryDequeue(out _)) { }   // nothing stale from the last connection
                if (onConnect != null) await SendText(ws, onConnect);
                Connected = true;
                Connects++;
                LastError = null;
                _ = SendLoop(ws);
                var text = new StringBuilder();
                while (ws.State == WebSocketState.Open)
                {
                    var r = await ws.ReceiveAsync(new ArraySegment<byte>(buffer), cts.Token);
                    if (r.MessageType == WebSocketMessageType.Close)
                    {
                        LastError = ws.CloseStatusDescription;
                        break;
                    }
                    if (r.MessageType == WebSocketMessageType.Binary)
                    {
                        Audio.PushPcm16(buffer, r.Count);
                        continue;
                    }
                    text.Append(Encoding.UTF8.GetString(buffer, 0, r.Count));
                    if (!r.EndOfMessage) continue;
                    messages.Enqueue(text.ToString());
                    text.Clear();
                }
            }
            catch (OperationCanceledException) { return; }
            catch (Exception e) { LastError = e.Message; }
            Connected = false;
            try { await Task.Delay(ReconnectMs, cts.Token); }
            catch (OperationCanceledException) { return; }
        }
    }

    async Task SendLoop(ClientWebSocket ws)
    {
        try
        {
            while (ws.State == WebSocketState.Open)
            {
                if (!outgoing.TryDequeue(out var item)) await Task.Delay(20, cts.Token);
                else if (item is string json) await SendText(ws, json);
                else await ws.SendAsync(new ArraySegment<byte>((byte[])item), WebSocketMessageType.Binary, true, cts.Token);
            }
        }
        catch (Exception) { }   // the receive loop sees the closed socket and reconnects
    }

    Task SendText(ClientWebSocket ws, string json) =>
        ws.SendAsync(new ArraySegment<byte>(Encoding.UTF8.GetBytes(json)), WebSocketMessageType.Text, true, cts.Token);

    public void Dispose() => cts.Cancel();
}
