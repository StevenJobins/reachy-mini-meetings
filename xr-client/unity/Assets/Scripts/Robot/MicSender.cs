// Your voice -> Reachy's speaker, over the same WebRTC connection as the video. The audio transceiver is send-only:
// receiving the robot's audio would hold the video back (see RobotVideo.Answer), and the room sound comes another way.
// Push to talk: on only while the user holds the right pinch (RobotControl), so it can never stay on by accident.

using System.Collections;
using Unity.WebRTC;
using UnityEngine;
using UnityEngine.Android;

public class MicSender : MonoBehaviour
{
    const int SampleRate = 48000;

    public RobotControl control;

    AudioSource source;
    AudioStreamTrack track;
    RTCRtpSender sender;

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
        Status = sender == null ? "mic: no connection" : on ? $"mic on {Kbps:0} kbps" : "mic off, hold right pinch to talk";
    }

    void OnDestroy()
    {
        track?.Dispose();
        Microphone.End(null);
    }
}
