// Robot camera on the video window: webrtcsink signalling on the LAN (GstSignalling) -> WebRTC -> texture.
// Phase 1 spike: no HF sign-in / Pollen central, the headset talks to the daemon on the robot laptop directly.
//
// Signalling URL: set once with an intent extra, then remembered on the headset:
//   adb shell am start -n ch.ethz.reachymeetings/com.unity3d.player.UnityPlayerGameActivity -e signalling ws://<laptop-ip>:8443

using System.Collections;
using Unity.WebRTC;
using UnityEngine;

public class RobotVideo : MonoBehaviour
{
    const string UrlKey = "signalling";
    const float RetryS = 2f;
    // Camera exposure/readout + encoding + network, the part the stats do not show. From the flash test: 141 ms total
    // minus jitter buffer and decode (41 ms) minus ~20 ms for the test screen, minus ~10 ms the pose stream itself is
    // late. Override for tests: adb ... -e capturems 70
    const double CaptureDefaultMs = 70;

    public Renderer window;
    public FpsLabel hud;
    public VideoLatency latency;
    public FlashLatency flash;
    public RobotControl control;
    public MicSender mic;
    public RoomSound room;
    public CaptionView captions;
    public RoomPanorama panorama;
    public VideoWindow view;

    public string SignallingUrl => url;
    /// <summary>Age of the shown frame relative to the pose stream, live from the WebRTC stats (see CaptureDefaultMs).</summary>
    public double VideoDelayS { get; private set; } = 0.12;
    /// <summary>The received video (a RenderTexture the WebRTC package updates), null until the first frame.</summary>
    public Texture VideoTexture { get; private set; }

    string url;
    GstSignalling signalling;
    RTCPeerConnection pc;
    string sessionId;
    string state = "starting";
    string stats = "";
    float retryAt;
    double captureS = CaptureDefaultMs / 1000;
    bool keepAudio;      // adb ... -e audio on: accept the robot's audio track (for A/B tests of the video delay)
    int statsLogged;

    // previous GetStats() sample, for rates
    double lastMicT, lastMicBytes;
    double lastStatsT, lastBytes, lastJbDelay, lastJbCount, lastDecodeS, lastDecoded;

    void Start()
    {
        keepAudio = ReadIntentExtra("audio") == "on";
        var showHud = ReadIntentExtra("hud");
        if (!string.IsNullOrEmpty(showHud)) { PlayerPrefs.SetString("hud", showHud); PlayerPrefs.Save(); }
        if (hud != null) hud.SetVisible(PlayerPrefs.GetString("hud", "on") != "off");
        if (double.TryParse(ReadIntentExtra("capturems"), System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture, out var ms)) captureS = ms / 1000;
        url = ReadIntentExtra(UrlKey);
        if (!string.IsNullOrEmpty(url)) { PlayerPrefs.SetString(UrlKey, url); PlayerPrefs.Save(); }
        else url = PlayerPrefs.GetString(UrlKey, "");

        StartCoroutine(WebRTC.Update());
        StartCoroutine(PollStats());
        if (string.IsNullOrEmpty(url)) state = "no signalling URL (adb ... -e signalling ws://<laptop-ip>:8443)";
        else Connect();
    }

    void Connect()
    {
        Close();
        state = $"connecting {url}";
        signalling = new GstSignalling(url);
    }

    void Close()
    {
        pc?.Close();
        pc?.Dispose();
        pc = null;
        sessionId = null;
        VideoTexture = null;
        signalling?.Dispose();
        signalling = null;
    }

    void OnDestroy() => Close();

    void Update()
    {
        if (signalling != null)
        {
            if (signalling.Status == GstSignalling.State.Open && state.StartsWith("connecting"))
            {
                state = "signalling open";
                signalling.List();
            }
            for (var m = signalling.Poll(); m != null; m = signalling.Poll()) Handle(m);
            if (signalling.Status == GstSignalling.State.Closed && retryAt == 0)
            {
                state = $"signalling closed {signalling.LastError}";
                retryAt = Time.time + RetryS;
            }
        }
        if (retryAt > 0 && Time.time >= retryAt)
        {
            retryAt = 0;
            Connect();
        }
        if (hud != null) hud.extra = $"{state}\n{stats}\n{latency?.Summary}{flash?.Summary}\n{control?.Summary}  {mic?.Status}\n{view?.Summary}  {panorama?.Status}\n{room?.Status}  {captions?.Status}";
    }

