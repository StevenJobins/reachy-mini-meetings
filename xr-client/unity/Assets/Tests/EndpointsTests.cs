using System;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using NUnit.Framework;

public class EndpointsTests
{
    [Test]
    public void EveryPartHangsUnderTheOneAddress()
    {
        var e = Endpoints.From("ws://192.168.1.5:8790", "tok");
        Assert.AreEqual("ws://192.168.1.5:8790/signalling", e.Signalling.ToString());
        Assert.AreEqual("ws://192.168.1.5:8790/backend", e.Backend.ToString());
        Assert.AreEqual("ws://192.168.1.5:8790/time", e.Clock.ToString());
        Assert.AreEqual("ws://192.168.1.5:8790/daemon/api/move/ws/set_target", e.DaemonTarget.ToString());
        Assert.AreEqual("/daemon/api/state/ws/full", e.DaemonState.AbsolutePath);
        StringAssert.Contains("frequency=60", e.DaemonState.Query);
        Assert.AreEqual("http://192.168.1.5:8790/daemon", e.DaemonHttp);
        Assert.AreEqual("Bearer tok", e.Authorization);
    }

    [Test]
    public void AnAddressOnTheInternetKeepsItsPathAndIsEncrypted()
    {
        var e = Endpoints.From(" wss://meet.example.org/reachy-7/ ", " tok\n");
        Assert.AreEqual("wss://meet.example.org/reachy-7/backend", e.Backend.ToString());
        Assert.AreEqual("https://meet.example.org/reachy-7/daemon", e.DaemonHttp);
        Assert.AreEqual("Bearer tok", e.Authorization);
    }

    [TestCase(null, "tok")]
    [TestCase("", "tok")]
    [TestCase("ws://192.168.1.5:8790", "")]
    [TestCase("ws://192.168.1.5:8790", null)]
    [TestCase("192.168.1.5:8790", "tok")]
    [TestCase("http://192.168.1.5:8790", "tok")]
    [TestCase("ws://192.168.1.5:8790/?token=tok", "tok")]
    [TestCase("off", "tok")]
    public void NotSetUpIsNull(string gateway, string token)
    {
        Assert.IsNull(Endpoints.From(gateway, token));
    }

    /// <summary>The token must really leave in the request head: the gateway decides on nothing else.</summary>
    [Test]
    public void SignPutsTheTokenInTheRequestHead()
    {
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        try
        {
            int port = ((IPEndPoint)listener.LocalEndpoint).Port;
            var laptop = Endpoints.From($"ws://127.0.0.1:{port}", "s3cret-token");
            var head = Task.Run(() =>
            {
                using var client = listener.AcceptTcpClient();
                var reader = new StreamReader(client.GetStream(), Encoding.ASCII);
                var text = new StringBuilder();
                for (var line = reader.ReadLine(); !string.IsNullOrEmpty(line); line = reader.ReadLine()) text.Append(line).Append('\n');
                return text.ToString();   // closing without an answer ends the client's attempt
            });
            var connect = Task.Run(async () =>
            {
                using var ws = new ClientWebSocket();
                using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
                laptop.Sign(ws);
                try { await ws.ConnectAsync(laptop.DaemonTarget, cts.Token); }
                catch (Exception) { }   // the stand-in server never upgrades
            });
            Assert.IsTrue(head.Wait(TimeSpan.FromSeconds(10)), "no request arrived");
            connect.Wait(TimeSpan.FromSeconds(10));
            StringAssert.StartsWith("GET /daemon/api/move/ws/set_target HTTP/1.1\n", head.Result);
            StringAssert.Contains("\nAuthorization: Bearer s3cret-token\n", head.Result);
        }
        finally { listener.Stop(); }
    }
}
