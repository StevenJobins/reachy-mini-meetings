// Speaker following. Pure logic, no Unity: port of xr-client/pages/speaker.js and the following part of app.js.
//
// The result is a slowly moving BASE yaw and pitch: "where the current speaker is". The headset adds the user's head
// rotation on top, so looking straight ahead in the headset = looking at the speaker.
// Angles in degrees, robot frame (yaw + = left, pitch + = down), time in seconds.

using System;
using System.Collections.Generic;
using System.Linq;

/// <summary>Mic direction -> confirmed speaker direction -> smooth base yaw.</summary>
public class SpeakerTracker
{
    const double DeadbandDeg = 10;      // ignore direction changes smaller than this
    const double LatencyS = 0.15;       // a mic reading refers to the head yaw this long ago
    const double MaxYawDeg = 150;       // world yaw limit for the speaker direction (body limit ±160)
    const int ConfirmN = 5;             // a new direction needs this many speech readings ...
    const double ConfirmWindowS = 1.0;  // ... within this time (3 in 0.6 s let single reflections turn the head) ...
    const double ConfirmSpreadDeg = 12; // ... within ± this of their median (outliers are ignored, not blocking)

    readonly List<(double t, double yaw)> headHist = new List<(double, double)>();
    readonly List<(double t, double yaw)> recent = new List<(double, double)>();
    readonly RateLimiter limiter = new RateLimiter(80, 80);   // deg/s, deg/s²: smooth, not hectic

    public double Target;
    public double Base => limiter.Pos;

    public void PushHeadYaw(double t, double yaw)
    {
        headHist.Add((t, yaw));
        if (headHist.Count > 100) headHist.RemoveAt(0);
    }

    /// <summary>Measured head yaw at time t (latest sample at or before it).</summary>
    public double HeadYawAt(double t, double fallback)
    {
        for (int i = headHist.Count - 1; i >= 0; i--) if (headHist[i].t <= t) return headHist[i].yaw;
        return headHist.Count > 0 ? headHist[0].yaw : fallback;
    }

    /// <summary>Mic reading: angle in the SDK convention (0 = left, π/2 = front, π = right).</summary>
    public void PushDoa(double t, double angleRad)
    {
        double rel = 90 - angleRad * 180 / Math.PI;   // relative to the head, + = left
        double y0 = headHist.Count > 0 ? headHist[headHist.Count - 1].yaw : Base;
        for (int i = headHist.Count - 1; i >= 0; i--)
            if (headHist[i].t <= t - LatencyS) { y0 = headHist[i].yaw; break; }
        double world = Math.Max(-MaxYawDeg, Math.Min(MaxYawDeg, y0 + rel));
        recent.Add((t, world));
        recent.RemoveAll(r => t - r.t > ConfirmWindowS);

        // Cluster around the median: enough readings agree -> that is the speaker. Single wild readings (noise,
        // reflections, the robot's own motors) neither move the target nor block it.
        var ys = recent.Select(r => r.yaw).OrderBy(y => y).ToList();
        double med = ys[ys.Count / 2];
        var near = ys.Where(y => Math.Abs(y - med) <= ConfirmSpreadDeg).ToList();
        if (near.Count < ConfirmN) return;
        double mean = near.Average();
        if (Math.Abs(mean - Target) > DeadbandDeg) Target = mean;
    }

    public double Step(double dt) => limiter.Step(Target, Math.Max(0.001, Math.Min(0.1, dt)));

    public void Reset()
    {
        Target = 0;
        limiter.Pos = 0;
        limiter.Vel = 0;
        recent.Clear();
    }
}

/// <summary>
/// Whom Reachy looks at. Only confirmed speech counts: mic directions wait in a buffer until the backend hears speech,
/// so noise never turns the robot. When the speaking face is known the robot turns exactly to it and frames it like a
/// camera operator (head centred, 1/3 from the top); in silence it keeps looking at the last person who spoke.
/// </summary>
public class SpeakerFollow
{
    const double PitchUp = 35, PitchDown = 20;   // same as HeadMirror's limits
    const double PitchDead = 6, PitchGain = 0.5, EdgeSearchMax = 10;
    const double PitchRate = 30;                 // deg/s
    // Searching upward for a face: the camera sees ~28° above its axis, so 25° up reaches ~50° above the horizon.
    // A voice without a visible face (out of view, a loudspeaker) must not leave Reachy staring at the ceiling.
    const double SearchUpMax = 25, SearchGiveUpS = 3, SearchReturnRate = 10;

