// Where the headset reaches the laptop next to Reachy: one address, the gateway (robot/scripts/gateway.py), and
// this headset's token. Everything goes through that one door, each part under its own path:
//   /daemon/...   Reachy daemon (set_target, state, wake-up)     /signalling   video signalling
//   /backend      captions, translation, room sound              /time         clock for the latency tests
// The gateway lets a request in only with the token in the Authorization header (Sign, Authorization).
// No Unity in here.

using System;
using System.Net.WebSockets;

public class Endpoints
{
    /// <summary>The address everything hangs under, as given: ws://laptop:8790.</summary>
    public readonly string Gateway;
    public readonly Uri Signalling, Backend, Clock, DaemonTarget, DaemonState;
    /// <summary>For the daemon's HTTP calls: append the daemon's own path, e.g. "/api/daemon/status".</summary>
    public readonly string DaemonHttp;
    /// <summary>Value of the Authorization header for HTTP requests.</summary>
    public readonly string Authorization;

    Endpoints(string ws, string token)
    {
        Gateway = ws;
        Signalling = new Uri(ws + "/signalling");
        Backend = new Uri(ws + "/backend");
        Clock = new Uri(ws + "/time");
        DaemonTarget = new Uri(ws + "/daemon/api/move/ws/set_target");
        DaemonState = new Uri(ws + "/daemon/api/state/ws/full?frequency=60&with_head_pose=true&with_body_yaw=true&with_antenna_positions=false");
        DaemonHttp = "http" + ws.Substring(2) + "/daemon";   // ws -> http, wss -> https
        Authorization = "Bearer " + token;
    }

    /// <summary>gateway: ws://laptop:8790 (later wss://…, possibly with a path). Null while the headset is not set
    /// up: no address, no token, or an address that is none.</summary>
    public static Endpoints From(string gateway, string token)
    {
        gateway = (gateway ?? "").Trim().TrimEnd('/');
        token = (token ?? "").Trim();
        if (token.Length == 0 || !Uri.TryCreate(gateway, UriKind.Absolute, out var uri)) return null;
        if (uri.Scheme != "ws" && uri.Scheme != "wss") return null;
        if (uri.Query.Length > 0 || uri.Fragment.Length > 0) return null;
        return new Endpoints(gateway, token);
    }

    /// <summary>Call before ConnectAsync: without the token the gateway answers 401.</summary>
    public void Sign(ClientWebSocket ws) => ws.Options.SetRequestHeader("Authorization", Authorization);
}
