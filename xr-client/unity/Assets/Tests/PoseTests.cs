// Pose.cs against xr-client/pages/pose.js (PoseGolden.txt) and the axis conventions.

using System;
using System.Globalization;
using System.IO;
using System.Linq;
using NUnit.Framework;

public class PoseTests
{
    const double Tol = 1e-9;

    static double[][] Golden(string kind, string file = "Assets/Tests/PoseGolden.txt") => File.ReadAllLines(file)
        .Where(l => l.StartsWith(kind + " "))
        .Select(l => l.Split(' ').Skip(1).Select(v => double.Parse(v, CultureInfo.InvariantCulture)).ToArray())
        .ToArray();

    [Test]
    public void AnglesMatchPoseJs()
    {
        foreach (var v in Golden("angles"))
        {
            var q = PoseMath.RobotToHeadset(v[0], v[1], v[2]);
            Assert.That(new[] { q.X, q.Y, q.Z, q.W }, Is.EqualTo(new[] { v[3], v[4], v[5], v[6] }).Within(Tol));
            var a = PoseMath.HeadsetToRobot(q);
            Assert.That(new[] { a.Roll, a.Pitch, a.Yaw }, Is.EqualTo(new[] { v[7], v[8], v[9] }).Within(Tol));
        }
    }

    [Test]
    public void RateLimiterMatchesPoseJs()
    {
        var lim = new RateLimiter(150, 800);
        foreach (var v in Golden("limiter"))
        {
            Assert.That(lim.Step(v[0], v[1]), Is.EqualTo(v[2]).Within(Tol));
            Assert.That(lim.Vel, Is.EqualTo(v[3]).Within(Tol));
        }
    }

    [Test]
    public void HeadMirrorMatchesPoseJs()
    {
        var m = new HeadMirror();
        foreach (var v in Golden("mirror"))
        {
            var o = m.Step(new RobotAngles(v[0], v[1], v[2]), v[3]);
            Assert.That(new[] { o.Roll, o.Pitch, o.Yaw, o.BodyYaw }, Is.EqualTo(new[] { v[4], v[5], v[6], v[7] }).Within(Tol));
        }
    }

    // Unity's own quaternions for a head turn, as UnityEngine.Quaternion.Euler gives them
    static RobotAngles FromUnityAxis(double ax, double ay, double az, double deg)
    {
        double half = deg / PoseMath.Deg / 2, s = Math.Sin(half);
        return PoseMath.HeadsetToRobot(Quat.FromUnity(ax * s, ay * s, az * s, Math.Cos(half)));
    }

    [Test]
    public void UnityHeadTurnsGiveTheDocumentedSigns()
    {
        Assert.That(FromUnityAxis(0, 1, 0, -30).Yaw, Is.EqualTo(30).Within(Tol), "turn left 30 -> yaw +30");
        Assert.That(FromUnityAxis(1, 0, 0, 20).Pitch, Is.EqualTo(20).Within(Tol), "look down 20 -> pitch +20");
        Assert.That(FromUnityAxis(0, 0, 1, -15).Roll, Is.EqualTo(15).Within(Tol), "tilt right 15 -> roll +15");
    }

    [Test]
    public void DaemonMessageIsTheExpectedJson()
    {
        var t = new HeadTarget { Roll = 0, Pitch = 180 / Math.PI, Yaw = -90 / Math.PI, BodyYaw = 0.5 * 180 / Math.PI };
        Assert.That(t.ToDaemonJson(), Is.EqualTo(
            "{\"target_head_pose\":{\"roll\":0,\"pitch\":1,\"yaw\":-0.5},\"target_antennas\":[0,0],\"target_body_yaw\":0.5}"));
    }

