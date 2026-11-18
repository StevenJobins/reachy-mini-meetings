// Room sound for the headset: buffering and the speech filter. Pure maths, no Unity: port of the worklet and the filter
// chain in xr-client/pages/roomaudio.js.
//
// The stream is int16 PCM, 16 kHz mono, in uneven bursts over Wi-Fi. Quality notes (measured on the robot mic): natively
// 16 kHz (nothing above 8 kHz exists), ~90 % of the energy is room hum below 1 kHz. So: hum out, presence up, levels even.

using System;

/// <summary>Ring buffer that holds only as much sound as the network needs, so the room is not heard late.
/// It keeps the least it ever holds (over the last few seconds) near Safety: what is above that is delay nobody needs
/// and is played away 4 % faster. Safety starts small, grows with every gap (the Wi-Fi is that uneven) and shrinks
/// slowly while there is none. After a gap it refills to Safety plus one packet, with a short fade.
/// After a stall the sound that then comes in one burst is kept and played faster (12 % above Fast) until the delay
/// is gone; only above Hard is old sound dropped.
/// Safety started at 50 ms and shrank 3 ms per window at first: in the headset the sound was still choppy at times, so
/// it now starts at 100 ms, never goes below 60 and shrinks 1 ms per window.
/// Until 2026-11-18 it held a fixed 200 to 300 ms (and Unity read it 256 ms at a time, RoomSound): one saw the lips
/// move before one heard the voice.</summary>
public class AudioJitterBuffer
{
    public const int SampleRate = 16000;
    const int Ms = SampleRate / 1000;
    const int MinSafety = 60 * Ms, StartSafety = 100 * Ms, MaxSafety = 300 * Ms, Packet = 40 * Ms;
    const int Fast = SampleRate / 2, Hard = SampleRate * 3 / 2;
    const int Window = SampleRate * 4;       // the least held is watched over this much played sound
    const int SkipEvery = 25, SkipEveryFast = 8;

    readonly float[] buf = new float[SampleRate * 4];
    readonly object gate = new object();
    int read, write, count, skip, half = -1;
    int safety = StartSafety, low = int.MaxValue, played, shave;
    bool playing;
    float last;

    public int Underruns { get; private set; }
    public double BufferedMs { get { lock (gate) return count * 1000.0 / SampleRate; } }
    /// <summary>What it keeps in hand against uneven delivery, ms.</summary>
    public double SafetyMs { get { lock (gate) return safety * 1000.0 / SampleRate; } }

    /// <summary>One binary frame from the backend.</summary>
    public void PushPcm16(byte[] data, int length)
    {
        lock (gate)
        {
            int i = 0, n = 0;
            if (half >= 0 && length > 0)   // a frame that came in two pieces, cut inside a sample
            {
                buf[write] = (short)(half | data[i++] << 8) / 32768f;
                write = (write + 1) % buf.Length;
                n++;
                half = -1;
            }
            for (; i + 1 < length; i += 2)
            {
                buf[write] = (short)(data[i] | data[i + 1] << 8) / 32768f;
                write = (write + 1) % buf.Length;
                n++;
            }
            if (i < length) half = data[i];
            count += n;
            if (count > buf.Length) { count = buf.Length; read = write; }   // keep the newest, no hidden delay
        }
    }

    public void Read(float[] output, int offset, int length)
    {
        lock (gate)
        {
            if (count > Hard) { int drop = count - safety - Packet; read = (read + drop) % buf.Length; count -= drop; }
            if (!playing && count >= safety + Packet) { playing = true; low = int.MaxValue; played = 0; shave = 0; }
            for (int i = offset; i < offset + length; i++)
            {
                if (!playing || count < 1)
                {
                    if (playing)
                    {
                        playing = false;
                        Underruns++;
                        safety = Math.Min(MaxSafety, safety * 3 / 2 + 20 * Ms);
                    }
                    last *= 0.97f;   // fade out instead of a click
                    output[i] = last;
                    continue;
                }
                if ((shave > 0 || count > Fast) && ++skip >= (count > Fast ? SkipEveryFast : SkipEvery))
                {
                    skip = 0;
                    read = (read + 1) % buf.Length;
                    count--;
                    if (shave > 0) shave--;
                }
                last = output[i] = buf[read];
                read = (read + 1) % buf.Length;
                count--;
            }
            if (!playing) return;
            low = Math.Min(low, count);
            played += length;
            if (played < Window) return;
            // a window without a gap: what was never needed goes, and a little less is kept in hand
            shave = Math.Max(0, low - safety - 10 * Ms);
            safety = Math.Max(MinSafety, safety - Ms);
            low = int.MaxValue;
            played = 0;
        }
    }
}

