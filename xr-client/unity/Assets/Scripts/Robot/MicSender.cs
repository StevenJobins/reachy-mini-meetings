// Your voice -> Reachy's speaker, over the same WebRTC connection as the video. The audio transceiver is send-only:
// receiving the robot's audio would hold the video back (see RobotVideo.Answer), and the room sound comes another way.
// Push to talk: on only while the user holds the right pinch (RobotControl), so it can never stay on by accident.
// Translate me (QuickMenu): the voice goes to the backend instead (int16 PCM, 16 kHz mono, 40 ms frames, like
// xr-client/pages/mic.js), which translates it and lets Reachy say it; the WebRTC track stays off meanwhile.

using System;
using System.Collections;
using Unity.WebRTC;
using UnityEngine;
using UnityEngine.Android;

public class MicSender : MonoBehaviour
{
    const int SampleRate = 48000;
    const int VoiceRate = 16000, VoiceFrame = 640;

    public RobotControl control;
    public RoomSound room;
    public QuickMenu menu;

    AudioSource source;
    AudioStreamTrack track;
    RTCRtpSender sender;
    float[] clipData;
    int readPos;
    double step, acc, sum;
    int count;
    readonly short[] frame = new short[VoiceFrame];
    int frameN;
    bool voiceOn;
    int framesSent;

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
        clipData = new float[source.clip.samples];
        step = (double)source.clip.frequency / VoiceRate;
        readPos = Microphone.GetPosition(null);
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
        bool translate = menu != null && menu.Translate;
        bool on = control.MicOn;
        if (track.Enabled != (on && !translate)) track.Enabled = on && !translate;
        UpdateVoice(on && translate);
        var backend = room != null ? room.Backend : null;
        Status = translate
            ? backend == null || !backend.Connected ? "translate: no backend"
              : on ? $"translate: speaking, {framesSent} frames" : "translate on, hold right pinch to talk"
            : sender == null ? "mic: no connection" : on ? $"mic on {Kbps:0} kbps" : "mic off, hold right pinch to talk";
    }

    /// <summary>New mic samples, 16 kHz, in 40 ms frames to the backend while `send`; voice_end when it stops.</summary>
    void UpdateVoice(bool send)
    {
        var backend = room != null ? room.Backend : null;
        int pos = Microphone.GetPosition(null);
        if (!send || backend == null || !backend.Connected)
        {
            readPos = pos;
            if (voiceOn)
            {
                voiceOn = false;
                frameN = 0;
                backend?.Send("{\"type\": \"voice_end\"}");   // the backend ends your sentence now
                Debug.Log($"[mic] translate: voice_end after {framesSent} frames");
            }
            return;
        }
        if (!voiceOn)
        {
            voiceOn = true;
            framesSent = 0;
            acc = sum = 0;
            count = 0;
            Debug.Log("[mic] translate: sending to the backend");
        }
        source.clip.GetData(clipData, 0);
        int n = clipData.Length;
        for (int i = readPos; i != pos; i = (i + 1) % n)
        {
            // average over each 1/16000 s: low-pass and decimate in one
            sum += clipData[i];
            count++;
            acc += 1;
            if (acc < step) continue;
            acc -= step;
            frame[frameN++] = (short)Mathf.Clamp((float)(sum / count) * 32767f, -32768f, 32767f);
            sum = 0;
            count = 0;
            if (frameN < VoiceFrame) continue;
            var bytes = new byte[VoiceFrame * 2];
            Buffer.BlockCopy(frame, 0, bytes, 0, bytes.Length);   // little-endian on ARM and x86
            backend.SendBinary(bytes);
            framesSent++;
            frameN = 0;
        }
        readPos = pos;
    }

    void OnDestroy()
    {
        track?.Dispose();
        Microphone.End(null);
    }
}