    void Handle(GstSignalling.Message m)
    {
        switch (m.type)
        {
            case "list":
                var producer = System.Array.Find(m.producers, p => p.meta?.name == "reachymini") ??
                               (m.producers.Length > 0 ? m.producers[0] : null);
                if (producer == null)
                {
                    state = "no producer yet (daemon running?)";
                    retryAt = Time.time + RetryS;
                    return;
                }
                state = $"producer {producer.id.Substring(0, 8)}";
                signalling.StartSession(producer.id);
                break;
            case "sessionStarted":
                sessionId = m.sessionId;
                CreatePeerConnection();
                break;
            case "peer" when m.HasSdp:
                StartCoroutine(Answer(m.sdp.sdp));
                break;
            case "peer" when m.HasIce:
                pc?.AddIceCandidate(new RTCIceCandidate(new RTCIceCandidateInit
                {
                    candidate = m.ice.candidate,
                    sdpMLineIndex = m.ice.sdpMLineIndex,
                }));
                break;
            case "endSession":
                state = "session ended by producer";
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
        pc.OnIceCandidate = c => signalling?.SendIce(sessionId, c.Candidate, c.SdpMLineIndex ?? 0);
        pc.OnIceConnectionChange = s =>
        {
            state = $"ice {s}";
            if (s == RTCIceConnectionState.Failed || s == RTCIceConnectionState.Closed) retryAt = Time.time + RetryS;
        };
        pc.OnTrack = e =>
        {
            if (e.Track is VideoStreamTrack video)
                video.OnVideoReceived += tex => window.material.mainTexture = VideoTexture = tex;
        };
    }

    IEnumerator Answer(string offerSdp)
    {
        var offer = new RTCSessionDescription { type = RTCSdpType.Offer, sdp = offerSdp };
        var setRemote = pc.SetRemoteDescription(ref offer);
        yield return setRemote;
        if (setRemote.IsError) { state = $"setRemote: {setRemote.Error.message}"; yield break; }
        // Nothing plays the robot's WebRTC audio here, and libwebrtc holds video back to stay in sync with it
        if (!keepAudio)
            foreach (var t in pc.GetTransceivers())
                if (t.Receiver.Track?.Kind == TrackKind.Audio) mic.Attach(t);
        var create = pc.CreateAnswer();
        yield return create;
        if (create.IsError) { state = $"createAnswer: {create.Error.message}"; yield break; }
        var answer = create.Desc;
        var setLocal = pc.SetLocalDescription(ref answer);
        yield return setLocal;
        if (setLocal.IsError) { state = $"setLocal: {setLocal.Error.message}"; yield break; }
        signalling.SendAnswer(sessionId, answer.sdp);
        state = "answer sent";
    }

    /// <summary>Incoming video once per second: size, fps, codec + decoder, bitrate, jitter buffer and decode time.</summary>
    IEnumerator PollStats()
    {
        while (true)
        {
            yield return new WaitForSeconds(1f);
            if (pc == null) { stats = ""; continue; }
            var op = pc.GetStats();
            yield return op;
            if (op.IsError) continue;
            foreach (var s in op.Value.Stats.Values)
            {
                if (s is RTCOutboundRTPStreamStats o && o.kind == "audio")
                {
                    double at = o.Timestamp / 1e6;
                    if (at > lastMicT && lastMicT > 0) mic.Kbps = (o.bytesSent - lastMicBytes) * 8 / (at - lastMicT) / 1000;
                    lastMicT = at; lastMicBytes = o.bytesSent;
                }
                if (!(s is RTCInboundRTPStreamStats v) || v.kind != "video") continue;
                var codec = op.Value.Stats.TryGetValue(v.codecId ?? "", out var c) ? ((RTCCodecStats)c).mimeType : "?";
                double t = v.Timestamp / 1e6, dt = t - lastStatsT;
                double jbCount = v.jitterBufferEmittedCount - lastJbCount, decoded = v.framesDecoded - lastDecoded;
                var mbps = dt > 0 ? (v.bytesReceived - lastBytes) * 8 / dt / 1e6 : 0;
                var jbMs = jbCount > 0 ? (v.jitterBufferDelay - lastJbDelay) / jbCount * 1000 : 0;
                var decMs = decoded > 0 ? (v.totalDecodeTime - lastDecodeS) / decoded * 1000 : 0;
                if (jbCount > 0 && decoded > 0) VideoDelayS += 0.3 * ((jbMs + decMs) / 1000 + captureS - VideoDelayS);
                stats = $"{v.frameWidth}x{v.frameHeight} {v.framesPerSecond:0}fps {mbps:0.0}Mbps {codec} {v.decoderImplementation}\n" +
                        $"jitter buf {jbMs:0}ms  decode {decMs:0.0}ms  dropped {v.framesDropped}";
                if (++statsLogged % 5 == 0) Debug.Log($"[stats] audio {(keepAudio ? "on" : "off")} {stats.Replace('\n', ' ')}");
                lastStatsT = t; lastBytes = v.bytesReceived; lastJbDelay = v.jitterBufferDelay;
                lastJbCount = v.jitterBufferEmittedCount; lastDecodeS = v.totalDecodeTime; lastDecoded = v.framesDecoded;
            }
        }
    }

    public static string ReadIntentExtra(string key)
    {
#if UNITY_ANDROID && !UNITY_EDITOR
        try
        {
            using var player = new AndroidJavaClass("com.unity3d.player.UnityPlayer");
            using var activity = player.GetStatic<AndroidJavaObject>("currentActivity");
            using var intent = activity.Call<AndroidJavaObject>("getIntent");
            return intent.Call<string>("getStringExtra", key);
        }
        catch (System.Exception e) { Debug.LogWarning($"intent extra {key}: {e.Message}"); }
#endif
        return null;
    }
}
