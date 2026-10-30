// Headset goes to sleep -> close the app completely. Android only pauses it, and after waking up that paused app could
// not be reached any more, neither from the launcher nor by itself. Opening the app is then always a clean start:
// the laptop address is remembered and Reachy waits for a pinch.
// Checked on a background thread, because Unity's own pause callback does not run when the headset falls asleep during
// startup, and the Unity loop stops as soon as the app is paused. A pause without sleep (system menu) keeps the app.

using System.Threading;
using UnityEngine;

public class AppLifecycle : MonoBehaviour
{
#if UNITY_ANDROID && !UNITY_EDITOR
    const int CheckMs = 1000;
    const int AsleepChecks = 2;   // about 2 s asleep, so a short flicker of the sensor does not close the app

    void Awake()
    {
        new Thread(Watch) { IsBackground = true, Name = "AppLifecycle" }.Start();
    }

    static void Watch()
    {
        AndroidJNI.AttachCurrentThread();
        using var player = new AndroidJavaClass("com.unity3d.player.UnityPlayer");
        using var activity = player.GetStatic<AndroidJavaObject>("currentActivity");
        using var power = activity.Call<AndroidJavaObject>("getSystemService", "power");
        for (int asleep = 0; asleep < AsleepChecks;)
        {
            Thread.Sleep(CheckMs);
            asleep = power.Call<bool>("isInteractive") ? 0 : asleep + 1;
        }
        Debug.Log("[app] headset asleep, closing");
        activity.Call("finishAndRemoveTask");
        using var process = new AndroidJavaClass("android.os.Process");
        process.CallStatic("killProcess", process.CallStatic<int>("myPid"));
    }
#endif
}
