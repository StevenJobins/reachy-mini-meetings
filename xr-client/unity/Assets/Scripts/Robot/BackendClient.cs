// Connection to the caption backend on the laptop (backend/, ws://<laptop>:8766, protocol in captions.py).
// Binary frames are the room sound; JSON messages wait in a queue for the main thread. Reconnects by itself.
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
    readonly ConcurrentQueue<string> outgoing = new ConcurrentQueue<string>();

    public AudioJitterBuffer Audio { get; } = new AudioJitterBuffer();
    public bool Connected { get; private set; }
    public string LastError { get; private set; }

    public BackendClient(string host, string secret)
    {
        uri = new Uri($"ws://{host}:8766");
        this.secret = secret;
        _ = Loop();
    }

    public bool TryGetMessage(out string json) => messages.TryDequeue(out json);

    /// <summary>JSON to the backend, sent in order.</summary>
    public void Send(string json) => outgoing.Enqueue(json);

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
                Connected = true;
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
                if (outgoing.TryDequeue(out var json)) await SendText(ws, json);
                else await Task.Delay(20, cts.Token);
            }
        }
        catch (Exception) { }   // the receive loop sees the closed socket and reconnects
    }

    Task SendText(ClientWebSocket ws, string json) =>
        ws.SendAsync(new ArraySegment<byte>(Encoding.UTF8.GetBytes(json)), WebSocketMessageType.Text, true, cts.Token);

    public void Dispose() => cts.Cancel();
}