    [Test]
    public void RecenterMakesTheCurrentPoseStraightAhead()
    {
        var r = new Recenter();
        var q = PoseMath.RobotToHeadset(5, -10, 40);
        r.Set(q);
        var a = PoseMath.HeadsetToRobot(r.ToRelative(q));
        Assert.That(new[] { a.Roll, a.Pitch, a.Yaw }, Is.EqualTo(new[] { 0.0, 0.0, 0.0 }).Within(Tol));
    }

    [Test]
    public void CameraModelMatchesCameraJs()
    {
        var c = CameraModel.Calibrated();
        const string file = "Assets/Tests/CameraGolden.txt";
        foreach (var v in Golden("unproject", file))
        {
            Assert.That(c.Unproject(v[0], v[1], out var x, out var y, out var z), Is.True);
            Assert.That(new[] { x, y, z }, Is.EqualTo(new[] { v[2], v[3], v[4] }).Within(1e-8));
        }
        foreach (var v in Golden("project", file))
        {
            Assert.That(c.Project(v[0], v[1], v[2], out var pu, out var pv), Is.True);
            Assert.That(new[] { pu, pv }, Is.EqualTo(new[] { v[3], v[4] }).Within(1e-8));
        }
    }

    [Test]
    public void PoseHistoryInterpolatesAndClamps()
    {
        var h = new PoseHistory();
        h.Add(1, new RobotAngles(0, 0, 0));
        h.Add(2, new RobotAngles(10, 20, 30));
        Assert.That(h.TryAt(1.5, out var mid), Is.True);
        Assert.That(new[] { mid.Roll, mid.Pitch, mid.Yaw }, Is.EqualTo(new[] { 5.0, 10.0, 15.0 }).Within(Tol));
        h.TryAt(0, out var before);
        Assert.That(before.Yaw, Is.EqualTo(0).Within(Tol));
        h.TryAt(9, out var after);
        Assert.That(after.Yaw, Is.EqualTo(30).Within(Tol));
    }

    [Test]
    public void FollowGuardLocksOnlyWhileTheRobotDoesWhatWeCommand()
    {
        var measured = new PoseHistory();
        var commanded = new PoseHistory();
        var g = new FollowGuard();
        var good = new RobotAngles(0, 0, 10);
        for (double t = 0; t < 1; t += 0.02) { measured.Add(t, good); commanded.Add(t, good); g.Update(true, measured, commanded, 0.1, t); }
        Assert.That(g.Following, Is.True, "locks after the hold time");
        for (double t = 1; t < 2; t += 0.02) { measured.Add(t, new RobotAngles(0, 26, 10)); commanded.Add(t, good); g.Update(true, measured, commanded, 0.1, t); }
        Assert.That(g.Following, Is.True, "11..26 deg off: above the entry tolerance but inside the exit tolerance");
        for (double t = 2; t < 3; t += 0.02) { measured.Add(t, new RobotAngles(0, 45, 10)); commanded.Add(t, good); g.Update(true, measured, commanded, 0.1, t); }
        Assert.That(g.Following, Is.False, "stuck 45 deg off");
        for (double t = 3; t < 4; t += 0.02) { measured.Add(t, good); commanded.Add(t, good); g.Update(false, measured, commanded, 0.1, t); }
        Assert.That(g.Following, Is.False, "not active");
        for (double t = 4; t < 6; t += 0.02) { commanded.Add(t, good); g.Update(true, measured, commanded, 0.1, t); }
        Assert.That(g.Following, Is.False, "no fresh poses for 2 s");
    }

    [Test]
    public void PoseClockIgnoresNetworkDelay()
    {
        var c = new PoseClock();
        const double offset = 1000;
        double[] delays = { 0.005, 0.004, 0.120, 0.100, 0.080, 0.006, 0.005, 0.300, 0.004 };
        for (int i = 0; i < delays.Length; i++)
        {
            double sender = i * 0.0167;
            double local = c.ToLocal(sender, sender + offset + delays[i]);
            if (i >= 1) Assert.That(local - sender - offset, Is.LessThan(0.006), $"sample {i}");
        }
    }

