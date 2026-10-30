// The video on a piece of sphere around the eye: every grid point of the image sits in the direction its pixel sees
// (camera model, lens distortion included). World-locked: the sphere hangs where the robot camera looked when the
// shown frame was captured, so the user's own head turns show without lag and the robot's delay only shows as the
// window's edge. Frames carry no pose, so they are paired by time: the pose from VideoDelayS ago.
// While the robot does not really follow (FollowGuard), the window stands in front of the user instead and only
// glides after them when they look away.

using UnityEngine;

[RequireComponent(typeof(MeshFilter), typeof(MeshRenderer))]
public class VideoWindow : MonoBehaviour
{
    const float Radius = 3f;
    const int GridX = 48, GridY = 27;
    // Command -> robot head on top of its play, from the recording (2026-10-30). Override: adb ... -e lagms 130
    const double RobotLagDefaultMs = 130;
    const float BlendS = 0.3f;         // switching between world-locked and in front
    const float GlideAwayDeg = 30f, GlideAfterS = 0.5f, GlideS = 0.25f;

    public RobotControl control;
    public RobotVideo video;
    public Transform head;
    public Renderer vignette;

    float vignetteLevel;
    readonly PoseHistory measured = new PoseHistory();
    readonly FollowGuard guard = new FollowGuard();
    readonly PoseClock clock = new PoseClock();
    PoseBlend blend = new PoseBlend(RobotLagDefaultMs / 1000);
    Quaternion blendFrom = Quaternion.identity;
    float blendAt = -10f;
    bool locked;
    float frontYaw, awaySince = -1f;
    bool glide, haveFront;

    // Bench recording during a sweep: one row per rendered frame, for analysis on the laptop
    const int RecordRows = 1500;
    readonly System.Text.StringBuilder record = new System.Text.StringBuilder();
    readonly System.Text.StringBuilder poses = new System.Text.StringBuilder();
    int recorded;
    bool recordDone;

    public string Summary { get; private set; } = "";

    void Awake()
    {
        if (double.TryParse(RobotVideo.ReadIntentExtra("lagms"), System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture, out var ms)) blend = new PoseBlend(ms / 1000);
        GetComponent<MeshFilter>().sharedMesh = BuildSphere(CameraModel.Calibrated());
    }

    /// <summary>N x M grid over the image, each vertex in the direction its pixel sees. Unity frame: x right, y up, z forward.</summary>
    static Mesh BuildSphere(CameraModel cam)
    {
        int stride = GridX + 1;
        var vertices = new Vector3[stride * (GridY + 1)];
        var uv = new Vector2[vertices.Length];
        var ok = new bool[vertices.Length];
        for (int j = 0; j <= GridY; j++)
        for (int i = 0; i <= GridX; i++)
        {
            int k = j * stride + i;
            double u = (double)i / GridX, v = (double)j / GridY;
            ok[k] = cam.Unproject(u, v, out var dx, out var dy, out var dz);
            vertices[k] = new Vector3((float)dx, -(float)dy, (float)dz) * Radius;
            uv[k] = new Vector2((float)u, 1 - (float)v);
        }
        var triangles = new System.Collections.Generic.List<int>(GridX * GridY * 6);
        for (int j = 0; j < GridY; j++)
        for (int i = 0; i < GridX; i++)
        {
            int a = j * stride + i, b = a + 1, c = a + stride, e = c + 1;
            if (!(ok[a] && ok[b] && ok[c] && ok[e])) continue;
            triangles.AddRange(new[] { a, b, c, b, e, c });
        }
        var mesh = new Mesh { name = "Video Sphere", vertices = vertices, uv = uv };
        mesh.SetTriangles(triangles, 0);
        mesh.RecalculateBounds();
        return mesh;
    }

    static Quaternion ToUnity(Quat q) => new Quaternion(-(float)q.X, -(float)q.Y, (float)q.Z, (float)q.W);   // its own inverse

