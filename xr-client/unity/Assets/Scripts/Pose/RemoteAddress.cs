// Finding the laptop from another network. Started with --remote, the laptop has a public address that is new on
// every start (a Cloudflare quick tunnel), and posts it on a public board (ntfy.sh) as a notice for each paired
// headset (robot/scripts/gateway.py: topic(), notice()):
//   where     a topic made from this headset's token, so nobody else knows where to look or to post
//   what      "<wss address> <unix time> <signature>", the signature made from the token too
// The signature is what counts: the headset sends its token to whatever address it takes from here, so it takes
// none that its own laptop did not sign, and none that is not encrypted (wss).
// No Unity in here.

using System;
using System.Security.Cryptography;
using System.Text;

public static class RemoteAddress
{
    public const string Board = "https://ntfy.sh";
    public const long MaxAgeS = 12 * 3600;   // the board keeps a notice that long

    static string Mac(string token, string text)
    {
        using var h = new HMACSHA256(Encoding.UTF8.GetBytes(token));
        var hex = new StringBuilder();
        foreach (byte b in h.ComputeHash(Encoding.UTF8.GetBytes(text))) hex.Append(b.ToString("x2"));
        return hex.ToString();
    }

    public static string Topic(string token) => "rm-" + Mac(token, "reachy-meetings address").Substring(0, 40);

    /// <summary>The latest notice for this headset, as one JSON line with a "message" (or nothing).</summary>
    public static string LookupUrl(string token) => $"{Board}/{Topic(token)}/json?poll=1&since=latest";

    /// <summary>The address in a notice, or null: not signed with this token, not wss, too old, or no address.</summary>
    public static string Read(string notice, string token, long nowUnixS)
    {
        var parts = (notice ?? "").Split(' ');
        if (parts.Length != 3 || string.IsNullOrEmpty(token) || !parts[0].StartsWith("wss://")) return null;
        if (!long.TryParse(parts[1], out long ts) || ts < 0 || nowUnixS - ts >= MaxAgeS) return null;
        string expected = Mac(token, parts[0] + " " + parts[1]);
        if (parts[2].Length != expected.Length) return null;
        int differs = 0;
        for (int i = 0; i < expected.Length; i++) differs |= parts[2][i] ^ expected[i];
        if (differs != 0) return null;
        return Endpoints.From(parts[0], token) != null ? parts[0] : null;
    }
}
