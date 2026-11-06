// Your voice -> Reachy's speaker, over the same WebRTC connection as the video. The audio transceiver is send-only:
// receiving the robot's audio would hold the video back (see RobotVideo.Answer), and the room sound comes another way.
// Push to talk: on only while the user holds the right pinch (RobotControl), so it can never stay on by accident.
// While it is on, the same mic also goes to the backend (16 kHz PCM), which transcribes it for the "You" subtitles.

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

    AudioSource source;
    AudioStreamTrack track;
    RTCRtpSender sender;
    // to the backend: read position in the looping mic clip, box-filter decimation state, samples not sent yet
    int readPos;
    bool teeing;
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
        bool on = control.MicOn;
        if (track.Enabled != on) track.Enabled = on;
        Tee(on);
        Status = sender == null ? "mic: no connection" : on ? $"mic on {Kbps:0} kbps" : "mic off, hold right pinch to talk";
    }

    /// <summary>The mic since the last frame, down to 16 kHz, to the backend while talking; "voice_end" on release.</summary>
    void Tee(bool on)
    {
        var backend = room != null ? room.Backend : null;
        var clip = source.clip;
        int pos = Microphone.GetPosition(null);
        if (!on || backend == null || !backend.Connected)
        {
            if (teeing)
            {
                Flush(backend, true);
                backend?.Send(VoiceEnd);
                teeing = false;
            }
            readPos = pos;
            return;
        }
        teeing = true;
        int n = (pos - readPos + clip.samples) % clip.samples;
        if (n == 0) return;
        if (read.Length < n) read = new float[n];
        clip.GetData(read, readPos);   // wraps around the end of the clip
        readPos = pos;
        for (int i = 0; i < n; i++)
        {
            sum += read[i];
            count++;
            phase += BackendRate;
            if (phase < clip.frequency) continue;
            phase -= clip.frequency;
            pcm.Add((short)(Mathf.Clamp(sum / count, -1, 1) * 32767));
            sum = 0;
            count = 0;
        }
        Flush(backend, false);
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
