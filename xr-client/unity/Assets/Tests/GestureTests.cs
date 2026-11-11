// Gestures.cs: the "I want to talk" wave and the amplified nod / head shake.

using System;
using NUnit.Framework;

public class GestureTests
{
    static double Swing(Func<double, RobotAngles> head, Func<RobotAngles, double> axis, out NodShake g)
    {
        g = new NodShake();
        double most = 0;
        for (int i = 0; i < 72 * 4; i++)
        {
            double t = i / 72.0;
            var a = head(t);
            var o = g.Step(a, t, 1 / 72.0);
            if (t > 2) most = Math.Max(most, Math.Abs(axis(o) - axis(head(0))));
        }
        return most;
    }

    [Test]
    public void ANodMovesTheAntennasAndNotTheCamera()
    {
        double head = Swing(t => new RobotAngles(0, 6 * Math.Sin(2 * Math.PI * 1.8 * t), 10), a => a.Pitch, out var g);
        Assert.That(g.Nodding(3.9), Is.True);
        Assert.That(head, Is.LessThan(1.5), "the head holds still: the picture does not go up and down");
        var n = new NodShake();
        double both = 0, apart = 0;
        for (int i = 0; i < 72 * 4; i++)
        {
            double t = i / 72.0;
            n.Step(new RobotAngles(0, 6 * Math.Sin(2 * Math.PI * 1.8 * t), 10), t, 1 / 72.0);
            n.Antennas(out var r, out var l);
            if (t > 2) { both = Math.Max(both, Math.Abs(r + l) / 2); apart = Math.Max(apart, Math.Abs(r - l)); }
        }
        Assert.That(both, Is.GreaterThan(15), "both antennas nod");
        Assert.That(apart, Is.LessThan(0.5));
    }

    [Test]
    public void AHeadShakeMovesTheAntennasAgainstEachOther()
    {
        double head = Swing(t => new RobotAngles(0, 0, 8 * Math.Sin(2 * Math.PI * 1.5 * t)), a => a.Yaw, out var g);
        Assert.That(g.Shaking(3.9), Is.True);
        Assert.That(head, Is.LessThan(2));
        var n = new NodShake();
        double apart = 0;
        for (int i = 0; i < 72 * 4; i++)
        {
            double t = i / 72.0;
            n.Step(new RobotAngles(0, 0, 8 * Math.Sin(2 * Math.PI * 1.5 * t)), t, 1 / 72.0);
            n.Antennas(out var r, out var l);
            if (t > 2) apart = Math.Max(apart, Math.Abs(r - l));
        }
        Assert.That(apart, Is.GreaterThan(30));
    }

    [Test]
    public void ThumbsUpBouncesUpAndThumbsDownSinks()
    {
        var r = new Reaction();
        r.Step(0, 1 / 72.0, 0, out var a0, out _);
        Assert.That(a0, Is.EqualTo(0));
        r.Trigger(Reaction.Kind.ThumbsUp, 1);
        double top = 0;
        for (double t = 1; t < 2.4; t += 1 / 72.0) { r.Step(t, 1 / 72.0, 0, out var x, out var y); Assert.That(x, Is.EqualTo(y)); top = Math.Max(top, x); }
        Assert.That(top, Is.InRange(30, 41));
        Assert.That(r.Active(2.5), Is.False);
        r.Trigger(Reaction.Kind.ThumbsDown, 5);
        double low = 0;
        for (double t = 5; t < 6.8; t += 1 / 72.0) { r.Step(t, 1 / 72.0, 0, out var x, out _); low = Math.Min(low, x); }
        Assert.That(low, Is.InRange(-39, -30));
        double smile = 0;
        for (double t = 10; t < 12; t += 1 / 72.0) r.Step(t, 1 / 72.0, 1, out smile, out _);
        Assert.That(smile, Is.InRange(12, 14.1), "a smile lifts both a little");
    }

    [Test]
    public void LookingSomewhereIsNotAGesture()
    {
        // a turn of 40 degrees to someone, then a look down at the table
        RobotAngles Head(double t) => new RobotAngles(0, t > 2.5 ? 15 : 0, Math.Min(40, 40 * t));
        var g = new NodShake();
        double worst = 0;
        for (int i = 0; i < 72 * 5; i++)
        {
            double t = i / 72.0;
            var o = g.Step(Head(t), t, 1 / 72.0);
            worst = Math.Max(worst, Math.Max(Math.Abs(o.Yaw - Head(t).Yaw), Math.Abs(o.Pitch - Head(t).Pitch)));
            Assert.That(g.Nodding(t) || g.Shaking(t), Is.False);
        }
        Assert.That(worst, Is.LessThan(0.01));
    }

    [Test]
    public void WantToTalkWavesTheRightAntennaAndSwingsTheBody()
    {
        var w = new WantToTalk();
        w.Step(0, out var r0, out _, out var b0);
        Assert.That(new[] { r0, b0 }, Is.EqualTo(new[] { 0.0, 0 }), "idle");
        w.Trigger(10);
        double antenna = 0, body = 0;
        for (double t = 10; t < 13; t += 1 / 72.0)
        {
            w.Step(t, out var r, out var l, out var b);
            Assert.That(l, Is.EqualTo(0));
            antenna = Math.Max(antenna, Math.Abs(r));
            body = Math.Max(body, Math.Abs(b));
        }
        Assert.That(antenna, Is.InRange(30, 39));
        Assert.That(body, Is.InRange(20, WantToTalk.BodyDeg));
        Assert.That(w.Active(13.1), Is.False);
    }
}
