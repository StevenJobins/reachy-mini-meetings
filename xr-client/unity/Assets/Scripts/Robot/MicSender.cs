// Your voice -> Reachy's speaker, over the same WebRTC connection as the video. The audio transceiver is send-only:
// receiving the robot's audio would hold the video back (see RobotVideo.Answer), and the room sound comes another way.
// Open unless muted (the mic tile, RobotControl.Muted, on at every start), and then only sent while you speak
// (VoiceGate): the backend mutes the room for as long as your voice comes in. While you speak, the same mic also goes
// to the backend (16 kHz PCM, with 0.3 s from before the gate opened), which transcribes it for the "You" subtitles;
// "voice_end" when the gate closes ends the sentence.
// Translate me (QuickMenu): the WebRTC track stays off and the backend translates your words and lets Reachy say
// them (the menu tells the backend which of the two, "me_mode").

using System.Collections;
using System.Collections.Generic;
using Unity.WebRTC;
using UnityEngine;
using UnityEngine.Android;

public class MicSender : MonoBehaviour
{
    const int SampleRate = 48000;
    const int BackendRate = 16000, FrameSamples = 640;   // 40 ms frames, as the room sound
    const string VoiceEnd = "{\"type\": \"voice_end\"}";

    public RobotControl control;
    public RoomSound room;
    public QuickMenu menu;

    AudioSource source;
    AudioStreamTrack track;
    RTCRtpSender sender;
    // to the backend: read position in the looping mic clip, box-filter decimation state, samples not sent yet
    const int PreRoll = BackendRate * 3 / 10;   // 0.3 s kept while quiet: the gate opens ~60 ms into the first word
    int readPos, rmsSamples, openBlocks;
    bool teeing;
    VoiceGate gate;
    readonly List<float> levels = new List<float>();
    float loggedAt;
    float sum;
    int count, phase;
    float[] read = new float[0];
    readonly List<short> pcm = new List<short>();

    public string Status { get; private set; } = "mic starting";
    public double Kbps { get; set; }

    IEnumerator Start()
    {
#if UNITY_ANDROID && !UNITY_EDITOR
        if (!Permission.HasUserAuthorizedPermission(Permission.Microphone))
        {
            Permission.RequestUserPermission(Permission.Microphone);
            while (!Permission.HasUserAuthorizedPermission(Permission.Microphone))
            {
                Status = "mic: allow access in the headset";
                yield return new WaitForSecondsRealtime(0.5f);
            }
        }
#endif
        gate = double.TryParse(RobotVideo.ReadIntentExtra("micgate"), System.Globalization.NumberStyles.Float,
                               System.Globalization.CultureInfo.InvariantCulture, out var db)
            ? new VoiceGate(db) : new VoiceGate();
        source = gameObject.AddComponent<AudioSource>();
        source.clip = Microphone.Start(null, true, 1, SampleRate);
        if (source.clip == null) { Status = "mic: none found"; yield break; }
        while (Microphone.GetPosition(null) <= 0) yield return null;
        source.loop = true;
        source.Play();
        track = new AudioStreamTrack(source) { Loopback = false, Enabled = false };   // not to our own ears
        if (sender != null) sender.ReplaceTrack(track);
    }

    /// <summary>Call with the audio transceiver before answering the offer.</summary>
    public void Attach(RTCRtpTransceiver transceiver)
    {
        transceiver.Direction = RTCRtpTransceiverDirection.SendOnly;
        sender = transceiver.Sender;
        if (track != null) sender.ReplaceTrack(track);
    }

    void Update()
    {
        if (track == null) return;
        bool open = control.MicOn;
        bool translate = menu != null && menu.Translate;
        var backend = room != null ? room.Backend : null;
        bool connected = backend != null && backend.Connected;
        float rms = ReadMic();
        if (!open) gate.Reset();
        else if (rmsSamples > 0) gate.Step(rms, rmsSamples / (double)source.clip.frequency);
        bool speaking = open && gate.Open;
        if (speaking != control.Speaking)
        {
            control.Speaking = speaking;
            Debug.Log($"[mic] {(speaking ? "speaking" : "quiet")}");
        }
        if (track.Enabled != (speaking && !translate)) track.Enabled = speaking && !translate;
        if (speaking && connected)
        {
            teeing = true;
            Flush(backend, false);   // the pre-roll first, then the voice as it comes
        }
        else
        {
            if (teeing)
            {
                if (connected) { Flush(backend, true); backend.Send(VoiceEnd); }
                teeing = false;
            }
            if (pcm.Count > PreRoll) pcm.RemoveRange(0, pcm.Count - PreRoll);
        }
        LogLevel(rms, speaking);
        Status = !open ? control.Muted ? "mic muted" : "mic off (tutorial)"
            : translate ? !connected ? "translate: no backend" : speaking ? "translate: speaking" : "translate: listening"
            : sender == null ? "mic: no connection" : speaking ? $"mic: speaking {Kbps:0} kbps" : "mic: listening";
    }

    /// <summary>The mic since the last frame: its RMS, and down to 16 kHz into pcm (the pre-roll while quiet).</summary>
    float ReadMic()
    {
        var clip = source.clip;
        int pos = Microphone.GetPosition(null);
        int n = (pos - readPos + clip.samples) % clip.samples;
        rmsSamples = n;
        if (n == 0) return 0;
        if (read.Length < n) read = new float[n];
        clip.GetData(read, readPos);   // wraps around the end of the clip
        readPos = pos;
        double sq = 0;
        for (int i = 0; i < n; i++)
        {
            sq += read[i] * read[i];
            sum += read[i];
            count++;
            phase += BackendRate;
            if (phase < clip.frequency) continue;
            phase -= clip.frequency;
            pcm.Add((short)(Mathf.Clamp(sum / count, -1, 1) * 32767));
            sum = 0;
            count = 0;
        }
        return Mathf.Sqrt((float)(sq / n));
    }

    /// <summary>Every 2 s: the loudest and the median block, and how much of the time the gate was open, for tuning
    /// the threshold in the headset (adb logcat -s Unity).</summary>
    void LogLevel(float rms, bool speaking)
    {
        if (rmsSamples == 0) return;
        levels.Add((float)VoiceGate.Db(rms));
        if (speaking) openBlocks++;
        if (Time.unscaledTime - loggedAt < 2f) return;
        levels.Sort();
        Debug.Log($"[mic] level median {levels[levels.Count / 2]:0} max {levels[levels.Count - 1]:0} dBFS, " +
                  $"gate {gate.OpenDb:0} dB, speaking {100f * openBlocks / levels.Count:0}%{(control.MicOn ? "" : " (muted)")}");
        levels.Clear();
        openBlocks = 0;
        loggedAt = Time.unscaledTime;
    }

    void Flush(BackendClient backend, bool all)
    {
        while (pcm.Count >= FrameSamples || (all && pcm.Count > 0))
        {
            int k = Mathf.Min(FrameSamples, pcm.Count);
            var bytes = new byte[2 * k];
            for (int i = 0; i < k; i++) { bytes[2 * i] = (byte)pcm[i]; bytes[2 * i + 1] = (byte)(pcm[i] >> 8); }
            pcm.RemoveRange(0, k);
            backend?.SendBinary(bytes);
        }
    }

    void OnDestroy()
    {
        track?.Dispose();
        Microphone.End(null);
    }
}
