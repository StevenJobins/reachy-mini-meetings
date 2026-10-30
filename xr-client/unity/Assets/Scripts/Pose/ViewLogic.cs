// Pairing video frames with robot poses, and when to trust the measured pose. Pure logic, no Unity.
// Same behaviour as xr-client/pages/app.js (poseAt, robotFollows).

using System;
using System.Collections.Generic;

/// <summary>Timestamped robot angles (degrees), interpolated. Frames carry no pose, so they are paired by time.</summary>
public class PoseHistory
{
    const int MaxSamples = 180;

    readonly List<(double t, RobotAngles a)> samples = new List<(double, RobotAngles)>();

    public int Count => samples.Count;
    public double LastTime => samples.Count > 0 ? samples[samples.Count - 1].t : double.NegativeInfinity;

    public void Add(double t, RobotAngles a)
    {
        samples.Add((t, a));
        if (samples.Count > MaxSamples) samples.RemoveAt(0);
    }

    public bool TryAt(double t, out RobotAngles result)
    {
        result = default;
        if (samples.Count == 0) return false;
        if (t <= samples[0].t) { result = samples[0].a; return true; }
        for (int i = samples.Count - 1; i > 0; i--)
        {
            if (samples[i - 1].t > t) continue;
            var (t0, a) = samples[i - 1];
            var (t1, b) = samples[i];
            double u = t1 > t0 ? Math.Min(1, (t - t0) / (t1 - t0)) : 1;
            result = new RobotAngles(a.Roll + (b.Roll - a.Roll) * u, a.Pitch + (b.Pitch - a.Pitch) * u, a.Yaw + (b.Yaw - a.Yaw) * u);
            return true;
        }
        result = samples[samples.Count - 1].a;
        return true;
    }
}

/// <summary>
/// Sender timestamps to the local clock. Network delay only ever adds to arrival − sender time, so its lower envelope
/// is the clock offset plus the fastest delivery. Arrival times alone are uneven on Wi-Fi: a late burst of poses would
/// shake the window. The envelope rises slowly, so it follows clock drift and a changed network path.
/// </summary>
public class PoseClock
{
    const double RiseRate = 0.002;   // s per s

    bool started;
    double offset, lastArrival;

    public double ToLocal(double senderS, double arrivalS)
    {
        double o = arrivalS - senderS;
        if (!started) { started = true; offset = o; }
        else offset = Math.Min(o, offset + RiseRate * (arrivalS - lastArrival));
        lastArrival = arrivalS;
        return senderS + offset;
    }
}

/// <summary>
/// The head has mechanical play. Measured 2026-10-30 from photos and the pose: a ±3° yaw command moves it 0.2°, ±6° moves
/// it 1.6°, and it stops ~3° short from either side. So small head movements of the user do not reach the robot at all,
/// and a window that followed the command would slide with the head while the picture stands still. This is that play:
/// the output only moves once the input pushes it from one end of the gap.
/// </summary>
public class Backlash
{
    readonly double width;
    bool started;
    double position;

    public Backlash(double width) { this.width = width; }

    public double Step(double input)
    {
        if (!started) { started = true; position = input; }
        else if (input > position + width) position = input - width;
        else if (input < position - width) position = input + width;
        return position;
    }
}

/// <summary>Where the head should be for a command, without the lag: the play on each axis.</summary>
public class HeadModel
{
    const double YawPlay = 3, PitchPlay = 2, RollPlay = 1.5;   // degrees, half the gap, fitted to the recording

    readonly Backlash roll = new Backlash(RollPlay), pitch = new Backlash(PitchPlay), yaw = new Backlash(YawPlay);

    public RobotAngles Step(RobotAngles command) =>
        new RobotAngles(roll.Step(command.Roll), pitch.Step(command.Pitch), yaw.Step(command.Yaw));
}

/// <summary>
/// Where the robot head is, for placing the video. The measured pose is accurate (it matches the image motion to ~3 %)
/// but comes in stair-steps and Wi-Fi bursts (bench test 2026-10-30: 0.29° jitter on a 0.37° step per frame), which shakes
/// the window, while a real head cannot move like that. So: the modelled path, smooth and known here (the command through
/// HeadModel), delayed by how long the robot takes, plus a correction towards the measured pose (a complementary filter).
/// </summary>
public class PoseBlend
{
    const double TauS = 0.25;

    readonly double lagS;
    bool started;
    double lastT, roll, pitch, yaw;   // measured − delayed model, degrees

    public PoseBlend(double lagS) { this.lagS = lagS; }

    public double LagS => lagS;

    public void Reset() => started = false;

    /// <summary>Call with every measured pose (at its time on the local clock) while the robot follows.</summary>
    public void AddMeasured(double t, RobotAngles m, PoseHistory modelled)
    {
        if (!modelled.TryAt(t - lagS, out var c)) return;
        double k = started ? 1 - Math.Exp(-Math.Max(0, t - lastT) / TauS) : 1;
        started = true;
        lastT = t;
        roll += k * (m.Roll - c.Roll - roll);
        pitch += k * (m.Pitch - c.Pitch - pitch);
        yaw += k * (m.Yaw - c.Yaw - yaw);
    }

    public bool TryAt(double t, PoseHistory modelled, out RobotAngles a)
    {
        a = default;
        if (!started || !modelled.TryAt(t - lagS, out var c)) return false;
        a = new RobotAngles(c.Roll + roll, c.Pitch + pitch, c.Yaw + yaw);
        return true;
    }
}

/// <summary>
/// World-locked only while the robot head really does what we command: following, fresh poses, measured pose within
/// tolerance of the command. Otherwise (asleep, wake-up motion, stuck, no pose stream) the measured pose says nothing
/// about where the user looks. Hysteresis so it does not flicker on fast head turns.
/// </summary>
public class FollowGuard
{
    const double Tol = 30, TolOk = 15;     // degrees: leave above Tol, enter below TolOk
    const double RobotLagS = 0.25;         // the robot lags the command, so compare with a slightly older command
    const double FreshS = 0.7;
    const double HoldOnS = 0.3, HoldOffS = 0.5;

    double since;

    public bool Following { get; private set; }

    public bool Update(bool active, PoseHistory measured, PoseHistory commanded, double delayS, double nowS)
    {
        double err = double.PositiveInfinity;
        if (measured.TryAt(nowS - delayS, out var p) && commanded.TryAt(nowS - delayS - RobotLagS, out var c))
            err = Math.Max(Math.Max(Math.Abs(p.Roll - c.Roll), Math.Abs(p.Pitch - c.Pitch)), Math.Abs(p.Yaw - c.Yaw));
        bool fresh = nowS - measured.LastTime < FreshS;
        bool ok = active && fresh && (Following ? err < Tol : err < TolOk);
        if (ok == Following) since = nowS;
        else if (nowS - since > (ok ? HoldOnS : HoldOffS)) { Following = ok; since = nowS; }
        return Following;
    }
}
