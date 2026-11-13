// Plays the room sound from the backend. Unity pulls samples on the audio thread and resamples the 16 kHz clip itself.
// Its own GameObject: MicSender silences everything played on its object. Owns the backend connection.

using UnityEngine;

public class RoomSound : MonoBehaviour
{
    // The headset voice the backend gets is only transcribed ("You" subtitles): Reachy already plays it over WebRTC
    const string MeMode = "{\"type\": \"me_mode\", \"speak\": false}";

    public RobotVideo video;

    readonly SpeechFilter filter = new SpeechFilter();
    BackendClient backend;
    volatile AudioJitterBuffer buffer;
    AudioSource source;
    bool muted;

    /// <summary>Silent while there is no picture (Curtain): sound from a room you do not see, with Reachy's motors
    /// in it while it looks around, only disturbs.</summary>
    public bool Muted
    {
        set { muted = value; if (source != null) source.mute = value; }
    }

    public BackendClient Backend => backend;
    public string Status { get; private set; } = "";

    void Update()
    {
        if (backend == null)
        {
            if (video.Laptop == null) return;
            backend = new BackendClient(() => video.Laptop, MeMode);
            video.LaptopChanged += backend.Restart;
            buffer = backend.Audio;
            source = gameObject.AddComponent<AudioSource>();
            source.mute = muted;
            source.clip = AudioClip.Create("Room", AudioJitterBuffer.SampleRate, 1, AudioJitterBuffer.SampleRate, true, OnRead);
            source.loop = true;
            source.Play();
        }
        Status = backend.Connected
            ? $"room sound {backend.Audio.BufferedMs:0} ms, {backend.Audio.Underruns} gaps"
            : $"backend not connected {backend.LastError}";
    }

    /// <summary>Audio thread.</summary>
    void OnRead(float[] data)
    {
        var b = buffer;
        if (b == null) { System.Array.Clear(data, 0, data.Length); return; }
        b.Read(data, 0, data.Length);
        filter.Process(data, 0, data.Length);
    }

    void OnDestroy() => backend?.Dispose();
}