    [Test]
    public void PoseBlendIsTheDelayedCommandPlusTheOffset()
    {
        const double lag = 0.13, offset = 3;
        double Cmd(double t) => 30 * Math.Sin(t * 1.5);
        var commanded = new PoseHistory();
        var b = new PoseBlend(lag);
        for (double t = 0; t < 2.4; t += 0.0139) commanded.Add(t, new RobotAngles(0, 0, Cmd(t)));
        // Stair-stepped robot pose: new value every 40 ms
        for (double t = 0.3; t < 2.4; t += 0.0167)
            b.AddMeasured(t, new RobotAngles(0, 0, Cmd(Math.Floor((t - lag) / 0.04) * 0.04) + offset), commanded);
        Assert.That(b.TryAt(2.3, commanded, out var a), Is.True);
        Assert.That(a.Yaw, Is.EqualTo(Cmd(2.3 - lag) + offset).Within(1.0));
    }

    static byte[] Pcm(int samples, short value)
    {
        var b = new byte[samples * 2];
        for (int i = 0; i < samples; i++) { b[2 * i] = (byte)(value & 0xff); b[2 * i + 1] = (byte)(value >> 8); }
        return b;
    }

    [Test]
    public void AudioBufferWaitsForTheTargetThenPlaysThenFades()
    {
        var a = new AudioJitterBuffer();
        var output = new float[320];
        a.PushPcm16(Pcm(1000, 16384), 2000);   // 62 ms, below the 120 ms target
        a.Read(output, 0, 320);
        Assert.That(output[319], Is.EqualTo(0).Within(1e-6), "still filling");
        a.PushPcm16(Pcm(1000, 16384), 2000);
        a.Read(output, 0, 320);
        Assert.That(output[0], Is.EqualTo(0.5f).Within(1e-3), "playing");
        for (int i = 0; i < 6; i++) a.Read(output, 0, 320);
        Assert.That(a.Underruns, Is.EqualTo(1));
        Assert.That(output[319], Is.LessThan(0.01f), "faded out");
    }

    [Test]
    public void AudioBufferDropsOldSoundAfterAStall()
    {
        var a = new AudioJitterBuffer();
        a.PushPcm16(Pcm(16000, 1000), 32000);   // 1 s burst
        a.Read(new float[160], 0, 160);
        Assert.That(a.BufferedMs, Is.LessThan(125));
    }

    [Test]
    public void SpeechFilterRemovesHumAndKeepsVoiceBand()
    {
        double Rms(double hz)
        {
            var f = new SpeechFilter();
            var s = new float[8000];
            for (int i = 0; i < s.Length; i++) s[i] = (float)(0.01 * Math.Sin(2 * Math.PI * hz * i / 16000));
            f.Process(s, 0, s.Length);
            return Math.Sqrt(s.Skip(4000).Average(v => v * v));
        }
        Assert.That(Rms(50), Is.LessThan(Rms(1000) / 3), "hum");
    }

    [Test]
    public void FaceSpeakersMatchesSpeakersJs()
    {
        double D(string v) => double.Parse(v, CultureInfo.InvariantCulture);
        var s = new FaceSpeakers(CameraModel.Calibrated());
        foreach (var line in File.ReadAllLines("Assets/Tests/SpeakersGolden.txt").Where(l => l.StartsWith("step ")))
        {
            var p = line.Split(' ');
            double? head = p[2] == "null" ? (double?)null : D(p[2]), doa = p[3] == "null" ? (double?)null : D(p[3]);
            var faces = p[4] == "-" ? new Face[0] : p[4].Split(';').Select(f =>
            {
                var v = f.Split(',').Select(D).ToArray();
                return new Face { Cx = v[0], Cy = v[1], Top = v[2], W = v[3], H = v[4], Mouth = v[5] < 0 ? (double?)null : v[5] };
            }).ToArray();
            s.Update(faces, D(p[1]), head);
            var expected = p[5] == "-" ? new string[0] : p[5].Split(';');
            Assert.That(s.Tracks.Count, Is.EqualTo(expected.Length), line);
            for (int i = 0; i < expected.Length; i++)
            {
                var e = expected[i].Split(',');
                Assert.That(s.Tracks[i].Id, Is.EqualTo(int.Parse(e[0])), line);
                Assert.That(s.Tracks[i].Pid, Is.EqualTo(int.Parse(e[1])), line);
                Assert.That(s.Tracks[i].Cx, Is.EqualTo(D(e[2])).Within(1e-8), line);
            }
            var pick = s.Pick(doa);
            Assert.That(pick == null ? "null" : pick.Id.ToString(), Is.EqualTo(p[6]), line);
        }
    }

