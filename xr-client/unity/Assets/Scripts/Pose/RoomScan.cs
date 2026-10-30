// Room panorama logic. Pure, no Unity. Angles in degrees, robot frame (yaw + = left, pitch + = down).
// The scan is a short version of xr-client/pages/roomscan.js: only the half-circle in front of Reachy, head only.

using System;
using System.Collections.Generic;
using System.Linq;

/// <summary>Where Reachy looks during the scan, and when to take the picture.</summary>
public class RoomScan
{
    // 6 stops, 50° apart, in 2 rows. The ~88° wide camera overlaps by ~38°, the ~57° tall frames reach ~46° up, ~43° down.
    // All inside the ±65° head/body window, so the body stays still. Snake order: every move is short.
    static readonly (double pitch, double yaw)[] Stops =
    {
        (-18, -50), (-18, 0), (-18, 50), (15, 50), (15, 0), (15, -50),
    };

    const double NearDeg = 7;        // within this of the stop: the head stops short of a command by its play
    const double StillDeg = 1;       // steady when it moved less than this ...
    const double SettleS = 0.3;      // ... for this long
    const double TimeoutS = 3;       // then take the picture anyway

    int index;
    double moveSince;
    double? steadySince;

    public bool Active { get; private set; }
    public int Done => index;
    public int Total => Stops.Length;
    public RobotAngles Target => new RobotAngles(0, Stops[index].pitch, Stops[index].yaw);

    public void Start(double t)
    {
        Active = true;
        index = 0;
        moveSince = t;
        steadySince = null;
    }

    public void Cancel() => Active = false;

    /// <summary>True when the picture of this stop is to be taken now. spanDeg: how much the head moved lately.</summary>
    public bool Step(double t, RobotAngles measured, double spanDeg, double videoDelayS)
    {
        if (!Active) return false;
        var tg = Target;
        bool near = Math.Abs(measured.Yaw - tg.Yaw) < NearDeg && Math.Abs(measured.Pitch - tg.Pitch) < NearDeg;
        if (!near || spanDeg >= StillDeg) steadySince = null;
        else if (steadySince == null) steadySince = t;
        // the video lags the pose, so the frame must show the settled view
        return steadySince != null && t - steadySince.Value >= SettleS + videoDelayS || t - moveSince > TimeoutS;
    }

    /// <summary>The picture of this stop was taken: next stop.</summary>
    public void Shot(double t)
    {
        index++;
        moveSince = t;
        steadySince = null;
        if (index >= Stops.Length) Active = false;
    }
}

/// <summary>Which saved picture a new frame replaces: the nearest one, or a new one for a new direction.</summary>
public class PatchSlots
{
    public class Slot { public int Id; public double Pitch, Yaw, T; }

    const double NewSlotDeg = 25;
    public const int Max = 24;

    readonly List<Slot> slots = new List<Slot>();
    int nextId;

    public int Count => slots.Count;

    /// <summary>The slot for this direction; evicted = the id whose picture it takes over, if the panorama is full.</summary>
    public Slot Take(double pitch, double yaw, double t, out int? evicted)
    {
        evicted = null;
        var best = slots.OrderBy(s => Distance(s, pitch, yaw)).FirstOrDefault();
        if (best == null || Distance(best, pitch, yaw) > NewSlotDeg)
        {
            if (slots.Count >= Max)
            {
                var oldest = slots.OrderBy(s => s.T).First();
                evicted = oldest.Id;
                slots.Remove(oldest);
            }
            best = new Slot { Id = nextId++ };
            slots.Add(best);
        }
        best.Pitch = pitch; best.Yaw = yaw; best.T = t;
        return best;
    }

    static double Distance(Slot s, double pitch, double yaw)
    {
        double dy = (yaw - s.Yaw + 540) % 360 - 180, dp = pitch - s.Pitch;
        return Math.Sqrt(dy * dy + dp * dp);
    }
}
