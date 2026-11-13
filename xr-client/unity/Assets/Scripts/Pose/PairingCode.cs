// Pairing a headset with the laptop next to Reachy, the parts without Unity (the pad itself is Robot/Pairing.cs):
//   CodeEntry   the six digits being typed
//   LanSearch   who on this Wi-Fi is a gateway (robot/scripts/gateway.py): a UDP broadcast "reachy-meetings?",
//               answered with "reachy-meetings 1 <port> <laptop id> <laptop name>"
// The code then goes to the gateway found (POST /pair), which answers with this headset's token.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Text;
using System.Threading;

public class CodeEntry
{
    public const int Length = 6;
    readonly StringBuilder digits = new StringBuilder();

    public string Code => digits.ToString();
    public int Count => digits.Length;
    public bool Complete => digits.Length == Length;

    /// <summary>One more digit; true when that completed the code. Anything else, or a seventh digit, is ignored.</summary>
    public bool Add(char digit)
    {
        if (digit < '0' || digit > '9' || Complete) return false;
        digits.Append(digit);
        return Complete;
    }

    public void Back()
    {
        if (digits.Length > 0) digits.Length--;
    }

    public void Clear() => digits.Length = 0;

    /// <summary>A code given whole (adb): the six digits, spaces allowed ("493 201"); null if it is none.</summary>
    public static string Clean(string given)
    {
        var s = (given ?? "").Replace(" ", "");
        if (s.Length != Length) return null;
        foreach (char c in s) if (c < '0' || c > '9') return null;
        return s;
    }
}

public static class LanSearch
{
    public const int Port = 8790;
    public static readonly byte[] Question = Encoding.ASCII.GetBytes("reachy-meetings?");

    public class Found
    {
        public string Id, Name, Gateway;                                 // Gateway: ws://<address>:<port>
        public string PairUrl => "http" + Gateway.Substring(2) + "/pair";
    }

    /// <summary>A gateway's answer, sent from `address`; null for anything else.</summary>
    public static Found Parse(string answer, string address)
    {
        var parts = (answer ?? "").Trim().Split(new[] { ' ' }, 5);
        if (parts.Length < 4 || parts[0] != "reachy-meetings" || parts[1] != "1") return null;
        if (!int.TryParse(parts[2], out int port) || port < 1 || port > 65535 || parts[3].Length == 0) return null;
        return new Found { Id = parts[3], Name = parts.Length > 4 ? parts[4] : "", Gateway = $"ws://{address}:{port}" };
    }

    /// <summary>The broadcast address of a network: 192.168.50.53 with 255.255.255.0 -> 192.168.50.255.</summary>
    public static IPAddress Broadcast(IPAddress address, IPAddress mask)
    {
        byte[] a = address.GetAddressBytes(), m = mask.GetAddressBytes();
        if (a.Length != 4 || m.Length != 4) return null;
        for (int i = 0; i < 4; i++) a[i] |= (byte)~m[i];
        return new IPAddress(a);
    }

    /// <summary>Where to ask: everyone, and each network this device is on (some access points only pass the latter).
    /// A device that does not tell its netmask is taken to be on a /24, as home and office Wi-Fi mostly is.</summary>
    public static List<IPAddress> Targets()
    {
        var targets = new List<IPAddress> { IPAddress.Broadcast };
        void Add(IPAddress a) { if (a != null && !targets.Contains(a)) targets.Add(a); }
        try
        {
            foreach (var nic in NetworkInterface.GetAllNetworkInterfaces())
            {
                if (nic.OperationalStatus != OperationalStatus.Up || nic.NetworkInterfaceType == NetworkInterfaceType.Loopback) continue;
                foreach (var u in nic.GetIPProperties().UnicastAddresses)
                {
                    if (u.Address.AddressFamily != AddressFamily.InterNetwork) continue;
                    Add(Broadcast(u.Address, IPAddress.Parse("255.255.255.0")));
                    try { if (u.IPv4Mask != null && !u.IPv4Mask.Equals(IPAddress.Any)) Add(Broadcast(u.Address, u.IPv4Mask)); }
                    catch (Exception) { }   // not every platform knows the mask
                }
            }
        }
        catch (Exception) { }   // no list of interfaces: the plain broadcast has to do
        return targets;
    }

    /// <summary>Ask three times within `waitMs` and collect who answers, one entry per laptop. Blocks: call it off the
    /// main thread. `to`: where to ask instead of Targets() (tests).</summary>
    public static List<Found> Find(int waitMs, int port = Port, IEnumerable<IPAddress> to = null)
    {
        var found = new Dictionary<string, Found>();
        using var udp = new UdpClient(new IPEndPoint(IPAddress.Any, 0)) { EnableBroadcast = true };
        var targets = new List<IPAddress>(to ?? Targets());
        var clock = Stopwatch.StartNew();
        int asked = 0;
        while (clock.ElapsedMilliseconds < waitMs)
        {
            if (asked < 3 && clock.ElapsedMilliseconds >= asked * 300)
            {
                asked++;
                foreach (var target in targets)
                {
                    try { udp.Send(Question, Question.Length, new IPEndPoint(target, port)); }
                    catch (SocketException) { }   // e.g. no route for the plain broadcast: the others may still work
                }
            }
            if (udp.Available == 0) { Thread.Sleep(20); continue; }
            var from = new IPEndPoint(IPAddress.Any, 0);
            try
            {
                var f = Parse(Encoding.UTF8.GetString(udp.Receive(ref from)), from.Address.ToString());
                if (f != null) found[f.Id] = f;
            }
            catch (SocketException) { }   // an ICMP "nobody there" for one of the targets
        }
        return new List<Found>(found.Values);
    }
}
