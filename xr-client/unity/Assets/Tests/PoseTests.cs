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
    public void AudioBufferWaitsForALittleThenPlaysThenFades()
    {
        var a = new AudioJitterBuffer();
        var output = new float[320];
        a.PushPcm16(Pcm(1200, 16384), 2400);   // 75 ms, below the 140 ms it starts at
        a.Read(output, 0, 320);
        Assert.That(output[319], Is.EqualTo(0).Within(1e-6), "still filling");
        a.PushPcm16(Pcm(1200, 16384), 2400);
        a.Read(output, 0, 320);
        Assert.That(output[0], Is.EqualTo(0.5f).Within(1e-3), "playing");
        double before = a.SafetyMs;
        for (int i = 0; i < 14; i++) a.Read(output, 0, 320);
        Assert.That(a.Underruns, Is.EqualTo(1));
        Assert.That(output[319], Is.LessThan(0.01f), "faded out");
        Assert.That(a.SafetyMs, Is.GreaterThan(before + 30), "a gap: keeps more in hand from now on");
    }

    [Test]
    public void AudioBufferHoldsLittleOnAnEvenNetwork()
    {
        var a = new AudioJitterBuffer();
        var output = new float[341];   // what the mixer asks for: ~21 ms
        a.PushPcm16(Pcm(4800, 1000), 9600);   // 300 ms had piled up
        var rng = new Random(1);
        int owed = 0, wait = 0;
        for (int i = 0; i < 2800; i++)   // 60 s, packets of 40 ms arriving up to 15 ms late
        {
            owed += 341;
            if (owed >= 640 + wait) { a.PushPcm16(Pcm(640, 1000), 1280); owed -= 640; wait = rng.Next(240); }
            a.Read(output, 0, 341);
        }
        Assert.That(a.Underruns, Is.EqualTo(0));
        Assert.That(a.BufferedMs, Is.LessThan(150), "the 300 ms are gone");
    }

    [Test]
    public void AudioBufferKeepsMoreOnAnUnevenNetworkAndStopsHavingGaps()
    {
        var a = new AudioJitterBuffer();
        var output = new float[320];   // 20 ms
        int late = 0, gapsEarly = 0;
        for (int i = 0; i < 6000; i++)   // 2 min: after 10 s, every 2 s the Wi-Fi hangs for 160 ms, then delivers it all at once
        {
            if (i > 500 && i % 100 < 8) late += 320;
            else { a.PushPcm16(Pcm(320 + late, 1000), 2 * (320 + late)); late = 0; }
            a.Read(output, 0, 320);
            if (i == 1500) gapsEarly = a.Underruns;
        }
        Assert.That(gapsEarly, Is.GreaterThan(0).And.LessThan(6), "a few gaps while it learns");
        Assert.That(a.Underruns - gapsEarly, Is.LessThanOrEqualTo(3), "then (almost) none: 45 hangs in that time");
    }

    [Test]
    public void AudioBufferKeepsTheWordsAfterAStallAndCatchesUp()
    {
        var a = new AudioJitterBuffer();
        var output = new float[640];
        a.PushPcm16(Pcm(16000, 1000), 32000);   // 1 s in one burst after a hang
        a.Read(output, 0, 640);
        Assert.That(a.BufferedMs, Is.GreaterThan(900), "nothing thrown away");
        for (int i = 0; i < 400; i++)   // 16 s of steady sound, 40 ms at a time
        {
            a.PushPcm16(Pcm(640, 1000), 1280);
            a.Read(output, 0, 640);
        }
        Assert.That(a.BufferedMs, Is.LessThan(200), "played faster until the delay was gone");
        Assert.That(a.Underruns, Is.EqualTo(0));
    }

    [Test]
    public void AudioBufferDropsOldSoundAfterALongStall()
    {
        var a = new AudioJitterBuffer();
        a.PushPcm16(Pcm(32000, 1000), 64000);   // 2 s burst
        a.Read(new float[160], 0, 160);
        Assert.That(a.BufferedMs, Is.LessThan(150));
    }

    [Test]
    public void AudioBufferJoinsAFrameCutInsideASample()
    {
        var a = new AudioJitterBuffer();
        var whole = Pcm(4000, 16384);
        var first = whole.Take(2401).ToArray();
        var rest = whole.Skip(2401).ToArray();
        a.PushPcm16(first, first.Length);
        a.PushPcm16(rest, rest.Length);
        Assert.That(a.BufferedMs, Is.EqualTo(250).Within(0.1));
        var output = new float[3000];
        a.Read(output, 0, 3000);
        Assert.That(output.Min(), Is.EqualTo(0.5f).Within(1e-3), "no sample torn apart at the cut");
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
    public void SpeechFilterBringsQuietAndLoudVoicesToASimilarLevel()
    {
        double Peak(double amplitude)
        {
            var f = new SpeechFilter();
            var s = new float[48000];   // 3 s
            for (int i = 0; i < s.Length; i++) s[i] = (float)(amplitude * Math.Sin(2 * Math.PI * 1000 * i / 16000));
            f.Process(s, 0, s.Length);
            return s.Skip(40000).Max(v => Math.Abs(v));
        }
        double quiet = Peak(0.02), loud = Peak(0.3);   // someone far from the robot, someone next to it
        Assert.That(quiet, Is.GreaterThan(0.15), "a far voice is brought up");
        Assert.That(loud, Is.LessThan(0.95), "a near voice does not clip");
        Assert.That(loud / quiet, Is.LessThan(2.5));
    }

    [Test]
    public void SpeechFilterMakesAFarVoiceLoudEnoughWithoutClipping()
    {
        var f = new SpeechFilter();
        var rng = new System.Random(1);
        var s = new float[16000 * 6];
        double prev = 0;
        for (int i = 0; i < s.Length; i++)   // speech-like: low-passed noise in syllables, peaks around -33 dBFS
        {
            double t = i / 16000.0;
            double env = Math.Sin(2 * Math.PI * 3 * t) > 0 ? Math.Abs(Math.Sin(2 * Math.PI * 4 * t)) : 0;
            prev = 0.9 * prev + (rng.NextDouble() * 2 - 1);
            s[i] = (float)(0.0035 * prev * env);
        }
        f.Process(s, 0, s.Length);
        var tail = s.Skip(16000 * 2).ToArray();
        double rmsDb = 20 * Math.Log10(Math.Sqrt(tail.Average(v => (double)v * v)));
        Assert.That(rmsDb, Is.GreaterThan(-24), "loud enough in the headset (was about -32 dBFS)");
        Assert.That(tail.Max(v => Math.Abs(v)), Is.LessThan(1.0f), "soft limit, no hard clipping");
    }

    [Test]
    public void VoiceGateOpensOnSpeechNotOnAClickAndHoldsThroughShortPauses()
    {
        const double block = 0.014;   // one frame at 72 fps
        double voice = Math.Pow(10, -25 / 20.0), quiet = Math.Pow(10, -60 / 20.0);
        var g = new VoiceGate();
        Assert.That(g.Step(voice, block), Is.False, "one loud block (a click) does not open it");
        g.Step(quiet, block);
        Assert.That(g.Step(voice, block), Is.False, "the click before does not count towards the next");
        for (int i = 0; i < 5; i++) g.Step(voice, block);
        Assert.That(g.Open, Is.True, "speech for ~80 ms opens it");
        for (int i = 0; i < 30; i++) g.Step(quiet, block);   // 0.42 s between words
        Assert.That(g.Open, Is.True, "a pause between words keeps it open");
        g.Step(voice, block);
        for (int i = 0; i < 50; i++) g.Step(quiet, block);   // 0.7 s
        Assert.That(g.Open, Is.False, "closes after the sentence");
        Assert.That(new VoiceGate().Step(Math.Pow(10, -45 / 20.0), 1.0), Is.False, "a quiet room stays out");
    }

    [Test]
    public void SubtitleLinesUpdateByIdAndKeepTheNewestThree()
    {
        var s = new Subtitles();
        s.Caption(1, false, "Hallo zusam", null, "de", null, 0);
        s.Caption(1, true, "Hallo zusammen", null, "de", 2, 1);
        s.Caption(1, true, "Hallo zusammen", "Hello everyone", "de", null, 2);
        var l = s.Shown(2).Single();
        Assert.That(l.Text, Is.EqualTo("Hello everyone"));
        Assert.That(l.Original, Is.EqualTo("de: Hallo zusammen"));
        Assert.That(l.Speaker, Is.EqualTo(2), "an update without a speaker keeps the one known");
        Assert.That(l.Final, Is.True);
        for (int id = 2; id <= 5; id++) s.Caption(id, true, $"line {id}", null, "en", id, 3);
        Assert.That(s.Shown(3).Select(x => x.Text), Is.EqualTo(new[] { "line 3", "line 4", "line 5" }));
        s.Caption(5, true, "", null, "en", null, 4);
        Assert.That(s.Shown(4).Select(x => x.Text), Is.EqualTo(new[] { "line 2", "line 3", "line 4" }), "empty final: removed");
        Assert.That(s.Shown(3 + Subtitles.FinalS + 0.1), Is.Empty, "faded out");
    }

    [Test]
    public void ALineKeepsWhoSaidItAndTakesANameOrAnotherSpeakerOnlyFromTheBackend()
    {
        var s = new Subtitles();
        s.Caption(1, false, "Guten Mor", null, "de", 2, 0, translating: true);
        Assert.That(s.Shown(0).Single().Waiting, Is.True, "being translated: dots, not the original");
        Assert.That(s.Shown(0).Single().Speaker, Is.EqualTo(2), "who it is from stands there from the first word");
        s.Caption(1, false, "Guten Morgen", "Good mor", "de", null, 0.5);
        s.Caption(1, true, "Guten Morgen zusammen", null, "de", 2, 1, translating: true);
        var l = s.Shown(1).Single();
        Assert.That((l.Text, l.Speaker, l.Waiting), Is.EqualTo(("Good mor", (int?)2, false)), "the translation so far stays");
        s.Caption(1, true, "Guten Morgen zusammen", "Good morning everyone", "de", 2, 1.5, name: "Anna");
        l = s.Shown(1.5).Single();
        Assert.That((l.Text, l.Speaker, l.Name), Is.EqualTo(("Good morning everyone", (int?)2, "Anna")));
        s.Caption(2, true, "I agree", null, "en", 2, 2);
        s.Named(2, "Anna");
        Assert.That(s.Shown(2).Select(x => x.Name), Is.All.EqualTo("Anna"), "a name learnt later goes on every line of theirs");
        s.Caption(2, true, "I agree", null, "en", 3, 3);   // the backend found out it was someone else
        l = s.Shown(3).Last();
        Assert.That((l.Speaker, l.Name), Is.EqualTo(((int?)3, (string)null)), "not under the old name");
    }

    [Test]
    public void OnePersonOneLabelAndColourEverywhere()
    {
        Assert.That(SpeakerLabel.Name(1, null), Is.EqualTo("Speaker A"));
        Assert.That(SpeakerLabel.Name(4, ""), Is.EqualTo("Speaker D"));
        Assert.That(SpeakerLabel.Name(4, "Dominic"), Is.EqualTo("Dominic"));
        Assert.That(SpeakerLabel.Name(null, null), Is.EqualTo(""));
        Assert.That(SpeakerLabel.Color(null), Is.EqualTo(SpeakerLabel.Unknown));
        var first = Enumerable.Range(1, SpeakerLabel.Colors.Length).Select(i => SpeakerLabel.Color(i)).ToList();
        Assert.That(first.Distinct().Count(), Is.EqualTo(first.Count), "the first eight people have eight colours");
        Assert.That(SpeakerLabel.Color(SpeakerLabel.Colors.Length + 1), Is.EqualTo(first[0]));
    }

    [Test]
    public void YouLineIsLiveWhileTalkingAndUntilTheTextComes()
    {
        var s = new Subtitles();
        s.Caption(1, true, "room", null, "en", 1, 0);
        s.Mic(true, 1);
        Assert.That(s.Shown(1).Last().Live, Is.True, "talking: live line at the end");
        s.Mic(false, 2);
        s.Caption(2, false, "someone else", null, "en", 2, 2.5);
        Assert.That(s.Shown(3).Last().Live, Is.True, "released, waiting for the text");
        s.Me("Can you hear me?", 3.2);
        var shown = s.Shown(3.2);
        Assert.That(shown.Any(x => x.Live), Is.False);
        Assert.That(shown.Last().You && shown.Last().Text == "Can you hear me?", Is.True);
        s.Mic(true, 4);
        s.Mic(false, 4.1);
        Assert.That(s.Shown(4.1 + Subtitles.YouWaitS + 0.1).Any(x => x.Live), Is.False, "no text came: gone after a while");
    }

    [Test]
    public void YawRangeWaitsAtTheEdgeAndNeverComesBackByItself()
    {
        Assert.That(YawRange.Fold(70, 0), Is.EqualTo(70), "inside: unchanged");
        Assert.That(YawRange.Fold(-100, 0), Is.EqualTo(-100));
        Assert.That(YawRange.Fold(130, 100), Is.EqualTo(YawRange.MaxDeg), "past the edge: waits there");
        Assert.That(YawRange.Fold(-140, -100), Is.EqualTo(-YawRange.MaxDeg));
        // turning right all the way round, the headset yaw wrapping at 180
        double last = 0;
        foreach (double user in new double[] { 50, 100, 130, 160, 180, -179, -160, -141 })
        {
            last = YawRange.Fold(user, last);
            if (Math.Abs(user) > 100) Assert.That(last, Is.EqualTo(YawRange.MaxDeg), $"back to the room at {user}: still at the right edge");
        }
        last = YawRange.Fold(-139, last);
        Assert.That(last, Is.EqualTo(-YawRange.MaxDeg), "coming round to the front on the left: over to the left edge");
        Assert.That(YawRange.Fold(-90, last), Is.EqualTo(-90), "and with the user again");
        // looking back and forth around the wrap does not make it change sides
        last = 100;
        foreach (double user in new double[] { 178, -178, 176, -172, 179 })
            Assert.That(last = YawRange.Fold(user, last), Is.EqualTo(YawRange.MaxDeg));
    }

    [Test]
    public void AFullTurnOfTheUserDoesNotDragReachyRound()
    {
        // The user turns left twice all the way round at 90°/s, the headset yaw wrapping at ±180 like atan2
        var m = new HeadMirror();
        const double dt = 1 / 72.0;
        HeadTarget prev = m.Step(new RobotAngles(), dt);
        double folded = 0;
        for (double t = 0; t < 8; t += dt)
        {
            double user = (90 * t + 180) % 360 - 180;
            var o = m.Step(new RobotAngles(0, 0, folded = YawRange.Fold(user, folded)), dt);
            // the rate limiters brake in steps of dt, so they may overshoot the edge by up to ~0.5°
            Assert.That(Math.Abs(o.Yaw), Is.LessThanOrEqualTo(YawRange.MaxDeg + 1), $"head at t={t:0.00}");
            Assert.That(Math.Abs(o.BodyYaw), Is.LessThanOrEqualTo(YawRange.MaxDeg - 65 + 1), $"body at t={t:0.00}");
            Assert.That(Math.Abs(o.Yaw - prev.Yaw) / dt, Is.LessThanOrEqualTo(150 + 1e-6), "head speed limit");
            Assert.That(Math.Abs(o.BodyYaw - prev.BodyYaw) / dt, Is.LessThanOrEqualTo(90 + 1e-6), "body speed limit");
            prev = o;
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
