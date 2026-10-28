// One-shot project setup, run from the command line (see xr-client/unity/README.md):
//   Unity -batchmode -projectPath xr-client/unity -executeMethod ProjectSetup.AddPackages
// Adds the packages without version numbers, so the Package Manager picks the ones verified for this editor.

using UnityEditor;
using UnityEditor.PackageManager;
using UnityEditor.PackageManager.Requests;
using UnityEngine;

public static class ProjectSetup
{
    static readonly string[] Packages =
    {
        "com.unity.render-pipelines.universal",
        "com.unity.inputsystem",
        "com.unity.xr.management",
        "com.unity.xr.openxr",
        "com.unity.xr.androidxr-openxr",    // Galaxy XR
        "com.unity.xr.interaction.toolkit",
        "com.unity.webrtc",                 // Phase 1: robot video
    };

    static AddAndRemoveRequest request;

    public static void AddPackages()
    {
        request = Client.AddAndRemove(Packages);
        EditorApplication.update += Poll;
    }

    static void Poll()
    {
        if (!request.IsCompleted) return;
        EditorApplication.update -= Poll;
        if (request.Status == StatusCode.Success)
        {
            foreach (var p in request.Result) Debug.Log($"[setup] {p.name} {p.version}");
            EditorApplication.Exit(0);
        }
        else
        {
            Debug.LogError($"[setup] {request.Error.message}");
            EditorApplication.Exit(1);
        }
    }
}
