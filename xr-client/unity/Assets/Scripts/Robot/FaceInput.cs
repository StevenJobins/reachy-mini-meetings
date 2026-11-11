// The user's smile, from the headset's inward face tracking (Android XR: Faces, OpenXR XR_ANDROID_face_tracking),
// for the antennas: while you smile, Reachy lifts both a little (RobotControl.Smile, Gestures.cs Reaction).
// Needs the FACE_TRACKING permission, asked with the others in XrInput. Without it, or on a headset without face
// tracking, the smile is simply 0. adb logcat -s Unity shows [face] every 5 s.

using System.Collections.Generic;
using Unity.Collections;
using UnityEngine;
using UnityEngine.XR.ARSubsystems;
using UnityEngine.XR.OpenXR.Features.Android;

public class FaceInput : MonoBehaviour
{
    public const string Permission = "android.permission.FACE_TRACKING";
    const int LipCornerPullerL = 32, LipCornerPullerR = 33;   // AndroidXRBlendShapeLocation
    const float From = 0.3f, To = 0.7f;                       // lip corners pulled this much = no smile .. a full one

    public RobotControl control;

    readonly List<XRFaceSubsystem> found = new List<XRFaceSubsystem>();
    XRFaceSubsystem face;
    float loggedAt, most;
    string state = "no face tracking";

    void Update()
    {
        float smile = 0;
#if UNITY_ANDROID && !UNITY_EDITOR
        try
        {
            if (face == null)
            {
                SubsystemManager.GetSubsystems(found);
                face = found.Count > 0 ? found[0] : null;
            }
            if (face != null && !face.running && UnityEngine.Android.Permission.HasUserAuthorizedPermission(Permission)) face.Start();
            if (face is AndroidOpenXRFaceSubsystem inward && face.running)
            {
                var shapes = inward.TryGetInwardBlendShapes(Allocator.Temp);
                if (shapes.status.IsSuccess())
                {
                    float left = 0, right = 0;
                    foreach (var s in shapes.value)
                    {
                        if (s.blendShapeId == LipCornerPullerL) left = s.weight;
                        else if (s.blendShapeId == LipCornerPullerR) right = s.weight;
                    }
                    smile = Mathf.InverseLerp(From, To, (left + right) / 2);
                    state = "tracking";
                }
                else state = "no data";
            }
            else state = face == null ? "no face tracking" : "not started (permission?)";
        }
        catch (System.Exception e)
        {
            state = e.GetType().Name;
        }
#endif
        control.Smile = smile;
        most = Mathf.Max(most, smile);
        if (Time.unscaledTime - loggedAt >= 5f)
        {
            loggedAt = Time.unscaledTime;
            Debug.Log($"[face] {state}, smile now {smile:0.00}, highest in 5 s {most:0.00}");
            most = 0;
        }
    }
}
