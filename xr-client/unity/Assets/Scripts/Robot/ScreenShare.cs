// The screen someone shares on the laptop next to Reachy, as a second video stream next to the camera
// (robot/scripts/share_screen.py: its own webrtcsink with its own signalling, reached through the gateway at
// Endpoints.Screen). While nobody shares, that address does not answer: this asks again every few seconds, and
// Texture is null. SharePanel shows the picture. Video only; RobotVideo runs the WebRTC update loop for both.
// Only on the laptop's network: the sender has no relay servers (see share_screen.py).

using System.Collections;
using System.Collections.Generic;
using Unity.WebRTC;
using UnityEngine;

public class ScreenShare : MonoBehaviour
{
    const float RetryS = 3f;

    public RobotVideo video;

    /// <summary>The shared screen (a RenderTexture the WebRTC package updates); null while nobody shares.</summary>
    public Texture Texture { get; private set; }
    public string Status => state;

    GstSignalling signalling;
    RTCPeerConnection pc;
    string sessionId, state = "off", logged;
    bool offerIn;
    readonly List<string> mids = new List<string>();
    readonly List<RTCIceCandidateInit> early = new List<RTCIceCandidateInit>();
    float retryAt;

    void Start()
    {
        video.LaptopChanged += Retry;
        Retry();
    }

    void Retry()
    {
        Close();
        retryAt = Time.time + 0.1f;
    }

    void Connect()
    {
        Close();
        if (video.Laptop == null) { retryAt = Time.time + RetryS; return; }
        state = "asking";
        signalling = new GstSignalling(video.Laptop, video.Laptop.Screen);
    }

    void Close()
    {
        pc?.Close();
        pc?.Dispose();
        pc = null;
        sessionId = null;
        Texture = null;
        signalling?.Dispose();
        signalling = null;
    }

    void OnDestroy()
    {
        if (video != null) video.LaptopChanged -= Retry;
        Close();
    }

    void Update()
    {
        if (signalling != null)
        {
            if (signalling.Status == GstSignalling.State.Open && state == "asking")
            {
                state = "signalling open";
                signalling.List();
            }
            for (var m = signalling.Poll(); m != null; m = signalling.Poll()) Handle(m);
            if (signalling != null && signalling.Status == GstSignalling.State.Closed && retryAt == 0)
            {
                // the usual case: nobody shares, the gateway finds nothing behind /screen
                state = Texture != null ? "sharing ended" : "off";
                Close();
                retryAt = Time.time + RetryS;
            }
        }
        if (state != logged && state != "asking") Debug.Log($"[screen] {logged = state}");
        if (retryAt > 0 && Time.time >= retryAt)
        {
            retryAt = 0;
            Connect();
        }
    }

    void Handle(GstSignalling.Message m)
    {
        switch (m.type)
        {
            case "list":
                if (m.producers == null || m.producers.Length == 0) { retryAt = Time.time + RetryS; return; }
                state = "found";
                signalling.StartSession(m.producers[0].id);   // this signalling server has the screen and nothing else
                break;
            case "sessionStarted":
                sessionId = m.sessionId;
                CreatePeerConnection();
                break;
            case "peer" when m.HasSdp:
                StartCoroutine(Answer(m.sdp.sdp));
                break;
            case "peer" when m.HasIce:
                var candidate = new RTCIceCandidateInit { candidate = m.ice.candidate, sdpMLineIndex = m.ice.sdpMLineIndex };
                if (offerIn) AddCandidate(candidate);   // before the offer is in, the peer connection throws it away
                else early.Add(candidate);
                break;
            case "endSession":
                state = "sharing ended";
                Close();
                retryAt = Time.time + RetryS;
                break;
            case "error":
                state = $"signalling error: {m.details}";
                break;
        }
    }

    void CreatePeerConnection()
    {
        var config = new RTCConfiguration
        {
            iceServers = new[] { new RTCIceServer { urls = new[] { "stun:stun.l.google.com:19302" } } },
        };
        pc = new RTCPeerConnection(ref config);
        offerIn = false;
        early.Clear();
        pc.OnIceCandidate = c => signalling?.SendIce(sessionId, c.Candidate, c.SdpMLineIndex ?? 0);
        pc.OnIceConnectionChange = s =>
        {
            state = $"ice {s}";
            if (s == RTCIceConnectionState.Failed || s == RTCIceConnectionState.Closed) Retry();
        };
        pc.OnTrack = e =>
        {
            if (e.Track is VideoStreamTrack track)
                track.OnVideoReceived += tex => { Texture = tex; state = $"showing {tex.width}x{tex.height}"; };
        };
    }

    IEnumerator Answer(string offerSdp)
    {
        var mine = pc;
        var offer = new RTCSessionDescription { type = RTCSdpType.Offer, sdp = offerSdp };
        var setRemote = mine.SetRemoteDescription(ref offer);
        yield return setRemote;
        if (pc != mine) yield break;   // closed meanwhile
        if (setRemote.IsError) { state = $"setRemote: {setRemote.Error.message}"; yield break; }
        mids.Clear();
        foreach (var line in offerSdp.Split('\n'))
            if (line.StartsWith("a=mid:")) mids.Add(line.Substring(6).Trim());
        offerIn = true;
        foreach (var candidate in early) AddCandidate(candidate);
        early.Clear();
        var create = mine.CreateAnswer();
        yield return create;
        if (pc != mine) yield break;
        if (create.IsError) { state = $"createAnswer: {create.Error.message}"; yield break; }
        var answer = create.Desc;
        var setLocal = mine.SetLocalDescription(ref answer);
        yield return setLocal;
        if (pc != mine) yield break;
        if (setLocal.IsError) { state = $"setLocal: {setLocal.Error.message}"; yield break; }
        signalling.SendAnswer(sessionId, answer.sdp);
        state = "answer sent";
    }

    void AddCandidate(RTCIceCandidateInit init)
    {
        if (pc == null) return;
        // webrtcsink names the media line only by its number; this peer connection wants its name (a=mid) too
        if (init.sdpMLineIndex is int index && index >= 0 && index < mids.Count) init.sdpMid = mids[index];
        try { pc.AddIceCandidate(new RTCIceCandidate(init)); }
        catch (System.Exception e) { Debug.LogWarning($"[screen] candidate refused: {e.Message}"); }
    }
}
