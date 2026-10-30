// Robot camera model: pixel <-> direction, with lens distortion. Pure maths, no Unity: port of
// xr-client/pages/camera.js, checked against it in Assets/Tests.
//
// Image coordinates are normalized (u, v in 0..1, origin top left), so a scaled-down stream uses the same model as
// long as the aspect ratio stays. Camera frame: x right, y down, z forward (OpenCV).
// Distortion: OpenCV order [k1, k2, p1, p2, k3, k4, k5, k6, s1, s2, s3, s4] (rational + thin prism).

using System;

public class CameraModel
{
    public readonly double Width, Height, Fx, Fy, Cx, Cy;
    readonly double[] dist = new double[12];

    public CameraModel(double width, double height, double fx, double fy, double cx, double cy, double[] distortion)
    {
        Width = width; Height = height; Fx = fx; Fy = fy; Cx = cx; Cy = cy;
        Array.Copy(distortion, dist, Math.Min(distortion.Length, 12));
    }

    /// <summary>Our calibration of the real camera, 2026-10-08, AprilTag grid (robot/scripts/calibrate_camera_apriltag.py),
    /// reprojection error 2.3 px. Same values as xr-client/pages/camera.json.</summary>
    public static CameraModel Calibrated() => new CameraModel(1920, 1080,
        992.076281828573, 992.8403520277743, 946.0789017717469, 548.5234281247938,
        new[] { -0.05038947568459324, 0.08391509940058939, 0.0018669674758364737, 0.0008246953938521169, -0.04830911465110975 });

    /// <summary>Undistorted normalized coords (x = X/Z, y = Y/Z) to distorted normalized coords.</summary>
    public void Distort(double x, double y, out double xd, out double yd)
    {
        double r2 = x * x + y * y, r4 = r2 * r2, r6 = r4 * r2;
        double radial = (1 + dist[0] * r2 + dist[1] * r4 + dist[4] * r6) / (1 + dist[5] * r2 + dist[6] * r4 + dist[7] * r6);
        xd = x * radial + 2 * dist[2] * x * y + dist[3] * (r2 + 2 * x * x) + dist[8] * r2 + dist[9] * r4;
        yd = y * radial + dist[2] * (r2 + 2 * y * y) + 2 * dist[3] * x * y + dist[10] * r2 + dist[11] * r4;
    }

    /// <summary>Direction (camera frame) to normalized image coords; false behind the camera.</summary>
    public bool Project(double X, double Y, double Z, out double u, out double v)
    {
        u = v = 0;
        if (Z <= 1e-6) return false;
        Distort(X / Z, Y / Z, out var xd, out var yd);
        u = (Fx * xd + Cx) / Width;
        v = (Fy * yd + Cy) / Height;
        return true;
    }

    /// <summary>Normalized image coords to a unit direction; false if the lens model does not reach there.</summary>
    public bool Unproject(double u, double v, out double dx, out double dy, out double dz)
    {
        dx = dy = dz = 0;
        double xd = (u * Width - Cx) / Fx, yd = (v * Height - Cy) / Fy, x = xd, y = yd;
        for (int i = 0; i < 30; i++)   // Newton on Distort(x, y) = (xd, yd), numeric Jacobian
        {
            Distort(x, y, out var fx0, out var fy0);
            double ex = fx0 - xd, ey = fy0 - yd;
            if (ex * ex + ey * ey < 1e-18) break;
            const double h = 1e-6;
            Distort(x + h, y, out var ax, out var ay);
            Distort(x, y + h, out var bx, out var by);
            double j11 = (ax - fx0) / h, j21 = (ay - fy0) / h, j12 = (bx - fx0) / h, j22 = (by - fy0) / h;
            double det = j11 * j22 - j12 * j21;
            if (Math.Abs(det) < 1e-12) return false;
            x -= (j22 * ex - j12 * ey) / det;
            y -= (-j21 * ex + j11 * ey) / det;
            if (double.IsNaN(x) || double.IsNaN(y) || Math.Abs(x) > 20 || Math.Abs(y) > 20) return false;
        }
        Distort(x, y, out var cx, out var cy);
        if (Math.Abs(cx - xd) > 1e-5 || Math.Abs(cy - yd) > 1e-5) return false;
        double n = Math.Sqrt(x * x + y * y + 1);
        dx = x / n; dy = y / n; dz = 1 / n;
        return true;
    }

    /// <summary>Horizontal angle of an image point, degrees, + = left (robot convention).</summary>
    public double YawDeg(double u, double v)
    {
        if (!Unproject(u, v, out var x, out _, out var z) && !Unproject(0.5, v, out x, out _, out z)) return 0;
        return -Math.Atan2(x, z) * 180 / Math.PI;
    }

    /// <summary>Image column of a direction on the horizon, yaw + = left.</summary>
    public double UForYaw(double yawDeg)
    {
        double rad = yawDeg * Math.PI / 180;
        return Project(-Math.Sin(rad), 0, Math.Cos(rad), out var u, out _) ? u : (yawDeg > 0 ? 0 : 1);
    }
}
