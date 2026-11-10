// Connection to the caption backend on the laptop (backend/, ws://<laptop>:8766, protocol in captions.py).
// Binary frames are the room sound; JSON messages wait in a queue for the main thread. Reconnects by itself.
// Out: JSON, and binary frames = the headset mic while push to talk is held (MicSender), for the "You" subtitles
// and for Translate me; one queue, so they keep their order.
//
// Address and secret are set once and remembered on the headset:
//   adb shell am start -n ch.ethz.reachymeetings/com.unity3d.player.UnityPlayerGameActivity -e secret <lan-secret>
// The host defaults to the signalling host, port 8766.

using System;
using System.Collections.Concurrent;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;

public class BackendClient : IDisposable
{
    [Serializable] class AuthMsg { public string type = "auth"; public string secret; }

    const int ReconnectMs = 2000;

    readonly Uri uri;
    readonly string secret;
    readonly CancellationTokenSource cts = new CancellationTokenSource();
    readonly ConcurrentQueue<string> messages = new ConcurrentQueue<string>();
    readonly ConcurrentQueue<object> outgoing = new ConcurrentQueue<object>();   // string = JSON, byte[] = binary
    readonly string onConnect;

    public AudioJitterBuffer Audio { get; } = new AudioJitterBuffer();
    public bool Connected { get; private set; }
    public string LastError { get; private set; }
    /// <summary>Counts up on every new connection, so settings can be sent again after a reconnect.</summary>
    public int Connects { get; private set; }

    /// <summary>onConnect: JSON sent after the sign-in on every (re)connect (settings the backend forgets).</summary>
    public BackendClient(string host, string secret, string onConnect = null)
    {
        uri = new Uri($"ws://{host}:8766");
        this.secret = secret;
        this.onConnect = onConnect;
        _ = Loop();
    }

    public bool TryGetMessage(out string json) => messages.TryDequeue(out json);

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
                await ws.ConnectAsync(uri, cts.Token);
                if (!string.IsNullOrEmpty(secret))
                    await SendText(ws, JsonUtility.ToJson(new AuthMsg { secret = secret }));
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
