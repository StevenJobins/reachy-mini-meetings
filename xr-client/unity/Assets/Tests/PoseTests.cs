// Pose.cs against xr-client/pages/pose.js (PoseGolden.txt) and the axis conventions.

using System;
using System.Globalization;
using System.IO;
using System.Linq;
using NUnit.Framework;

public class PoseTests
{
    const double Tol = 1e-9;

    static double[][] Golden(string kind) => File.ReadAllLines("Assets/Tests/PoseGolden.txt")
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
}
