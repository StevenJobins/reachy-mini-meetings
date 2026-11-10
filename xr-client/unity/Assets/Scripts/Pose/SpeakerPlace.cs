// Where a person in the room is in the picture. The backend says where the speaker's face is as a direction in the
// robot base frame (backend people.py: yaw + = left, up + = up); the picture shows what the camera saw with the head
// at some pose. Pure maths, no Unity.

using System;

public static class SpeakerPlace
{
    /// <summary>A direction in the robot base frame, seen from a head at `head` (R = Rz(yaw) Ry(pitch) Rx(roll), as
    /// everywhere): unit vector with x right, y up, z forward, the frame of the video window.</summary>
    public static void InCamera(double yawDeg, double upDeg, RobotAngles head, out double x, out double y, out double z)
    {
        double yaw = yawDeg / PoseMath.Deg, up = upDeg / PoseMath.Deg;
        double dx = Math.Cos(up) * Math.Cos(yaw), dy = Math.Cos(up) * Math.Sin(yaw), dz = Math.Sin(up);   // x forward, y left, z up
        double w = head.Yaw / PoseMath.Deg, q = head.Pitch / PoseMath.Deg, r = head.Roll / PoseMath.Deg;
        double x1 = Math.Cos(w) * dx + Math.Sin(w) * dy, y1 = -Math.Sin(w) * dx + Math.Cos(w) * dy;
        double x2 = Math.Cos(q) * x1 - Math.Sin(q) * dz, z2 = Math.Sin(q) * x1 + Math.Cos(q) * dz;
        double y3 = Math.Cos(r) * y1 + Math.Sin(r) * z2, z3 = -Math.Sin(r) * y1 + Math.Cos(r) * z2;
        x = -y3; y = z3; z = x2;
    }

    /// <summary>Is that direction inside the camera image, `margin` (fraction of the image) away from its edge?</summary>
    public static bool InPicture(CameraModel cam, double x, double y, double z, double margin = 0.03)
    {
        return cam.Project(x, -y, z, out var u, out var v) && u > margin && u < 1 - margin && v > margin && v < 1 - margin;
    }
}
