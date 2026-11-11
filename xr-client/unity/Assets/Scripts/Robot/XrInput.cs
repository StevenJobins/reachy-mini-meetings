// Hands and eyes for the whole app.
// - Permissions: Android XR only gives hand joints with HAND_TRACKING and eye gaze with EYE_TRACKING_FINE, granted at
//   runtime. Asked here together with the microphone, so the headset shows one dialog after the other at the start.
// - Pinch from the hand joints (XR Hands): thumb tip to index tip, 0 (apart) .. 1 (touching). RobotControl takes the
//   larger of this and the hand interaction profile's pinch value, so a pinch counts if either sees it.
// - Gaze: where the eyes look (OpenXR eye gaze interaction), slightly smoothed; the head direction while the eyes are
//   not tracked or not allowed. Used to pick menu tiles, the notes handle and the tour's "look at" tasks.
// - Pinch point (between thumb and index tip), for carrying the notes card with the hand.
// - Pointer rays: the system's aim ray of each hand (hand interaction profile, or the controller), like the cursor in
//   the headset's own menus. The menu and the notes pick with these; with no ray, the gaze picks instead.
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
    // Thumb-index tip distance for pinch 0 and 1. The tip joints sit inside the fingers: touching tips are about
    // 1.8 cm apart (headset test 2026-11-11: with 1.2 cm the left hand only reached 0.80-0.84, right at the threshold).
    const float OpenM = 0.045f, ClosedM = 0.018f;
    const float GazeSmoothS = 0.06f;
    const float LogS = 2f;

    public Transform head;

    static XrInput instance;
    XRHandSubsystem hands;
    readonly List<XRHandSubsystem> found = new List<XRHandSubsystem>();
    InputAction eyePos, eyeRot, eyeTracked;
    readonly InputAction[] aimPos = new InputAction[2], aimRot = new InputAction[2], aimTracked = new InputAction[2];
    Vector3 gazeDir;
    float loggedAt, maxLeft, maxRight;

    public static float PinchLeft { get; private set; }
    public static float PinchRight { get; private set; }
    public static bool EyesTracked { get; private set; }
    /// <summary>A hand held above the head for a moment: "I want to talk".</summary>
    public static bool HandRaised { get; private set; }
    /// <summary>+1 = a thumbs up held for a moment, -1 = thumbs down, 0 = neither.</summary>
    public static int Thumb { get; private set; }
    const float RaiseAboveM = 0.05f, RaiseHoldS = 0.5f, ThumbHoldS = 0.4f;
    float raisedSince = -1f, thumbSince = -1f;
    int thumbNow;

    /// <summary>Where the user looks, in world space: the eyes if tracked, else the head.</summary>
    public static Ray Gaze(Transform head) =>
        instance != null && EyesTracked ? new Ray(head.position, instance.gazeDir) : new Ray(head.position, head.forward);

    void Awake()
    {
        instance = this;
#if UNITY_ANDROID && !UNITY_EDITOR
        var ask = new List<string>();
        foreach (var p in new[] { Permission.Microphone, HandPermission, EyePermission, FaceInput.Permission })
            if (!Permission.HasUserAuthorizedPermission(p)) ask.Add(p);
        if (ask.Count > 0) Permission.RequestUserPermissions(ask.ToArray());
#endif
        eyePos = Bind("<EyeGaze>/pose/position", "Vector3");
        eyeRot = Bind("<EyeGaze>/pose/rotation", "Quaternion");
        eyeTracked = Bind("<EyeGaze>/pose/isTracked", "Button");
        gazeDir = head.forward;
        for (int i = 0; i < 2; i++)
        {
            var hand = i == 0 ? "{LeftHand}" : "{RightHand}";
            aimPos[i] = Bind($"<HandInteraction>{hand}/pointer/position", "Vector3", $"<XRController>{hand}/pointerPosition");
            aimRot[i] = Bind($"<HandInteraction>{hand}/pointer/rotation", "Quaternion", $"<XRController>{hand}/pointerRotation");
            aimTracked[i] = Bind($"<HandInteraction>{hand}/pointer/isTracked", "Button", $"<XRController>{hand}/isTracked");
        }
    }

    /// <summary>The aim ray of one hand (or controller) in world space; null while it is not tracked.</summary>
    public static Ray? Aim(bool right, Transform head)
    {
        if (instance == null) return null;
        int i = right ? 1 : 0;
        if (instance.aimTracked[i].ReadValue<float>() < 0.5f) return null;
        var origin = head.parent.TransformPoint(instance.aimPos[i].ReadValue<Vector3>());
        var dir = head.parent.rotation * (instance.aimRot[i].ReadValue<Quaternion>() * Vector3.forward);
        return dir.sqrMagnitude > 0.5f ? new Ray(origin, dir) : (Ray?)null;
    }

    /// <summary>What picks for this hand: its aim ray, else the gaze.</summary>
    public static Ray Pointer(bool right, Transform head) => Aim(right, head) ?? Gaze(head);

    static InputAction Bind(string path, string type, string alternative = null)
    {
        var a = new InputAction(binding: path, expectedControlType: type);
        if (alternative != null) a.AddBinding(alternative);
        a.Enable();
        return a;
    }

    void OnDestroy()
    {
        eyePos.Dispose();
        eyeRot.Dispose();
        eyeTracked.Dispose();
        foreach (var a in aimPos) a.Dispose();
        foreach (var a in aimRot) a.Dispose();
        foreach (var a in aimTracked) a.Dispose();
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
        UpdatePoses();
        maxLeft = Mathf.Max(maxLeft, PinchLeft);
        maxRight = Mathf.Max(maxRight, PinchRight);

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
            Debug.Log($"[input] joints L {PinchLeft:0.00} R {PinchRight:0.00}, highest in 2 s L {maxLeft:0.00} R {maxRight:0.00} ({(hands == null ? "no hand subsystem" : $"tracked L {hands.leftHand.isTracked} R {hands.rightHand.isTracked}")}), "
                      + $"eyes {(EyesTracked ? "tracked" : "not tracked")}, aim rays L {Aim(false, head) != null} R {Aim(true, head) != null}, permissions hand {Allowed(HandPermission)} eye {Allowed(EyePermission)}");
            maxLeft = maxRight = 0;
        }
    }

    /// <summary>Between thumb tip and index tip of one hand, in world space; null while that hand is not tracked.</summary>
    public static Vector3? PinchPoint(bool left, Transform head)
    {
        if (instance == null || instance.hands == null) return null;
        var hand = left ? instance.hands.leftHand : instance.hands.rightHand;
        if (!hand.isTracked) return null;
        if (!hand.GetJoint(XRHandJointID.ThumbTip).TryGetPose(out var thumb)) return null;
        if (!hand.GetJoint(XRHandJointID.IndexTip).TryGetPose(out var index)) return null;
        return head.parent.TransformPoint((thumb.position + index.position) / 2);   // joints are in the tracking space
    }

    static bool Allowed(string p)
    {
#if UNITY_ANDROID && !UNITY_EDITOR
        return Permission.HasUserAuthorizedPermission(p);
#else
        return true;
#endif
    }

    /// <summary>Raised hand and thumbs up / down, from the hand joints (tracking space: y is up).</summary>
    void UpdatePoses()
    {
        bool raised = false;
        int thumb = 0;
        if (hands != null)
            foreach (var hand in new[] { hands.leftHand, hands.rightHand })
            {
                if (!hand.isTracked || !hand.GetJoint(XRHandJointID.Wrist).TryGetPose(out var wrist)) continue;
                if (wrist.position.y > head.localPosition.y + RaiseAboveM) raised = true;
                int t = ThumbPose(hand);
                if (t != 0) thumb = t;
            }
        if (!raised) raisedSince = -1f;
        else if (raisedSince < 0) raisedSince = Time.unscaledTime;
        HandRaised = raised && Time.unscaledTime - raisedSince >= RaiseHoldS;

        if (thumb != thumbNow) { thumbNow = thumb; thumbSince = Time.unscaledTime; }
        Thumb = thumbNow != 0 && Time.unscaledTime - thumbSince >= ThumbHoldS ? thumbNow : 0;
    }

    /// <summary>+1 / -1 when the thumb points up / down and the four fingers are curled into the palm.</summary>
    static int ThumbPose(XRHand hand)
    {
        if (!hand.GetJoint(XRHandJointID.Palm).TryGetPose(out var palm)) return 0;
        if (!hand.GetJoint(XRHandJointID.ThumbTip).TryGetPose(out var tip)) return 0;
        if (!hand.GetJoint(XRHandJointID.ThumbProximal).TryGetPose(out var root)) return 0;
        foreach (var id in new[] { XRHandJointID.IndexTip, XRHandJointID.MiddleTip, XRHandJointID.RingTip, XRHandJointID.LittleTip })
            if (!hand.GetJoint(id).TryGetPose(out var f) || Vector3.Distance(f.position, palm.position) > 0.075f) return 0;   // a finger is out
        var along = tip.position - root.position;
        if (along.magnitude < 0.035f) return 0;                       // the thumb is tucked in
        float up = Vector3.Dot(along.normalized, Vector3.up);
        if (Vector3.Distance(tip.position, palm.position) < 0.07f) return 0;
        return up > 0.75f ? 1 : up < -0.75f ? -1 : 0;
    }

    static float Pinch(XRHand hand)
    {
        if (!hand.isTracked) return 0;
        if (!hand.GetJoint(XRHandJointID.ThumbTip).TryGetPose(out var thumb)) return 0;
        if (!hand.GetJoint(XRHandJointID.IndexTip).TryGetPose(out var index)) return 0;
        return Mathf.InverseLerp(OpenM, ClosedM, Vector3.Distance(thumb.position, index.position));
    }
}
