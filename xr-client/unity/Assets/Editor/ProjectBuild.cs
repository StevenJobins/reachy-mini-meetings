// Command-line Android build (see xr-client/unity/README.md):
//   Unity -batchmode -projectPath xr-client/unity -buildTarget Android -executeMethod ProjectBuild.Android -quit

using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

public static class ProjectBuild
{
    public const string ApkPath = "Builds/ReachyMeetings.apk";

    public static void Android()
    {
        var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
        {
            scenes = new[] { "Assets/Scenes/Main.unity" },
            locationPathName = ApkPath,
            target = BuildTarget.Android,
            options = BuildOptions.Development,
        });
        var s = report.summary;
        Debug.Log($"[build] {s.result} {s.totalSize / 1e6:0.0} MB in {s.totalTime.TotalSeconds:0} s, {s.totalErrors} errors -> {ApkPath}");
        if (s.result != BuildResult.Succeeded) EditorApplication.Exit(1);
    }
}
