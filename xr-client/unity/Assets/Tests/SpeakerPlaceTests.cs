// SpeakerPlace: directions in the robot base frame against the picture of a turned head.

using NUnit.Framework;

public class SpeakerPlaceTests
{
    static double[] At(double yaw, double up, double roll, double pitch, double headYaw)
    {
        SpeakerPlace.InCamera(yaw, up, new RobotAngles(roll, pitch, headYaw), out var x, out var y, out var z);
        return new[] { x, y, z };
    }

    [Test]
    public void LookingAtThePersonPutsThemInTheCentre()
    {
        Assert.That(At(30, 0, 0, 0, 30), Is.EqualTo(new[] { 0.0, 0, 1 }).Within(1e-12));
        Assert.That(At(-50, 10, 0, -10, -50), Is.EqualTo(new[] { 0.0, 0, 1 }).Within(1e-12));   // pitch + = down
    }

    [Test]
    public void Signs()
    {
        Assert.That(At(40, 0, 0, 0, 30)[0], Is.LessThan(0));       // further left than the head looks: left in the picture
        Assert.That(At(0, 0, 0, 20, 0)[1], Is.GreaterThan(0));     // head looks down: straight ahead is in the upper half
        Assert.That(At(20, 0, 15, 0, 0)[1], Is.LessThan(0));       // head tilted right: what is left sits lower in the picture
    }

    [Test]
    public void MatchesTheLensModel()
    {
        var cam = CameraModel.Calibrated();
        var p = At(10, 5, 0, 0, 0);
        Assert.That(SpeakerPlace.InPicture(cam, p[0], p[1], p[2]), Is.True);
        Assert.That(cam.Project(p[0], -p[1], p[2], out var u, out var v), Is.True);
        Assert.That(u, Is.LessThan(0.5));                           // left of the middle
        Assert.That(v, Is.LessThan(0.5));                           // above the middle
        p = At(80, 0, 0, 0, 0);
        Assert.That(SpeakerPlace.InPicture(cam, p[0], p[1], p[2]), Is.False);
        p = At(170, 0, 0, 0, 0);                                    // behind the camera
        Assert.That(SpeakerPlace.InPicture(cam, p[0], p[1], p[2]), Is.False);
    }
}
