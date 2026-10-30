// Who is speaking: person tracks in the robot camera image + mouth movement + microphone direction. Pure logic, no
// Unity: port of xr-client/pages/speakers.js, checked against it in Assets/Tests.
//
// Faces come in normalized image coordinates (0..1, origin top left) from backend/vision.py, ~20 times a second.
// Angles: degrees, + = left of the camera axis.
//
// Identity: a track only lives while the face is in view. Who it IS (Pid, "Speaker 1") is decided by the direction in
// the room (robot head yaw + face angle): people sit still in a meeting, so a face that shows up again where someone
// was before is that person again, also after the robot looked elsewhere.

using System;
using System.Collections.Generic;
using System.Linq;

public struct Face
{
    public double Cx, Cy, Top, W, H;
    public double? Mouth;
}

public class FaceTrack
{
    public int Id;
    public int? Pid;
    public double Cx, Cy, Top, W, H;
    public double? Seen;
    public readonly List<(double t, double mouth)> MouthHistory = new List<(double, double)>();
}

public class FaceSpeakers
{
    class Person { public int Pid; public double Yaw, Seen; }

    const double WindowS = 1.5;      // mouth movement is judged over this window
    const double MaxJump = 0.25;     // max face movement between two detections (fraction of the image)
    const double TtlS = 1.0;         // a face not seen for this long is dropped
    const double ReIdDeg = 25;       // same person if within this many degrees of where they were
    const double LostS = 4;          // someone who walked off: a lone new face within this time is still them
    const double FocusS = 60;        // the followed person is recognised again this long after leaving the picture ...
    const double FocusDeg = 60;      // ... within this many degrees of where they were
    const double MemoryS = 900;      // forget people not seen for this long

    readonly CameraModel camera;
    readonly List<Person> people = new List<Person>();
    int nextId = 1, nextPid = 1;

    public List<FaceTrack> Tracks { get; private set; } = new List<FaceTrack>();
    /// <summary>Set by the app: the person Reachy follows; kept when they move around.</summary>
    public int? FocusPid;
    public double? LastT { get; private set; }

    public FaceSpeakers(CameraModel camera) { this.camera = camera; }

    public void Update(IEnumerable<Face> faces, double t, double? headYawDeg)
    {
        LastT = t;
        var free = new HashSet<FaceTrack>(Tracks);
        foreach (var f in faces)
        {
            FaceTrack best = null;
            double bestD = MaxJump;
            foreach (var tr in free)
            {
                double d = Math.Sqrt((tr.Cx - f.Cx) * (tr.Cx - f.Cx) + (tr.Cy - f.Cy) * (tr.Cy - f.Cy));
                if (d < bestD) { bestD = d; best = tr; }
            }
            if (best != null) free.Remove(best);
            else { best = new FaceTrack { Id = nextId++ }; Tracks.Add(best); }
            // smooth the box: detections jitter by a few percent from frame to frame
            if (best.Seen == null) { best.Cx = f.Cx; best.Cy = f.Cy; best.Top = f.Top; best.W = f.W; best.H = f.H; }
            else
            {
                best.Cx += 0.5 * (f.Cx - best.Cx); best.Cy += 0.5 * (f.Cy - best.Cy); best.Top += 0.5 * (f.Top - best.Top);
                best.W += 0.5 * (f.W - best.W); best.H += 0.5 * (f.H - best.H);
            }
            best.Seen = t;
            if (f.Mouth != null) best.MouthHistory.Add((t, f.Mouth.Value));
            while (best.MouthHistory.Count > 0 && best.MouthHistory[0].t < t - WindowS) best.MouthHistory.RemoveAt(0);
        }
        Tracks = Tracks.Where(tr => t - tr.Seen < TtlS).ToList();
        Identify(t, headYawDeg);
    }

    /// <summary>Give every track a person id: the known person at that room direction, else a new one.</summary>
    void Identify(double t, double? headYawDeg)
    {
        people.RemoveAll(p => t - p.Seen >= MemoryS);
        var taken = new HashSet<int>(Tracks.Where(tr => tr.Pid != null && tr.Seen == t).Select(tr => tr.Pid.Value));
        foreach (var tr in Tracks)
        {
            if (tr.Seen != t) continue;   // not in this frame
            double? yaw = headYawDeg + AngleDeg(tr);
            if (tr.Pid == null)
            {
                Person best = null;
                if (yaw != null)
                    foreach (var p in people)
                    {
                        double d = Math.Abs(p.Yaw - yaw.Value);
                        if (!taken.Contains(p.Pid) && d < ReIdDeg && (best == null || d < Math.Abs(best.Yaw - yaw.Value))) best = p;
                    }
                best = best ?? WalkedOff(t, taken, yaw);
                if (best == null)
                {
                    best = new Person { Pid = nextPid++, Yaw = yaw ?? 0, Seen = t };
                    people.Add(best);
                }
                tr.Pid = best.Pid;
                taken.Add(best.Pid);
            }
            var person = people.Find(q => q.Pid == tr.Pid);
            if (person != null)
            {
                if (yaw != null) person.Yaw += 0.2 * (yaw.Value - person.Yaw);
                person.Seen = t;
            }
        }
    }

    public FaceTrack Get(int id) => Tracks.Find(tr => tr.Id == id);

    /// <summary>A face far from where anyone sat: if the followed person (or exactly one person) was lost only seconds
    /// ago and is not in view, it is them, having walked to a new place.</summary>
    Person WalkedOff(double t, HashSet<int> taken, double? yaw)
    {
        var focus = people.Find(p => p.Pid == FocusPid && !taken.Contains(p.Pid) && p.Seen < t);
        if (focus != null && t - focus.Seen < FocusS && (yaw == null || Math.Abs(focus.Yaw - yaw.Value) < FocusDeg)) return focus;
        var lost = people.Where(p => !taken.Contains(p.Pid) && t - p.Seen < LostS && p.Seen < t).ToList();
        return lost.Count == 1 ? lost[0] : null;
    }

    /// <summary>Mouth movement: standard deviation of jaw opening over the window (talking ~0.05-0.2, silent ~0.01).</summary>
    public static double Activity(FaceTrack tr)
    {
        if (tr.MouthHistory.Count < 3) return 0;
        double mean = tr.MouthHistory.Average(m => m.mouth);
        return Math.Sqrt(tr.MouthHistory.Average(m => (m.mouth - mean) * (m.mouth - mean)));
    }

    /// <summary>Horizontal angle of the face centre, + = left.</summary>
    public double AngleDeg(FaceTrack tr) => camera.YawDeg(tr.Cx, tr.Cy);

    /// <summary>Most likely speaker track, or null when no face is visible.</summary>
    public FaceTrack Pick(double? doaDeg)
    {
        if (Tracks.Count <= 1) return Tracks.FirstOrDefault();
        FaceTrack best = null;
        double bestScore = -1;
        foreach (var tr in Tracks)
        {
            double score = Math.Min(2, Activity(tr) / 0.05);
            if (doaDeg != null) score += Math.Max(0, 1 - Math.Abs(AngleDeg(tr) - doaDeg.Value) / 30);
            if (score > bestScore) { bestScore = score; best = tr; }
        }
        return best;
    }
}
