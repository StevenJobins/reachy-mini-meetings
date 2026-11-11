// Expressive feedback for the people in the room. Pure maths, no Unity; angles in degrees, time in seconds.
//
// WantToTalk: port of xr-client/pages/gestures.js. The right antenna waves (fast, a bit irregular), the left one stays
//   still, and the body swings around where it is, faded in and out. The head keeps following the user.
// NodShake: a nod or a head shake of the user is shown by the antennas, and the head (the camera) holds still
//   meanwhile. Moving the head with the nod, let alone larger, moved the picture in the headset up and down, which is
//   very unpleasant (headset test 2026-11-11). While the head swings back and forth, the quick part of pitch (nod) or
//   yaw (shake) is taken out of what the head follows and drives the antennas instead: both together for a nod,
//   against each other for a shake. A single look down or a turn to someone is not touched.
// Reaction: thumbs up (both antennas spring up and bounce), thumbs down (both sink), a smile (both a little up).

using System;
using System.Collections.Generic;

public class WantToTalk
{
    public const double DurationS = 3, AntennaDeg = 32, AntennaHz = 4.5, BodyDeg = 27, BodyHz = 1, FadeS = 0.3;

    readonly Random random = new Random(1);
    double start = double.NegativeInfinity, until = double.NegativeInfinity;

    /// <summary>Start, or extend while already waving (like pressing the button again).</summary>
    public void Trigger(double t)
    {
        if (t > until) start = t;
        until = t + DurationS;
    }

    public bool Active(double t) => t <= until;

    /// <summary>Antennas (right, left) and the body's offset from where it is. Zero when idle.</summary>
    public void Step(double t, out double right, out double left, out double bodyOffset)
    {
        right = left = bodyOffset = 0;
        if (!Active(t)) return;
        double s = t - start;
        double env = Math.Max(0, Math.Min(1, Math.Min(s / FadeS, (until - t) / FadeS)));
        right = AntennaDeg * Math.Sin(2 * Math.PI * AntennaHz * t) + (random.NextDouble() * 12 - 6);
        bodyOffset = env * BodyDeg * Math.Sin(2 * Math.PI * BodyHz * s);
    }
}

public class NodShake
{
    const double SlowS = 0.5;        // what changes slower than this is looking somewhere, not a gesture
    const double MinDeg = 2.5;       // a swing counts from this far off the slow part
    const double WindowS = 1.4;      // three swings (down-up-down) within this are a nod
    const double HoldS = 0.5, RampS = 0.15;
    public const double AntennaPerDeg = 5, AntennaMax = 40;   // antenna degrees per degree of the head's quick part

    class Axis
    {
        public double Slow, Boost, Quick, ActiveUntil = double.NegativeInfinity;
        public int Sign;
        public readonly List<double> Swings = new List<double>();
    }

    readonly Axis pitch = new Axis(), yaw = new Axis();
    bool started;

    public bool Nodding(double t) => t <= pitch.ActiveUntil;
    public bool Shaking(double t) => t <= yaw.ActiveUntil;

    public RobotAngles Step(RobotAngles a, double t, double dt)
    {
        if (!started) { pitch.Slow = a.Pitch; yaw.Slow = a.Yaw; started = true; }
        // what the head follows: without the quick part while it is a gesture, so the camera holds still
        return new RobotAngles(a.Roll, a.Pitch - Quick(pitch, a.Pitch, t, dt), a.Yaw - Quick(yaw, a.Yaw, t, dt));
    }

    /// <summary>The gesture on the antennas (right, left), degrees; zero when there is none.</summary>
    public void Antennas(out double right, out double left)
    {
        double nod = Clamp(-AntennaPerDeg * pitch.Boost * pitch.Quick);   // head down = antennas down
        double shake = Clamp(AntennaPerDeg * yaw.Boost * yaw.Quick);
        right = Clamp(nod + shake);
        left = Clamp(nod - shake);
    }

    static double Clamp(double v) => Math.Max(-AntennaMax, Math.Min(AntennaMax, v));

    static double Quick(Axis x, double value, double t, double dt)
    {
        x.Slow += (value - x.Slow) * (1 - Math.Exp(-dt / SlowS));
        double quick = value - x.Slow;
        int sign = quick > MinDeg ? 1 : quick < -MinDeg ? -1 : 0;
        if (sign != 0 && sign != x.Sign)
        {
            x.Sign = sign;
            x.Swings.Add(t);
        }
        x.Swings.RemoveAll(s => t - s > WindowS);
        if (x.Swings.Count >= 3) x.ActiveUntil = t + HoldS;
        double target = t <= x.ActiveUntil ? 1 : 0;
        x.Boost += Math.Max(-dt / RampS, Math.Min(dt / RampS, target - x.Boost));
        x.Quick = quick;
        return x.Boost * quick;
    }
}

/// <summary>Short antenna gestures for the room: thumbs up, thumbs down, a smile.</summary>
public class Reaction
{
    public enum Kind { None, ThumbsUp, ThumbsDown }
    const double UpS = 1.4, DownS = 1.8;

    Kind kind;
    double start = double.NegativeInfinity;
    double smile;   // 0..1, eased

    public void Trigger(Kind k, double t)
    {
        if (Active(t) && kind == k) return;
        kind = k;
        start = t;
    }

    public bool Active(double t) => kind != Kind.None && t - start <= (kind == Kind.ThumbsUp ? UpS : DownS);

    /// <summary>Antennas (right, left) in degrees, + = up. smiling: how much the user smiles now, 0..1.</summary>
    public void Step(double t, double dt, double smiling, out double right, out double left)
    {
        smile += (Math.Max(0, Math.Min(1, smiling)) - smile) * (1 - Math.Exp(-dt / 0.25));
        right = left = 14 * smile;
        if (!Active(t)) return;
        double s = t - start;
        if (kind == Kind.ThumbsUp)
        {
            // up at once, three quick bounces, back
            double env = Math.Min(1, s / 0.12) * Math.Min(1, (UpS - s) / 0.3);
            double bounce = 30 + 10 * Math.Abs(Math.Sin(Math.PI * 3 * s));
            right = left = env * bounce;
        }
        else
        {
            // sink slowly, hold, come back
            double env = Math.Min(1, s / 0.5) * Math.Min(1, (DownS - s) / 0.4);
            right = left = -38 * env;
        }
    }
}