    /// <summary>Yaw-only window in front of the user; glides after them once they look away.</summary>
    Quaternion Front()
    {
        float yaw = head.localEulerAngles.y;
        if (!haveFront) { frontYaw = yaw; haveFront = true; }
        float diff = Mathf.DeltaAngle(frontYaw, yaw);
        if (Mathf.Abs(diff) > GlideAwayDeg)
        {
            if (awaySince < 0) awaySince = Time.unscaledTime;
            if (Time.unscaledTime - awaySince > GlideAfterS) glide = true;
        }
        else
        {
            awaySince = -1f;
            if (Mathf.Abs(diff) < 2f) glide = false;
        }
        if (glide) frontYaw += diff * (1 - Mathf.Exp(-Time.unscaledDeltaTime / GlideS));
        return Quaternion.Euler(0, frontYaw, 0);
    }

    void SaveRecord()
    {
        recordDone = true;
        var path = System.IO.Path.Combine(Application.persistentDataPath, "sweep.csv");
        System.IO.File.WriteAllText(path, "now,shown_yaw,delay,raw_yaw\n" + record);
        System.IO.File.WriteAllText(System.IO.Path.ChangeExtension(path, "poses.csv"), "arrived,local,yaw\n" + poses);
        Debug.Log($"[view] recording saved to {path}");
    }

    void LateUpdate()
    {
        var daemon = control.Daemon;
        if (daemon == null) return;
        while (daemon.TryDequeueState(out var s))
        {
            double poseT = s.senderS > 0 ? clock.ToLocal(s.senderS, s.arrivedS) : s.arrivedS;
            measured.Add(poseT, s.Head);
            control.Follow?.PushHead(poseT, s.Head.Yaw, s.Head.Pitch);
            if (s.hasDoa) control.Follow?.PushDoa(poseT, s.doa.angle, DaemonClient.NowS());
            if (control.IsFollowing) blend.AddMeasured(poseT, s.Head, control.Modelled);
            if (control.Sweeping && !recordDone)
                poses.AppendLine(string.Format(System.Globalization.CultureInfo.InvariantCulture,
                    "{0:F4},{1:F4},{2:F3}", s.arrivedS, poseT, s.Head.Yaw));
        }

        double now = DaemonClient.NowS(), delay = video.VideoDelayS;
        bool follow = guard.Update(control.IsFollowing, measured, control.Commanded, delay, now);
        if (follow != locked)
        {
            locked = follow;
            blendFrom = transform.localRotation;
            blendAt = Time.unscaledTime;
            if (!follow) { haveFront = false; blend.Reset(); }
            Debug.Log($"[view] {(follow ? "world-locked at the robot pose" : "robot not following, window in front")}");
        }

        if (control.Sweeping && !recordDone && blend.TryAt(now - delay, control.Modelled, out var shown))
        {
            measured.TryAt(now - delay, out var raw);
            record.AppendLine(string.Format(System.Globalization.CultureInfo.InvariantCulture,
                "{0:F4},{1:F3},{2:F4},{3:F3}", now, shown.Yaw, delay, raw.Yaw));
            if (++recorded >= RecordRows) SaveRecord();
        }

        Quaternion target;
        if (locked && blend.TryAt(now - delay, control.Modelled, out var a))
        {
            // The room turns with the base (speaker direction + framing pitch), as it was when this frame was captured:
            // the window stays in front of the user while Reachy turns to a speaker, and looking ahead = at the speaker.
            control.BaseHistory.TryAt(now - delay - blend.LagS, out var b);
            var room = control.Frame.ToWorld(PoseMath.RobotToHeadset(0, b.Pitch, b.Yaw).Inverse);
            target = ToUnity(room * PoseMath.RobotToHeadset(a.Roll, a.Pitch, a.Yaw));
        }
        else
            target = Front();

        float t = (Time.unscaledTime - blendAt) / BlendS;
        transform.localRotation = t < 1 ? Quaternion.Slerp(blendFrom, target, Mathf.SmoothStep(0, 1, t)) : target;
        transform.localPosition = head.localPosition;   // centred on the eye
        // The picture turns without the user's own head motion: darken the edge of the view, the standard measure
        // against VR sickness. 0 below 6 °/s, full at 45 °/s.
        if (vignette != null)
        {
            float strength = Mathf.Clamp01(((float)control.BaseRate - 6) / 39);
            vignetteLevel = Mathf.MoveTowards(vignetteLevel, strength, Time.unscaledDeltaTime / 0.15f);
            vignette.enabled = vignetteLevel > 0.01f;
            vignette.material.SetFloat("_Strength", vignetteLevel);
        }
        Summary = $"view {(locked ? "world-locked" : "in front")}  delay {delay * 1000:0} ms";
    }
}
