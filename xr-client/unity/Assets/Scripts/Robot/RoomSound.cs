// Plays the room sound from the backend. Unity pulls samples on the audio thread and resamples the 16 kHz clip itself.
// Its own GameObject: MicSender silences everything played on its object.

using UnityEngine;

public class RoomSound : MonoBehaviour
{
    const string SecretKey = "secret";

    public RobotVideo video;

    readonly SpeechFilter filter = new SpeechFilter();
    BackendClient backend;
    volatile AudioJitterBuffer buffer;

    public BackendClient Backend => backend;
    public string Status { get; private set; } = "";

    void Start()
    {
        var secret = RobotVideo.ReadIntentExtra(SecretKey);
        if (!string.IsNullOrEmpty(secret)) { PlayerPrefs.SetString(SecretKey, secret); PlayerPrefs.Save(); }
    }

    void Update()
    {
        if (backend == null)
        {
            if (string.IsNullOrEmpty(video.SignallingUrl)) return;
            backend = new BackendClient(new System.Uri(video.SignallingUrl).Host, PlayerPrefs.GetString(SecretKey, ""));
            buffer = backend.Audio;
            var source = gameObject.AddComponent<AudioSource>();
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
