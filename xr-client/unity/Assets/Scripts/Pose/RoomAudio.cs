// Room sound for the headset: buffering and the speech filter. Pure maths, no Unity: port of the worklet and the filter
// chain in xr-client/pages/roomaudio.js.
//
// The stream is int16 PCM, 16 kHz mono, in uneven bursts over Wi-Fi. Quality notes (measured on the robot mic): natively
// 16 kHz (nothing above 8 kHz exists), ~90 % of the energy is room hum below 1 kHz. So: hum out, presence up, levels even.

using System;

/// <summary>Ring buffer: starts at a small target, refills after an underrun with a short fade, and drops single samples
/// when too full, so clock drift and bursts do not add delay.</summary>
public class AudioJitterBuffer
{
    public const int SampleRate = 16000;
    const int Target = SampleRate * 12 / 100, Slack = SampleRate / 5, Hard = SampleRate * 45 / 100;
    const int SkipEvery = 25;

    readonly float[] buf = new float[SampleRate * 4];
    readonly object gate = new object();
    int read, write, count, skip;
    bool playing;
    float last;

    public int Underruns { get; private set; }
    public double BufferedMs { get { lock (gate) return count * 1000.0 / SampleRate; } }

    /// <summary>One binary frame from the backend.</summary>
    public void PushPcm16(byte[] data, int length)
    {
        lock (gate)
        {
            for (int i = 0; i + 1 < length; i += 2)
            {
                buf[write] = (short)(data[i] | data[i + 1] << 8) / 32768f;
                write = (write + 1) % buf.Length;
            }
            count += length / 2;
            if (count > buf.Length) { count = buf.Length; read = write; }   // keep the newest, no hidden delay
        }
    }

    public void Read(float[] output, int offset, int length)
    {
        lock (gate)
        {
            if (count > Hard) { int drop = count - Target; read = (read + drop) % buf.Length; count -= drop; }
            if (!playing && count >= Target) playing = true;
            for (int i = offset; i < offset + length; i++)
            {
                if (!playing || count < 1)
                {
                    if (playing) { playing = false; Underruns++; }
                    last *= 0.97f;   // fade out instead of a click
                    output[i] = last;
                    continue;
                }
                if (count > Slack && ++skip >= SkipEvery) { skip = 0; read = (read + 1) % buf.Length; count--; }
                last = output[i] = buf[read];
                read = (read + 1) % buf.Length;
                count--;
            }
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

/// <summary>Speech chain: high-pass 120 Hz against room hum, +4 dB presence at 3 kHz, compressor, make-up gain.</summary>
public class SpeechFilter
{
    const double ThresholdDb = -28, Ratio = 3.5, MakeUp = 1.8;
    const double AttackS = 0.005, ReleaseS = 0.15;

    readonly Biquad highPass = Biquad.HighPass(120, 0.7, AudioJitterBuffer.SampleRate);
    readonly Biquad presence = Biquad.Peaking(3000, 0.9, 4, AudioJitterBuffer.SampleRate);
    readonly double attack = Math.Exp(-1 / (AttackS * AudioJitterBuffer.SampleRate));
    readonly double release = Math.Exp(-1 / (ReleaseS * AudioJitterBuffer.SampleRate));
    double level;

    public void Process(float[] samples, int offset, int length)
    {
        for (int i = offset; i < offset + length; i++)
        {
            double x = presence.Step(highPass.Step(samples[i]));
            double rectified = Math.Abs(x);
            level = rectified > level ? attack * level + (1 - attack) * rectified : release * level + (1 - release) * rectified;
            double db = 20 * Math.Log10(Math.Max(level, 1e-6));
            double gainDb = db > ThresholdDb ? (ThresholdDb - db) * (1 - 1 / Ratio) : 0;
            double y = x * Math.Pow(10, gainDb / 20) * MakeUp;
            samples[i] = (float)Math.Max(-1, Math.Min(1, y));
        }
    }
}
