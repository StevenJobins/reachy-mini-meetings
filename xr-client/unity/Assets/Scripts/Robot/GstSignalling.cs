// Client for GStreamer webrtcsink's signalling server (the daemon's ws://<robot-laptop>:8443), consumer side.
// Same protocol as xr-client/web/index.html:
//   -> {type:"list"}                               <- {type:"list", producers:[{id, meta:{name}}]}
//   -> {type:"startSession", peerId}               <- {type:"sessionStarted", peerId, sessionId}
//   <- {type:"peer", sessionId, sdp:{type:"offer", sdp}}   -> {type:"peer", sessionId, sdp:{type:"answer", sdp}}
//   <-> {type:"peer", sessionId, ice:{candidate, sdpMLineIndex}}
//   <- {type:"endSession"}, {type:"error", details}
// No Unity rendering in here. Messages arrive on a background thread and are queued; Poll() hands them to the
// caller on the main thread.

using System;
using System.Collections.Concurrent;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;

public class GstSignalling : IDisposable
{
    [Serializable] public class Meta { public string name; }
    [Serializable] public class Producer { public string id; public Meta meta; }
    [Serializable] public class Sdp { public string type; public string sdp; }
    [Serializable] public class Ice { public string candidate; public int sdpMLineIndex; }

    /// <summary>Union of all incoming message types; fields a message does not have stay empty.</summary>
    [Serializable]
    public class Message
    {
        public string type;
        public Producer[] producers;
        public string peerId;
        public string sessionId;
        public Sdp sdp;
        public Ice ice;
        public string details;

        public bool HasSdp => sdp != null && !string.IsNullOrEmpty(sdp.sdp);
        public bool HasIce => ice != null && !string.IsNullOrEmpty(ice.candidate);
    }

    [Serializable] class TypeOnly { public string type; }
    [Serializable] class StartSessionMsg { public string type = "startSession"; public string peerId; }
    [Serializable] class PeerSdp { public string type = "peer"; public string sessionId; public Sdp sdp; }
    [Serializable] class PeerIce { public string type = "peer"; public string sessionId; public Ice ice; }

    public enum State { Connecting, Open, Closed }

    readonly ClientWebSocket ws = new ClientWebSocket();
    readonly CancellationTokenSource cts = new CancellationTokenSource();
    readonly ConcurrentQueue<Message> inbox = new ConcurrentQueue<Message>();
    readonly SemaphoreSlim sendLock = new SemaphoreSlim(1, 1);

    public State Status { get; private set; } = State.Connecting;
    public string LastError { get; private set; }

    public GstSignalling(string url)
    {
        _ = Run(new Uri(url));
    }

    async Task Run(Uri uri)
    {
        try
        {
            await ws.ConnectAsync(uri, cts.Token);
            Status = State.Open;
            var buffer = new byte[64 * 1024];
            var text = new StringBuilder();
            while (ws.State == WebSocketState.Open)
            {
                var r = await ws.ReceiveAsync(new ArraySegment<byte>(buffer), cts.Token);
                if (r.MessageType == WebSocketMessageType.Close) break;
                text.Append(Encoding.UTF8.GetString(buffer, 0, r.Count));
                if (!r.EndOfMessage) continue;
                inbox.Enqueue(JsonUtility.FromJson<Message>(text.ToString()));
                text.Clear();
            }
        }
        catch (Exception e) when (!(e is OperationCanceledException))
        {
            LastError = e.Message;
        }
        Status = State.Closed;
    }

    /// <summary>Next received message, or null. Call from the main thread.</summary>
    public Message Poll() => inbox.TryDequeue(out var m) ? m : null;

    public void List() => Send(new TypeOnly { type = "list" });
    public void StartSession(string producerId) => Send(new StartSessionMsg { peerId = producerId });
    public void SendAnswer(string sessionId, string sdp) =>
        Send(new PeerSdp { sessionId = sessionId, sdp = new Sdp { type = "answer", sdp = sdp } });
    public void SendIce(string sessionId, string candidate, int sdpMLineIndex) =>
        Send(new PeerIce { sessionId = sessionId, ice = new Ice { candidate = candidate, sdpMLineIndex = sdpMLineIndex } });

    void Send(object message)
    {
        var bytes = Encoding.UTF8.GetBytes(JsonUtility.ToJson(message));
        _ = SendAsync(bytes);
    }

    async Task SendAsync(byte[] bytes)
    {
        await sendLock.WaitAsync();
        try
        {
            if (ws.State == WebSocketState.Open)
                await ws.SendAsync(new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, cts.Token);
        }
        catch (Exception e) { LastError = e.Message; }
        finally { sendLock.Release(); }
    }

    public void Dispose()
    {
        cts.Cancel();
        ws.Dispose();
    }
}