    readonly FaceSpeakers faces;
    readonly CameraModel cam;
    readonly double hfovDeg, frameUpDeg;
    readonly List<(double t, double angle)> doaBuf = new List<(double, double)>();
    readonly List<(double t, double pitch)> pitchHist = new List<(double, double)>();
    double lastSpeechS = double.NegativeInfinity, doaPushedUntil, targetPitch, focusVel;
    double lastSearchS = double.NegativeInfinity;
    bool searching;
    (int pid, double top, double bottom, double t, double yaw, double pitch)? focusLast;

    public readonly SpeakerTracker Tracker = new SpeakerTracker();
    public bool Active;
    public int? FocusPid;
    public double BasePitch { get; private set; }
    public double TargetPitch => targetPitch;
    public string Source { get; private set; } = "";

    public SpeakerFollow(FaceSpeakers faces, CameraModel cam)
    {
        this.faces = faces;
        this.cam = cam;
        hfovDeg = cam.YawDeg(0, 0.5) - cam.YawDeg(1, 0.5);
        frameUpDeg = cam.UpDeg(0.5, 1.0 / 3);
    }

    public void Reset()
    {
        Tracker.Reset();
        FocusPid = null;
        BasePitch = targetPitch = 0;
        focusLast = null;
        focusVel = 0;
        searching = false;
    }

    public void PushHead(double t, double yaw, double pitch)
    {
        Tracker.PushHeadYaw(t, yaw);
        pitchHist.Add((t, pitch));
        if (pitchHist.Count > 100) pitchHist.RemoveAt(0);
    }

    double HeadPitchAt(double t)
    {
        for (int i = pitchHist.Count - 1; i >= 0; i--) if (pitchHist[i].t <= t) return pitchHist[i].pitch;
        return pitchHist.Count > 0 ? pitchHist[0].pitch : 0;
    }

    /// <summary>Buffered, and only used for speech the backend confirms.</summary>
    public void PushDoa(double t, double angleRad, double now)
    {
        doaBuf.Add((t, angleRad));
        doaBuf.RemoveAll(d => d.t < now - 6);
        if (Active && now - lastSpeechS < 1.5) FlushDoa(now - 1.5, now);
    }

    FaceTrack FocusTrack() => FocusPid == null ? null
        : faces.Tracks.Find(t => t.Pid == FocusPid && (faces.LastT ?? 0) - t.Seen < 0.5);

    /// <summary>Feed the buffered mic directions since fromS into the tracker, each reading once, in order.</summary>
    void FlushDoa(double fromS, double now)
    {
        // While the followed person is in the picture, the mic direction (±10-20°, plus wall reflections) only counts
        // when it points clearly outside the picture: someone out of view speaks. Letting both steer made the head
        // twitch and turn away. If their mouth moves, they are the one talking and the mic has nothing to add.
        var focus = FocusTrack();
        bool focusTalking = focus != null && FaceSpeakers.Activity(focus) > 0.03;
        foreach (var (t, a) in doaBuf)
        {
            if (t <= doaPushedUntil || t < fromS || focusTalking) continue;
            if (focus != null && Math.Abs(90 - a * 180 / Math.PI) < hfovDeg / 2 + 10) continue;
            Tracker.PushDoa(t, a);
            Source = "mic";
        }
        doaPushedUntil = now;
    }

