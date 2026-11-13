// Robot camera on the video window: webrtcsink signalling (GstSignalling) -> WebRTC -> texture.
// No HF sign-in / Pollen central: the headset talks to the laptop next to Reachy through its gateway
// (robot/scripts/gateway.py, Endpoints.cs); the video itself goes straight between the two.
//
// The laptop's address and this headset's token are remembered on the headset. They come from the pairing pad
// (Pairing.cs: the six-digit code the laptop shows), or once from intent extras; on the laptop,
// `python3 robot/scripts/gateway.py add <name>` prints the line:
//   adb shell am start -n ch.ethz.reachymeetings/com.unity3d.player.UnityPlayerGameActivity -e gateway ws://<laptop-ip>:8790 -e token <token>

using System.Collections;
using Unity.WebRTC;
using UnityEngine;

public class RobotVideo : MonoBehaviour
{
    const string GatewayKey = "gateway", TokenKey = "token", LaptopKey = "laptop";
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

    /// <summary>Where the laptop next to Reachy is reached; null while this headset is not set up for one.</summary>
    public Endpoints Laptop { get; private set; }
    /// <summary>Which laptop that is (its own id, from pairing), to know it again at a new address; "" if unknown.</summary>
    public string LaptopId { get; private set; } = "";

    /// <summary>Where the laptop posts its public address for this headset (RemoteAddress), and what a notice
    /// found there says: an address only if this headset's laptop signed it.</summary>
    public string AddressBoardUrl => RemoteAddress.LookupUrl(PlayerPrefs.GetString(TokenKey, ""));
    public string ReadNotice(string notice) =>
        RemoteAddress.Read(notice, PlayerPrefs.GetString(TokenKey, ""), System.DateTimeOffset.UtcNow.ToUnixTimeSeconds());

    /// <summary>After SetLaptop: whoever holds a line to the laptop drops it and connects again.</summary>
    public event System.Action LaptopChanged;

    /// <summary>Paired with a laptop, or found the known one at a new address (token null: keep the one we have).
    /// Remembered, and every connection starts again there.</summary>
    public void SetLaptop(string gateway, string token, string id)
    {
        var laptop = Endpoints.From(gateway, token ?? PlayerPrefs.GetString(TokenKey, ""));
        if (laptop == null) return;
        PlayerPrefs.SetString(GatewayKey, gateway);
        if (token != null) PlayerPrefs.SetString(TokenKey, token);
        PlayerPrefs.SetString(LaptopKey, id ?? "");
        PlayerPrefs.Save();
        Laptop = laptop;
        LaptopId = id ?? "";
        retryAt = 0;
        Connect();
        LaptopChanged?.Invoke();
    }
    /// <summary>Age of the shown frame relative to the pose stream, live from the WebRTC stats (see CaptureDefaultMs).</summary>
    public double VideoDelayS { get; private set; } = 0.12;
    /// <summary>The received video (a RenderTexture the WebRTC package updates), null until the first frame.</summary>
    public Texture VideoTexture { get; private set; }

    GstSignalling signalling;
    string logged;
    bool offerIn;   // the remote description is set: candidates can go to the peer connection
    readonly System.Collections.Generic.List<string> mids = new System.Collections.Generic.List<string>();   // the offer's media lines, in order
    readonly System.Collections.Generic.List<RTCIceCandidateInit> early = new System.Collections.Generic.List<RTCIceCandidateInit>();
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
        // The status text is for development: off unless asked for once (adb ... -e hud on; -e hud off hides it again)
        if (hud != null) hud.SetVisible(PlayerPrefs.GetString("hud", "off") == "on");
        if (double.TryParse(ReadIntentExtra("capturems"), System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture, out var ms)) captureS = ms / 1000;
        // an address given by hand may be another laptop: which one it is, is not known then
        if (!string.IsNullOrEmpty(ReadIntentExtra(GatewayKey))) PlayerPrefs.DeleteKey(LaptopKey);
        Laptop = Endpoints.From(Setting(GatewayKey), Setting(TokenKey));
        LaptopId = PlayerPrefs.GetString(LaptopKey, "");

