// Hands and eyes for the whole app.
// - Permissions: Android XR only gives hand joints with HAND_TRACKING and eye gaze with EYE_TRACKING_FINE, granted at
//   runtime. Asked here together with the microphone, so the headset shows one dialog after the other at the start.
// - Pinch from the hand joints (XR Hands): thumb tip to index tip, 0 (apart) .. 1 (touching). RobotControl takes the
//   larger of this and the hand interaction profile's pinch value, so a pinch counts if either sees it.
// - Gaze: where the eyes look (OpenXR eye gaze interaction), slightly smoothed; the head direction while the eyes are
//   not tracked or not allowed. Used to pick menu tiles, the notes handle and the tour's "look at" tasks.
// Every 2 s, adb logcat -s Unity shows [input] with both pinch sources and whether the eyes are tracked.

using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Android;
using UnityEngine.InputSystem;
using UnityEngine.XR.Hands;

public class XrInput : MonoBehaviour
{
    const string HandPermission = "android.permission.HAND_TRACKING";
    const string EyePermission = "android.permission.EYE_TRACKING_FINE";
    const float OpenM = 0.045f, ClosedM = 0.012f;   // thumb-index tip distance for pinch 0 and 1
    const float GazeSmoothS = 0.06f;
    const float LogS = 2f;

    public Transform head;

    static XrInput instance;
    XRHandSubsystem hands;
    readonly List<XRHandSubsystem> found = new List<XRHandSubsystem>();
    InputAction eyePos, eyeRot, eyeTracked;
    Vector3 gazeDir;
    float loggedAt;

    public static float PinchLeft { get; private set; }
    public static float PinchRight { get; private set; }
    public static bool EyesTracked { get; private set; }

    /// <summary>Where the user looks, in world space: the eyes if tracked, else the head.</summary>
    public static Ray Gaze(Transform head) =>
        instance != null && EyesTracked ? new Ray(head.position, instance.gazeDir) : new Ray(head.position, head.forward);

    void Awake()
    {
        instance = this;
#if UNITY_ANDROID && !UNITY_EDITOR
        var ask = new List<string>();
        foreach (var p in new[] { Permission.Microphone, HandPermission, EyePermission })
            if (!Permission.HasUserAuthorizedPermission(p)) ask.Add(p);
        if (ask.Count > 0) Permission.RequestUserPermissions(ask.ToArray());
#endif
        eyePos = Bind("<EyeGaze>/pose/position", "Vector3");
        eyeRot = Bind("<EyeGaze>/pose/rotation", "Quaternion");
        eyeTracked = Bind("<EyeGaze>/pose/isTracked", "Button");
        gazeDir = head.forward;
    }

    static InputAction Bind(string path, string type)
    {
        var a = new InputAction(binding: path, expectedControlType: type);
        a.Enable();
        return a;
    }

    void OnDestroy()
    {
        eyePos.Dispose();
        eyeRot.Dispose();
        eyeTracked.Dispose();
    }

    void Update()
    {
        if (hands == null || !hands.running)
        {
            SubsystemManager.GetSubsystems(found);
            hands = found.Find(s => s.running);
        }
        PinchLeft = hands != null ? Pinch(hands.leftHand) : 0;
        PinchRight = hands != null ? Pinch(hands.rightHand) : 0;

        // eye gaze comes in the tracking space, like the head: same parent as the camera
        EyesTracked = eyeTracked.ReadValue<float>() > 0.5f;
        if (EyesTracked)
        {
            var dir = head.parent.rotation * (eyeRot.ReadValue<Quaternion>() * Vector3.forward);
            float k = 1 - Mathf.Exp(-Time.unscaledDeltaTime / GazeSmoothS);
            gazeDir = Vector3.Slerp(gazeDir, dir, k).normalized;
        }
        else gazeDir = head.forward;

        if (Time.unscaledTime - loggedAt >= LogS)
        {
            loggedAt = Time.unscaledTime;
            Debug.Log($"[input] joints L {PinchLeft:0.00} R {PinchRight:0.00} ({(hands == null ? "no hand subsystem" : $"tracked L {hands.leftHand.isTracked} R {hands.rightHand.isTracked}")}), "
                      + $"eyes {(EyesTracked ? "tracked" : "not tracked")}, permissions hand {Allowed(HandPermission)} eye {Allowed(EyePermission)}");
        }
    }

    static bool Allowed(string p)
    {
#if UNITY_ANDROID && !UNITY_EDITOR
        return Permission.HasUserAuthorizedPermission(p);
#else
        return true;
#endif
    }

    static float Pinch(XRHand hand)
    {
        if (!hand.isTracked) return 0;
        if (!hand.GetJoint(XRHandJointID.ThumbTip).TryGetPose(out var thumb)) return 0;
        if (!hand.GetJoint(XRHandJointID.IndexTip).TryGetPose(out var index)) return 0;
        return Mathf.InverseLerp(OpenM, ClosedM, Vector3.Distance(thumb.position, index.position));
    }
}