    /// <summary>Instant "someone is speaking" from the backend's neural VAD, long before any text.</summary>
    public void OnVad(bool speaking, double now)
    {
        if (!speaking || !Active) return;
        lastSpeechS = now;
        FlushDoa(now - 0.6, now);
        double? doaRel = doaBuf.Count > 0 ? 90 - doaBuf[doaBuf.Count - 1].angle * 180 / Math.PI : (double?)null;
        var tr = faces.Pick(doaRel);
        if (tr != null && tr.Seen == faces.LastT)
        {
            FocusPid = tr.Pid;
            FrameFocus();
        }
        else if (!faces.Tracks.Any(t => (faces.LastT ?? 0) - t.Seen < 1.5)
                 && (focusLast == null || (faces.LastT ?? 0) - focusLast.Value.t > 1.5)
                 && (doaRel == null || Math.Abs(doaRel.Value) < 35))
        {
            // Someone talks in front of Reachy but no face is in the picture: their head is above it (standing, or
            // close to the robot). Look up step by step until the face shows up.
            targetPitch = Math.Max(Math.Min(targetPitch, -SearchUpMax), targetPitch - 3);
            searching = true;
            lastSearchS = now;
            Source = "search up";
        }
    }

    /// <summary>A caption about speech going on now. A late final must not pull the head back to the previous speaker.</summary>
    public void OnCaption(bool finalWithTranslation, double ageS, double durationS, FaceTrack track, double now)
    {
        if (finalWithTranslation || ageS > 1.2) return;
        lastSpeechS = now;
        if (!Active) return;
        FlushDoa(now - durationS - 0.7, now);
        if (track == null) return;
        FocusPid = track.Pid;
        FrameFocus();
    }

    /// <summary>Call after every face update.</summary>
    public void FrameFocus()
    {
        if (!Active || FocusPid == null) return;
        double t = faces.LastT ?? 0;
        var tr = faces.Tracks.Find(x => x.Pid == FocusPid && x.Seen == faces.LastT);
        if (tr == null)
        {
            // A face cut off at the image edge is no longer detected: keep tilting that way for a moment.
            if (focusLast is var (_, top, bottom, lastT, _, pitch) && t - lastT < 2.5)
            {
                if (top < 0.12) targetPitch = Math.Max(Math.Max(-PitchUp, pitch - EdgeSearchMax), targetPitch - 1);
                else if (bottom > 0.9) targetPitch = Math.Min(Math.Min(PitchDown, pitch + EdgeSearchMax), targetPitch + 1);
                Source = "edge search";
            }
            return;
        }
        double offX = faces.AngleDeg(tr);
        double yawNow = Tracker.HeadYawAt(t, Tracker.Base) + offX;   // where the face is in the room
        // Lead a moving person: below 8 °/s it counts as standing still (no lead, no jitter).
        if (focusLast is var (pid, _, _, lt, ly, _) && pid == FocusPid && t > lt && t - lt < 0.5)
            focusVel += 0.25 * ((yawNow - ly) / (t - lt) - focusVel);
        else
            focusVel = 0;
        focusLast = (FocusPid.Value, tr.Top, tr.Top + tr.H, t, yawNow, targetPitch);
        bool moving = Math.Abs(focusVel) > 8;
        // Dead zone: a face that is already well placed does not move the head at all.
        if (moving || Math.Abs(offX) > 4)
        {
            double lead = moving ? Math.Max(-15, Math.Min(15, focusVel * 0.3)) : 0;
            double yaw = Math.Max(-150, Math.Min(150, yawNow + lead));
            if (Math.Abs(yaw - Tracker.Target) > 2) Tracker.Target = yaw;
        }
        double up = cam.UpDeg(tr.Cx, tr.Cy);
        if (Math.Abs(up - frameUpDeg) > PitchDead)
        {
            double pitch = HeadPitchAt(t) - (up - frameUpDeg);   // pitch + = look down
            targetPitch += PitchGain * (Math.Max(-PitchUp, Math.Min(PitchDown, pitch)) - targetPitch);
        }
        searching = false;
        Source = $"face {tr.Pid}";
    }

    /// <summary>Advance the smooth base; returns the base yaw.</summary>
    public double Step(double dt, double now)
    {
        if (searching && now - lastSearchS > SearchGiveUpS)
        {
            // nobody found: back to level
            targetPitch += Math.Max(-SearchReturnRate * dt, Math.Min(SearchReturnRate * dt, -targetPitch));
            if (Math.Abs(targetPitch) < 1) searching = false;
        }
        double b = Tracker.Step(dt);
        BasePitch += Math.Max(-PitchRate * dt, Math.Min(PitchRate * dt, targetPitch - BasePitch));
        return b;
    }
}
