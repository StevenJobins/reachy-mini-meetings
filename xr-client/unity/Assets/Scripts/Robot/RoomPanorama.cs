// The room around the live video: frames saved while Reachy held still, each on the video sphere at the pose it was
// taken with, so turning your head shows the room at once and only what moves waits for the live video on top. Fills
// by itself as you look around; a short scan at the start gives it a head start (left pinch skips it). Patches are
// placed by the measured pose: at rest it is accurate, while the head stops short of a command by its play.

using System.Collections.Generic;
using UnityEngine;

public class RoomPanorama : MonoBehaviour
{
    const int PatchW = 960, PatchH = 540;
    const float Scale = 1.1f;            // just behind the live video sphere
    const double StillWindowS = 0.6, StillDeg = 1;
    const float RefreshS = 1f;           // at most one saved frame per this time, outside the scan
    const int QueueBase = 2010;          // below the live video (Geometry+100), newest on top

    class Patch { public GameObject Go; public RenderTexture Frame; public Material Material; }

    public RobotControl control;
    public RobotVideo video;
    public VideoWindow view;
    public Transform head;
    public Material patchMaterial;
    public Renderer floor;

    readonly RoomScan scan = new RoomScan();
    readonly PatchSlots slots = new PatchSlots();
    readonly Dictionary<int, Patch> patches = new Dictionary<int, Patch>();
    readonly List<int> order = new List<int>();   // oldest first
    bool scanned;
    float nextRefresh;

    public string Status { get; private set; } = "";

    static Quaternion ToUnity(Quat q) => new Quaternion(-(float)q.X, -(float)q.Y, (float)q.Z, (float)q.W);

    void LateUpdate()
    {
        // the whole panorama hangs in the user's "straight ahead" and around the eye, like the live window
        transform.localRotation = ToUnity(control.Frame.ToWorld(Quat.Identity));
        transform.localPosition = head.localPosition;

        if (!control.IsFollowing || !view.Locked) { Report(); return; }
        double now = DaemonClient.NowS();
        var measured = view.Measured;
        if (!measured.TryAt(now, out var pose)) return;
        double span = measured.SpanDeg(now - StillWindowS, now);

        if (!scanned && !control.Sweeping)
        {
            scanned = true;
            scan.Start(now);
            Debug.Log("[room] scan start");
        }
        if (scan.Active)
        {
            if (control.ConsumeSkip()) { scan.Cancel(); Debug.Log("[room] scan skipped"); }
            else if (scan.Step(now, pose, span, video.VideoDelayS))
            {
                Save(pose, now);
                scan.Shot(now);
                if (!scan.Active) Debug.Log($"[room] scan done, {slots.Count} patches");
            }
        }
        else if (Time.unscaledTime >= nextRefresh && span < StillDeg)
        {
            nextRefresh = Time.unscaledTime + RefreshS;
            Save(pose, now);
        }
        control.Override = scan.Active ? scan.Target : (RobotAngles?)null;
        Report();
    }

    void Report()
    {
        Status = scan.Active ? $"scanning {scan.Done + 1}/{scan.Total}, left pinch skips" : $"room {slots.Count}";
    }

    void Save(RobotAngles pose, double now)
    {
        var tex = video.VideoTexture;
        if (tex == null) return;
        var slot = slots.Take(pose.Pitch, pose.Yaw, now, out var evicted);
        Patch patch;
        if (evicted != null)
        {
            patch = patches[evicted.Value];
            patches.Remove(evicted.Value);
            order.Remove(evicted.Value);
        }
        else if (!patches.TryGetValue(slot.Id, out patch)) patch = Create();
        patches[slot.Id] = patch;
        Graphics.Blit(tex, patch.Frame);
        patch.Go.transform.localRotation = ToUnity(PoseMath.RobotToHeadset(pose.Roll, pose.Pitch, pose.Yaw));
        patch.Go.SetActive(true);
        order.Remove(slot.Id);
        order.Add(slot.Id);
        for (int i = 0; i < order.Count; i++) patches[order[i]].Material.renderQueue = QueueBase + i;
        if (floor != null) floor.enabled = false;   // the scanned room replaces the virtual floor
    }

    Patch Create()
    {
        var frame = new RenderTexture(PatchW, PatchH, 0, RenderTextureFormat.ARGB32);
        frame.Create();
        var material = new Material(patchMaterial) { mainTexture = frame };
        var go = new GameObject("Patch");
        go.transform.SetParent(transform, false);
        go.transform.localScale = Vector3.one * Scale;
        go.AddComponent<MeshFilter>().sharedMesh = view.SphereMesh;
        go.AddComponent<MeshRenderer>().sharedMaterial = material;
        go.SetActive(false);
        return new Patch { Go = go, Frame = frame, Material = material };
    }

    void OnDestroy()
    {
        foreach (var p in patches.Values) { p.Frame.Release(); Destroy(p.Material); }
    }
}