/// <summary>Biquad, coefficients as in the Web Audio spec / RBJ cookbook.</summary>
public class Biquad
{
    readonly double b0, b1, b2, a1, a2;
    double x1, x2, y1, y2;

    Biquad(double b0, double b1, double b2, double a0, double a1, double a2)
    {
        this.b0 = b0 / a0; this.b1 = b1 / a0; this.b2 = b2 / a0; this.a1 = a1 / a0; this.a2 = a2 / a0;
    }

    public static Biquad HighPass(double hz, double q, double rate)
    {
        double w = 2 * Math.PI * hz / rate, alpha = Math.Sin(w) / (2 * q), c = Math.Cos(w);
        return new Biquad((1 + c) / 2, -(1 + c), (1 + c) / 2, 1 + alpha, -2 * c, 1 - alpha);
    }

    public static Biquad Peaking(double hz, double q, double gainDb, double rate)
    {
        double w = 2 * Math.PI * hz / rate, alpha = Math.Sin(w) / (2 * q), c = Math.Cos(w), g = Math.Pow(10, gainDb / 40);
        return new Biquad(1 + alpha * g, -2 * c, 1 - alpha * g, 1 + alpha / g, -2 * c, 1 - alpha / g);
    }

    public double Step(double x)
    {
        double y = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2;
        x2 = x1; x1 = x; y2 = y1; y1 = y;
        return y;
    }
}

/// <summary>Speech chain: high-pass 120 Hz against room hum, +4 dB presence at 3 kHz, compressor, make-up gain, soft limit.</summary>
public class SpeechFilter
{
    // Loudness (2026-11-16, "very low in the headset"): the settings before left a voice that peaks at -33 dBFS at
    // about -32 dBFS RMS, because the level control looks at the peaks and speech peaks ~18 dB above its average.
    // Now about -20 dBFS RMS for that voice and -15 for someone next to the robot; the soft knee above Knee keeps
    // the make-up gain from clipping.
    const double ThresholdDb = -24, Ratio = 4, MakeUp = 4, Knee = 0.6;
    const double AttackS = 0.005, ReleaseS = 0.15;
    // Level first: people a few metres from the robot arrive very quiet (measured 2026-11-11: speech peaks at
    // -33 dBFS, and came out of this filter at -28). A slow gain brings the loud parts of the speech to AgcTarget,
    // at most AgcMax times; it does not move in the pauses (below AgcFloor), so the noise there is not pulled up.
    const double AgcTarget = 0.6, AgcMax = 30, AgcFloor = 0.004, AgcAttackS = 0.02, AgcReleaseS = 4;

    readonly Biquad highPass = Biquad.HighPass(120, 0.7, AudioJitterBuffer.SampleRate);
    readonly Biquad presence = Biquad.Peaking(3000, 0.9, 4, AudioJitterBuffer.SampleRate);
    readonly double attack = Math.Exp(-1 / (AttackS * AudioJitterBuffer.SampleRate));
    readonly double release = Math.Exp(-1 / (ReleaseS * AudioJitterBuffer.SampleRate));
    readonly double agcAttack = Math.Exp(-1 / (AgcAttackS * AudioJitterBuffer.SampleRate));
    readonly double agcRelease = Math.Exp(-1 / (AgcReleaseS * AudioJitterBuffer.SampleRate));
    double level, peak = 0.06;

    public void Process(float[] samples, int offset, int length)
    {
        for (int i = offset; i < offset + length; i++)
        {
            double x = presence.Step(highPass.Step(samples[i]));
            double a = Math.Abs(x);
            if (a > peak) peak = agcAttack * peak + (1 - agcAttack) * a;
            else if (a > AgcFloor) peak = agcRelease * peak + (1 - agcRelease) * a;
            x *= Math.Min(AgcMax, Math.Max(1, AgcTarget / peak));
            double rectified = Math.Abs(x);
            level = rectified > level ? attack * level + (1 - attack) * rectified : release * level + (1 - release) * rectified;
            double db = 20 * Math.Log10(Math.Max(level, 1e-6));
            double gainDb = db > ThresholdDb ? (ThresholdDb - db) * (1 - 1 / Ratio) : 0;
            double y = x * Math.Pow(10, gainDb / 20) * MakeUp, ay = Math.Abs(y);
            if (ay > Knee) y = Math.Sign(y) * (Knee + (1 - Knee) * Math.Tanh((ay - Knee) / (1 - Knee)));
            samples[i] = (float)y;
        }
    }
}
