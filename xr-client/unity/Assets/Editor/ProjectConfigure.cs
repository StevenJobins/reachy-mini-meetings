// Phase 0 configuration: URP, OpenXR + Android XR (Galaxy XR), Android player settings, test scene.
// Idempotent, run from the command line after ProjectSetup.AddPackages (see xr-client/unity/README.md):
//   Unity -batchmode -projectPath xr-client/unity -executeMethod ProjectConfigure.Run -quit

using System.IO;
using Unity.XR.CoreUtils;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.SceneManagement;
using UnityEditor.XR.Management;
using UnityEditor.XR.Management.Metadata;
using UnityEditor.XR.OpenXR.Features;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.XR;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;
using UnityEngine.XR.Management;
using UnityEngine.XR.OpenXR;
using UnityEngine.XR.OpenXR.Features.Android;
using UnityEngine.XR.OpenXR.Features.Interactions;

public static class ProjectConfigure
{
    const string SettingsDir = "Assets/Settings";
    const string ScenePath = "Assets/Scenes/Main.unity";

    public static void Run()
    {
        ConfigureUrp();
        ConfigurePlayer();
        ConfigureXr();
        CreateScene();
        AssetDatabase.SaveAssets();
        Debug.Log("[configure] done");
    }

    static void ConfigureUrp()
    {
        Directory.CreateDirectory(SettingsDir);
        var rendererPath = $"{SettingsDir}/URP-Renderer.asset";
        var assetPath = $"{SettingsDir}/URP.asset";
        var renderer = AssetDatabase.LoadAssetAtPath<UniversalRendererData>(rendererPath);
        if (renderer == null)
        {
            renderer = ScriptableObject.CreateInstance<UniversalRendererData>();
            AssetDatabase.CreateAsset(renderer, rendererPath);
        }
        renderer.postProcessData = null;   // Android XR: no post processing

        var urp = AssetDatabase.LoadAssetAtPath<UniversalRenderPipelineAsset>(assetPath);
        if (urp == null)
        {
            urp = UniversalRenderPipelineAsset.Create(renderer);
            AssetDatabase.CreateAsset(urp, assetPath);
        }
        urp.supportsHDR = false;           // Android XR: no HDR
        urp.msaaSampleCount = 4;
        urp.renderScale = 1f;
        EditorUtility.SetDirty(renderer);
        EditorUtility.SetDirty(urp);

        GraphicsSettings.defaultRenderPipeline = urp;
        for (int i = 0; i < QualitySettings.names.Length; i++)
        {
            QualitySettings.SetQualityLevel(i, false);
            QualitySettings.renderPipeline = urp;
        }
    }

    static void ConfigurePlayer()
    {
        var android = NamedBuildTarget.Android;
        PlayerSettings.companyName = "ETH Zurich Mixed Reality";
        PlayerSettings.productName = "Reachy Meetings";
        PlayerSettings.SetApplicationIdentifier(android, "ch.ethz.reachymeetings");
        PlayerSettings.colorSpace = ColorSpace.Linear;
        PlayerSettings.SetScriptingBackend(android, ScriptingImplementation.IL2CPP);
        PlayerSettings.Android.targetArchitectures = AndroidArchitecture.ARM64;
        PlayerSettings.Android.minSdkVersion = AndroidSdkVersions.AndroidApiLevel32;
        PlayerSettings.Android.applicationEntry = AndroidApplicationEntry.GameActivity;
        PlayerSettings.Android.resizeableActivity = true;
        PlayerSettings.Android.forceInternetPermission = true;   // WebRTC + backend WebSockets from Phase 1 on
        PlayerSettings.SetUseDefaultGraphicsAPIs(BuildTarget.Android, false);
        PlayerSettings.SetGraphicsAPIs(BuildTarget.Android, new[] { GraphicsDeviceType.Vulkan });

        // Input System only (no public API for this setting)
        var player = new SerializedObject(AssetDatabase.LoadAllAssetsAtPath("ProjectSettings/ProjectSettings.asset")[0]);
        player.FindProperty("activeInputHandler").intValue = 1;
        player.ApplyModifiedPropertiesWithoutUndo();
    }

    static void ConfigureXr()
    {
        var group = BuildTargetGroup.Android;
        if (!EditorBuildSettings.TryGetConfigObject(XRGeneralSettings.k_SettingsKey, out XRGeneralSettingsPerBuildTarget perTarget))
        {
            Directory.CreateDirectory("Assets/XR");
            perTarget = ScriptableObject.CreateInstance<XRGeneralSettingsPerBuildTarget>();
            AssetDatabase.CreateAsset(perTarget, "Assets/XR/XRGeneralSettingsPerBuildTarget.asset");
            EditorBuildSettings.AddConfigObject(XRGeneralSettings.k_SettingsKey, perTarget, true);
        }
        if (!perTarget.HasManagerSettingsForBuildTarget(group))
            perTarget.CreateDefaultManagerSettingsForBuildTarget(group);
        var general = perTarget.SettingsForBuildTarget(group);
        general.InitManagerOnStart = true;
        XRPackageMetadataStore.AssignLoader(general.Manager, "UnityEngine.XR.OpenXR.OpenXRLoader", group);

        FeatureHelpers.RefreshFeatures(group);
        var openxr = OpenXRSettings.GetSettingsForBuildTargetGroup(group);
        openxr.renderMode = OpenXRSettings.RenderMode.SinglePassInstanced;
        openxr.GetFeature<AndroidXRSupportFeature>().enabled = true;
        openxr.GetFeature<HandInteractionProfile>().enabled = true;       // Galaxy XR hands
        openxr.GetFeature<KHRSimpleControllerProfile>().enabled = true;
        openxr.GetFeature<OculusTouchControllerProfile>().enabled = true; // Galaxy XR controllers report Touch-compatible input
        EditorUtility.SetDirty(openxr);
        EditorUtility.SetDirty(perTarget);

        foreach (var f in openxr.GetFeatures())
            if (f.enabled) Debug.Log($"[configure] OpenXR feature on: {f.GetType().Name}");
    }

