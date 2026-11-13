using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading.Tasks;
using NUnit.Framework;

public class PairingTests
{
    [Test]
    public void SixDigitsCompleteTheCode()
    {
        var entry = new CodeEntry();
        foreach (char c in "49320") Assert.IsFalse(entry.Add(c));
        Assert.IsFalse(entry.Complete);
        Assert.IsTrue(entry.Add('1'));
        Assert.AreEqual("493201", entry.Code);
        Assert.IsFalse(entry.Add('7'), "a seventh digit is ignored");
        Assert.AreEqual("493201", entry.Code);
    }

    [Test]
    public void BackTakesTheLastDigitAndOnlyDigitsCount()
    {
        var entry = new CodeEntry();
        entry.Back();
        entry.Add('1'); entry.Add('x'); entry.Add(' '); entry.Add('2');
        Assert.AreEqual("12", entry.Code);
        entry.Back();
        Assert.AreEqual("1", entry.Code);
        entry.Clear();
        Assert.AreEqual(0, entry.Count);
    }

    [TestCase("493201", "493201")]
    [TestCase("493 201", "493201")]
    [TestCase("49320", null)]
    [TestCase("4932011", null)]
    [TestCase("49320a", null)]
    [TestCase("", null)]
    [TestCase(null, null)]
    public void ACodeGivenWhole(string given, string expected)
    {
        Assert.AreEqual(expected, CodeEntry.Clean(given));
    }

    [Test]
    public void AGatewaysAnswerGivesItsAddress()
    {
        var f = LanSearch.Parse("reachy-meetings 1 8790 9776bd854e933d06 MacBook Pro von Kevin\n", "192.168.50.53");
        Assert.AreEqual("9776bd854e933d06", f.Id);
        Assert.AreEqual("MacBook Pro von Kevin", f.Name);
        Assert.AreEqual("ws://192.168.50.53:8790", f.Gateway);
        Assert.AreEqual("http://192.168.50.53:8790/pair", f.PairUrl);
        Assert.IsNotNull(Endpoints.From(f.Gateway, "tok"));
        Assert.AreEqual("", LanSearch.Parse("reachy-meetings 1 8790 abc", "10.0.0.2").Name);
    }

    [TestCase("")]
    [TestCase(null)]
    [TestCase("reachy-meetings?")]
    [TestCase("reachy-meetings 2 8790 abc Mac")]
    [TestCase("reachy-meetings 1 port abc Mac")]
    [TestCase("reachy-meetings 1 0 abc Mac")]
    [TestCase("reachy-meetings 1 99999 abc Mac")]
    [TestCase("someone-else 1 8790 abc Mac")]
    public void AnythingElseIsNoGateway(string answer)
    {
        Assert.IsNull(LanSearch.Parse(answer, "10.0.0.2"));
    }

    [TestCase("192.168.50.53", "255.255.255.0", "192.168.50.255")]
    [TestCase("10.3.77.5", "255.255.0.0", "10.3.255.255")]
    [TestCase("172.16.9.130", "255.255.255.128", "172.16.9.255")]
    public void BroadcastAddressOfANetwork(string address, string mask, string expected)
    {
        Assert.AreEqual(expected, LanSearch.Broadcast(IPAddress.Parse(address), IPAddress.Parse(mask)).ToString());
    }

    [Test]
    public void TargetsAlwaysIncludeThePlainBroadcast()
    {
        CollectionAssert.Contains(LanSearch.Targets(), IPAddress.Broadcast);
    }

    /// <summary>Over real sockets: a stand-in laptop answers the question; one that says something else is left out.</summary>
    [Test]
    public void FindCollectsWhoAnswers()
    {
        using var laptop = new UdpClient(new IPEndPoint(IPAddress.Loopback, 0));
        int port = ((IPEndPoint)laptop.Client.LocalEndPoint).Port;
        var answering = Task.Run(() =>
        {
            for (int i = 0; i < 3; i++)   // Find asks three times
            {
                var from = new IPEndPoint(IPAddress.Any, 0);
                var question = laptop.Receive(ref from);
                if (Encoding.ASCII.GetString(question) != "reachy-meetings?") continue;
                foreach (var answer in new[] { "reachy-meetings 1 8790 abc Test Mac", "hello there" })
                    laptop.Send(Encoding.UTF8.GetBytes(answer), Encoding.UTF8.GetByteCount(answer), from);
            }
        });
        var found = Task.Run(() => LanSearch.Find(900, port, new[] { IPAddress.Loopback }));
        Assert.IsTrue(found.Wait(5000), "the search did not end");
        Assert.AreEqual(1, found.Result.Count, "one laptop, however often it answered");
        Assert.AreEqual("abc", found.Result[0].Id);
        Assert.AreEqual("ws://127.0.0.1:8790", found.Result[0].Gateway);
        answering.Wait(2000);
    }

    [Test]
    public void FindEndsWhenNobodyAnswers()
    {
        using var silent = new UdpClient(new IPEndPoint(IPAddress.Loopback, 0));
        int port = ((IPEndPoint)silent.Client.LocalEndPoint).Port;
        var found = Task.Run(() => LanSearch.Find(300, port, new[] { IPAddress.Loopback }));
        Assert.IsTrue(found.Wait(5000));
        Assert.AreEqual(0, found.Result.Count);
    }
}