    [Test]
    public void BacklashSwallowsSmallMovesAndFollowsBigOnes()
    {
        var b = new Backlash(3);
        b.Step(0);
        Assert.That(b.Step(2.9), Is.EqualTo(0), "inside the gap");
        Assert.That(b.Step(-2.9), Is.EqualTo(0), "still inside");
        Assert.That(b.Step(10), Is.EqualTo(7).Within(Tol), "pushed along, 3° behind");
        Assert.That(b.Step(8), Is.EqualTo(7).Within(Tol), "reversal: stays until the far side of the gap");
        Assert.That(b.Step(3), Is.EqualTo(6).Within(Tol));
    }

    [Test]
    public void RoomScanStopsOnlyInTheHeadRangeAndWaitsForTheHeadToSettle()
    {
        var s = new RoomScan();
        s.Start(0);
        for (int i = 0; i < s.Total; i++)
        {
            var tg = s.Target;
            Assert.That(Math.Abs(tg.Yaw), Is.LessThanOrEqualTo(60), "inside the head/body window: the body stays still");
            Assert.That(tg.Pitch, Is.InRange(-35.0, 20.0));
            double t0 = i * 1.0;
            Assert.That(s.Step(t0 + 0.1, tg, 5, 0.1), Is.False, "still moving");
            Assert.That(s.Step(t0 + 0.2, new RobotAngles(0, tg.Pitch, tg.Yaw + 4), 0.2, 0.1), Is.False, "steady starts, 4° short is near enough");
            Assert.That(s.Step(t0 + 0.7, new RobotAngles(0, tg.Pitch, tg.Yaw + 4), 0.2, 0.1), Is.True, "settled + video delay");
            s.Shot(t0 + 0.7);
        }
        Assert.That(s.Active, Is.False);
    }

    [Test]
    public void RoomScanGivesUpOnAStuckHead()
    {
        var s = new RoomScan();
        s.Start(0);
        Assert.That(s.Step(2.9, new RobotAngles(), 0, 0), Is.False);
        Assert.That(s.Step(3.1, new RobotAngles(), 0, 0), Is.True);
    }

    [Test]
    public void PatchSlotsReuseNearbyDirectionsAndDropTheOldest()
    {
        var p = new PatchSlots();
        var a = p.Take(0, 0, 1, out _);
        Assert.That(p.Take(5, 10, 2, out _).Id, Is.EqualTo(a.Id), "same direction: replaced");
        Assert.That(p.Take(0, 90, 3, out _).Id, Is.Not.EqualTo(a.Id), "new direction: new slot");
        Assert.That(p.Take(0, -175, 4, out _).Id, Is.Not.EqualTo(a.Id));
        Assert.That(p.Take(0, 178, 5, out _).Id, Is.EqualTo(3 - 1), "wraps at ±180");
        for (int i = 0; i < 40; i++) p.Take(i % 2 == 0 ? -30 : 30, i * 30, 10 + i, out _);
        Assert.That(p.Count, Is.LessThanOrEqualTo(PatchSlots.Max));
    }
}
