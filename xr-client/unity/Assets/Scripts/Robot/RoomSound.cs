// Plays the room sound from the backend, straight into Unity's mixer (OnAudioFilterRead on an own object, ~21 ms at a
// time, resampled here from 16 kHz to the output rate). Until 2026-11-18 it was a streamed AudioClip, which Unity reads
// 4096 samples = 256 ms at a time and 0.8 s ahead when it starts (measured in the editor): the room was heard clearly
// after the lips moved. If the mixer does not ask for sound within 2 s, it falls back to that clip.
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
    volatile bool muted;
    Out direct;
    float startedAt, callsAt, loggedAt;
    int lastCalls;

    /// <summary>The mixer's audio thread asks for the next ~21 ms.</summary>
    class Out : MonoBehaviour
    {
        public RoomSound room;
        public volatile int Calls;
        int rate;
        double phase;
        float prev, cur;
        float[] block = new float[2048];

        void Awake() => rate = AudioSettings.outputSampleRate;

        void OnAudioFilterRead(float[] data, int channels)
        {
            Calls++;
            var b = room.buffer;
            if (b == null || room.direct != this) return;
            int frames = data.Length / channels;
            double step = (double)AudioJitterBuffer.SampleRate / rate;
            int need = (int)(phase + frames * step);
            if (need > block.Length) block = new float[need];
            b.Read(block, 0, need);
            room.filter.Process(block, 0, need);
            int k = 0;
            for (int f = 0; f < frames; f++)
            {
                phase += step;
                while (phase >= 1 && k < need) { prev = cur; cur = block[k++]; phase -= 1; }
                float v = room.muted ? 0 : prev + (cur - prev) * (float)phase;
                for (int c = 0; c < channels; c++) data[f * channels + c] = v;
            }
        }
    }

    /// <summary>Silent while there is no picture (Curtain): sound from a room you do not see, with Reachy's motors
    /// in it while it looks around, only disturbs.</summary>
    public bool Muted
    {
        set { muted = value; if (source != null && direct == null) source.mute = value; }
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
            var go = new GameObject("Room Sound Out");
            go.transform.SetParent(transform, false);
            source = go.AddComponent<AudioSource>();   // before the filter: it feeds it
            // a second of silence, looping: with no clip the source does not count as playing, and the mixer may
            // stop asking the filter for sound
            source.clip = AudioClip.Create("Silence", AudioJitterBuffer.SampleRate, 1, AudioJitterBuffer.SampleRate, false);
            source.loop = true;
            direct = go.AddComponent<Out>();
            direct.room = this;
            source.Play();
            startedAt = Time.unscaledTime;
            Debug.Log("[room sound] direct to the mixer");
        }
        if (direct != null && direct.Calls == 0 && Time.unscaledTime - startedAt > 2f)
        {
            direct = null;
            source.mute = muted;
            source.clip = AudioClip.Create("Room", AudioJitterBuffer.SampleRate, 1, AudioJitterBuffer.SampleRate, true, OnRead);
            source.Play();
            Debug.LogWarning("[room sound] the mixer does not ask for sound: streamed clip instead (about 0.3 s later)");
        }
        if (direct != null)
        {
            // the mixer stopped asking (the app was in the background, the audio device changed): start again
            if (direct.Calls != lastCalls) { lastCalls = direct.Calls; callsAt = Time.unscaledTime; }
            else if (Time.unscaledTime - callsAt > 1f && Application.isFocused)
            {
                callsAt = Time.unscaledTime;
                source.Play();
                Debug.LogWarning("[room sound] the mixer stopped asking for sound: started again");
            }
        }
        // to the laptop's ~/Library/Logs/reachy-headset.log, so a test leaves numbers behind without adb
        if (backend.Connected && Time.unscaledTime - loggedAt > 10f)
        {
            loggedAt = Time.unscaledTime;
            backend.Send($"{{\"type\": \"log\", \"lines\": [\"{System.DateTime.Now:HH:mm:ss} unity {Status}, connects {backend.Connects}, stalls {backend.Stalls}\"]}}");
        }
        Status = backend.Connected
            ? $"room sound {backend.Audio.BufferedMs:0} ms (keeps {backend.Audio.SafetyMs:0}), {backend.Audio.Underruns} gaps{(direct == null ? ", clip" : "")}"
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
