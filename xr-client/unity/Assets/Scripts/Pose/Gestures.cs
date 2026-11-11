// Expressive feedback for the people in the room. Pure maths, no Unity; angles in degrees, time in seconds.
//
// WantToTalk: port of xr-client/pages/gestures.js. The right antenna waves (fast, a bit irregular), the left one stays
//   still, and the body swings around where it is, faded in and out. The head keeps following the user.
// NodShake: a nod or a head shake of the user comes out of the robot larger. The head follows the headset through a
//   smoothing and speed limits, which swallow small quick movements, and a nod that the room cannot see says nothing.
//   The quick part of pitch (nod) and yaw (shake) is added once more while the head swings back and forth; a single
//   look down or a turn to someone is not touched.

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
    public const double NodGain = 1.0, ShakeGain = 0.8;   // added on top: a nod comes out twice as large

    class Axis
    {
        public double Slow, Boost, ActiveUntil = double.NegativeInfinity;
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
        return new RobotAngles(a.Roll, a.Pitch + NodGain * Quick(pitch, a.Pitch, t, dt), a.Yaw + ShakeGain * Quick(yaw, a.Yaw, t, dt));
    }

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
        return x.Boost * quick;
    }
}