    static void CreateScene()
    {
        Directory.CreateDirectory(Path.GetDirectoryName(ScenePath));
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        var unlit = Shader.Find("Universal Render Pipeline/Unlit");

        // XR rig: origin at the floor, head-tracked camera
        var origin = new GameObject("XR Origin").AddComponent<XROrigin>();
        var offset = new GameObject("Camera Offset");
        offset.transform.SetParent(origin.transform, false);
        var camGo = new GameObject("Main Camera") { tag = "MainCamera" };
        camGo.transform.SetParent(offset.transform, false);
        var cam = camGo.AddComponent<Camera>();
        cam.clearFlags = CameraClearFlags.SolidColor;
        cam.backgroundColor = new Color(0.10f, 0.12f, 0.20f);
        cam.nearClipPlane = 0.05f;
        var tpd = camGo.AddComponent<TrackedPoseDriver>();
        tpd.positionInput = new InputActionProperty(new InputAction("Position", binding: "<XRHMD>/centerEyePosition", expectedControlType: "Vector3"));
        tpd.rotationInput = new InputActionProperty(new InputAction("Rotation", binding: "<XRHMD>/centerEyeRotation", expectedControlType: "Quaternion"));
        tpd.trackingStateInput = new InputActionProperty(new InputAction("Tracking State", binding: "<XRHMD>/trackingState", expectedControlType: "Integer"));
        origin.Camera = cam;
        origin.CameraFloorOffsetObject = offset;
        origin.RequestedTrackingOriginMode = XROrigin.TrackingOriginMode.Floor;

        // Floor
        var floor = GameObject.CreatePrimitive(PrimitiveType.Plane);
        floor.name = "Floor";
        floor.transform.localScale = new Vector3(2, 1, 2);
        floor.GetComponent<Renderer>().sharedMaterial = SaveMaterial("Floor", unlit, new Color(0.18f, 0.20f, 0.28f));

        // Placeholder for the robot video window (Phase 1 puts the WebRTC texture here); 16:9, 2 m ahead
        var window = GameObject.CreatePrimitive(PrimitiveType.Quad);
        window.name = "Video Window";
        window.transform.position = new Vector3(0, 1.5f, 2f);
        window.transform.localScale = new Vector3(1.6f, 0.9f, 1);
        Object.DestroyImmediate(window.GetComponent<Collider>());
        var mat = SaveMaterial("VideoWindow", unlit, Color.white);
        mat.mainTexture = SaveCheckerboard();
        window.GetComponent<Renderer>().sharedMaterial = mat;

        // Frame-rate readout below the window: the Phase 1 go/no-go is measured against this
        var label = new GameObject("Fps Label");
        label.transform.position = new Vector3(0, 0.95f, 2f);
        var hud = label.AddComponent<FpsLabel>();
        hud.textMaterial = SaveMaterial("WorldText", Shader.Find("Reachy/WorldText"), Color.white);

        // Robot camera -> video window (Phase 1)
        var video = new GameObject("Robot Video").AddComponent<RobotVideo>();
        video.window = window.GetComponent<Renderer>();
        video.hud = hud;
        video.latency = video.gameObject.AddComponent<VideoLatency>();
        video.latency.video = video;
        video.flash = video.gameObject.AddComponent<FlashLatency>();
        video.flash.video = video;

        EditorSceneManager.SaveScene(scene, ScenePath);
        EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(ScenePath, true) };
    }

    static Material SaveMaterial(string name, Shader shader, Color color)
    {
        Directory.CreateDirectory("Assets/Materials");
        var path = $"Assets/Materials/{name}.mat";
        var mat = AssetDatabase.LoadAssetAtPath<Material>(path);
        if (mat == null)
        {
            mat = new Material(shader);
            AssetDatabase.CreateAsset(mat, path);
        }
        mat.shader = shader;
        mat.color = color;
        return mat;
    }

    static Texture2D SaveCheckerboard()
    {
        const string path = "Assets/Materials/Checkerboard.png";
        if (!File.Exists(path))
        {
            var tex = new Texture2D(320, 180);
            for (int y = 0; y < 180; y++)
            for (int x = 0; x < 320; x++)
                tex.SetPixel(x, y, ((x / 20 + y / 20) % 2 == 0) ? new Color(0.9f, 0.9f, 0.95f) : new Color(0.25f, 0.45f, 0.85f));
            File.WriteAllBytes(path, tex.EncodeToPNG());
            AssetDatabase.ImportAsset(path);
        }
        return AssetDatabase.LoadAssetAtPath<Texture2D>(path);
    }
}
