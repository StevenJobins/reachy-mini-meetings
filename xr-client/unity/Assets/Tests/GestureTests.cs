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
    public void ANodComesOutLarger()
    {
        double nod = Swing(t => new RobotAngles(0, 6 * Math.Sin(2 * Math.PI * 1.8 * t), 10), a => a.Pitch, out var g);
        Assert.That(g.Nodding(3.9), Is.True);
        Assert.That(nod, Is.GreaterThan(6 * 1.6), "about twice the 6 degrees");
        double yawOff = Swing(t => new RobotAngles(0, 6 * Math.Sin(2 * Math.PI * 1.8 * t), 10), a => a.Yaw, out _);
        Assert.That(yawOff, Is.LessThan(0.01), "the other axis is left alone");
    }

    [Test]
    public void AHeadShakeComesOutLarger()
    {
        double shake = Swing(t => new RobotAngles(0, 0, 8 * Math.Sin(2 * Math.PI * 1.5 * t)), a => a.Yaw, out var g);
        Assert.That(g.Shaking(3.9), Is.True);
        Assert.That(shake, Is.GreaterThan(8 * 1.5));
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