        StartCoroutine(WebRTC.Update());
        StartCoroutine(PollStats());
        if (Laptop == null) state = "not set up (enter the code the laptop shows)";
        else Connect();
    }

    /// <summary>A setting given once on the `am start` line and remembered from then on.</summary>
    static string Setting(string key)
    {
        var given = ReadIntentExtra(key);
        if (!string.IsNullOrEmpty(given)) { PlayerPrefs.SetString(key, given); PlayerPrefs.Save(); }
        return PlayerPrefs.GetString(key, "");
    }

    void Connect()
    {
        Close();
        state = $"connecting {Laptop.Signalling}";
        signalling = new GstSignalling(Laptop);
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
            if (state != logged) Debug.Log($"[video] {logged = state}");   // every step of connecting, also without the status text
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
                var candidate = new RTCIceCandidateInit { candidate = m.ice.candidate, sdpMLineIndex = m.ice.sdpMLineIndex };
                // Before the offer is in, the peer connection throws a candidate away. Through the tunnel the laptop's
                // candidates arrive in one batch with the offer, and without them the headset never tries to reach
                // the laptop: no picture from another network (2026-11-13). On one Wi-Fi nobody noticed, because
                // there the laptop reaches the headset by itself.
                if (offerIn) AddCandidate(candidate);
                else early.Add(candidate);
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
        offerIn = false;
        early.Clear();
        pc.OnIceCandidate = c => signalling?.SendIce(sessionId, c.Candidate, c.SdpMLineIndex ?? 0);
        pc.OnIceConnectionChange = s =>
        {
            state = $"ice {s}";
            Debug.Log($"[video] ice {s}");   // here too: "answer sent" may overwrite the state within the same frame
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
        mids.Clear();
        foreach (var line in offerSdp.Split('\n'))
            if (line.StartsWith("a=mid:")) mids.Add(line.Substring(6).Trim());
        offerIn = true;
        if (early.Count > 0) Debug.Log($"[video] {early.Count} candidate(s) had arrived before the offer was in");
        foreach (var candidate in early) AddCandidate(candidate);
        early.Clear();
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
            if (VideoTexture == null && ++routesLogged % 3 == 0) LogRoutes(op.Value);
            if (VideoTexture != null && routeOf != pc) { routeOf = pc; LogRoutes(op.Value); }   // once per connection: the way it took
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

    int routesLogged;
    RTCPeerConnection routeOf;

    void AddCandidate(RTCIceCandidateInit init)
    {
        if (pc == null) return;
        // webrtcsink names the media line only by its number; this peer connection wants its name (a=mid) too
        if (init.sdpMLineIndex is int index && index >= 0 && index < mids.Count) init.sdpMid = mids[index];
        string kind = System.Text.RegularExpressions.Regex.Match(init.candidate ?? "", @" (udp|tcp|UDP|TCP) .* typ (\w+)").Value;
        kind = kind.Length > 0 ? kind.Trim().Split(' ')[0] + " " + kind.Substring(kind.LastIndexOf(' ') + 1) : "?";
        try
        {
            bool taken = pc.AddIceCandidate(new RTCIceCandidate(init));
            if (!taken) Debug.LogWarning($"[video] candidate not taken ({kind}, line {init.sdpMLineIndex} {init.sdpMid})");
        }
        catch (System.Exception e) { Debug.LogWarning($"[video] candidate refused ({kind}, line {init.sdpMLineIndex}): {e.Message}"); }
    }

    /// <summary>While there is no picture: which ways between headset and laptop are being tried, by kind (host =
    /// own network, srflx = through the router, relay = over a relay server), and whether the other side answers.</summary>
    void LogRoutes(RTCStatsReport report)
    {
        var ways = new System.Collections.Generic.SortedDictionary<string, int>();
        foreach (var s in report.Stats.Values)
        {
            if (!(s is RTCIceCandidatePairStats pair)) continue;
            string Kind(string id) => report.Stats.TryGetValue(id ?? "", out var c) && c is RTCIceCandidateStats k
                ? $"{k.candidateType}/{k.protocol}" : "?";
            string way = $"{Kind(pair.localCandidateId)}>{Kind(pair.remoteCandidateId)} {pair.state}" +
                         $"{(pair.responsesReceived > 0 ? " answered" : "")}{(pair.nominated ? " IN USE" : "")}";
            ways[way] = ways.TryGetValue(way, out int n) ? n + 1 : 1;
        }
        var line = new System.Text.StringBuilder();
        int own = 0, theirs = 0;
        foreach (var s in report.Stats.Values)
        {
            if (s.Type == RTCStatsType.LocalCandidate) own++;
            if (s.Type == RTCStatsType.RemoteCandidate) theirs++;
        }
        line.Append($"{own} own / {theirs} of the laptop known; ");
        foreach (var w in ways) line.Append($"{w.Value}x {w.Key}; ");
        Debug.Log($"[video] routes (headset>laptop): {line}");
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
