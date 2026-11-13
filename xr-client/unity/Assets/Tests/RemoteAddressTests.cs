using NUnit.Framework;

public class RemoteAddressTests
{
    // What robot/scripts/gateway.py makes for the same token, address and time (topic(), notice()): both sides
    // must agree to the letter, or the headset looks in the wrong place or trusts nothing.
    const string Token = "s3cret-token", Url = "wss://quiet-river-1234.trycloudflare.com";
    const string Notice = Url + " 1763000000 7ba94013575d463df6a30173f8ad7c6f0dbd622b42d2b823374af98758d74dbb";
    const long Now = 1763000100;

    [Test]
    public void TopicIsTheOneTheLaptopPostsTo()
    {
        Assert.AreEqual("rm-7524e9ddbdeb52034ea00915e0db7644ac78abdf", RemoteAddress.Topic(Token));
        Assert.AreEqual("https://ntfy.sh/rm-7524e9ddbdeb52034ea00915e0db7644ac78abdf/json?poll=1&since=latest",
            RemoteAddress.LookupUrl(Token));
        Assert.AreNotEqual(RemoteAddress.Topic(Token), RemoteAddress.Topic("another-token"));
    }

    [Test]
    public void TheLaptopsNoticeIsRead()
    {
        Assert.AreEqual(Url, RemoteAddress.Read(Notice, Token, Now));
    }

    [Test]
    public void ANoticeForAnotherHeadsetIsNot()
    {
        Assert.IsNull(RemoteAddress.Read(Notice, "another-token", Now));
        Assert.IsNull(RemoteAddress.Read(Notice, "", Now));
        Assert.IsNull(RemoteAddress.Read(Notice, null, Now));
    }

    [Test]
    public void AChangedNoticeIsNot()
    {
        Assert.IsNull(RemoteAddress.Read(Notice.Replace("quiet", "loud"), Token, Now), "another address");
        Assert.IsNull(RemoteAddress.Read(Notice.Replace("1763000000", "1763000001"), Token, Now), "another time");
        Assert.IsNull(RemoteAddress.Read(Notice.Substring(0, Notice.Length - 1) + "c", Token, Now), "another signature");
        Assert.IsNull(RemoteAddress.Read(Notice.Substring(0, Notice.Length - 2), Token, Now), "a cut signature");
    }

    [Test]
    public void AnOldNoticeIsNot()
    {
        Assert.AreEqual(Url, RemoteAddress.Read(Notice, Token, 1763000000 + RemoteAddress.MaxAgeS - 1));
        Assert.IsNull(RemoteAddress.Read(Notice, Token, 1763000000 + RemoteAddress.MaxAgeS));
    }

    [TestCase(null)]
    [TestCase("")]
    [TestCase("wss://x")]
    [TestCase("wss://x 1763000000")]
    [TestCase("wss://x 1763000000 abc def")]
    [TestCase("ws://192.168.1.5:8790 1763000000 7ba94013575d463df6a30173f8ad7c6f0dbd622b42d2b823374af98758d74dbb")]
    [TestCase("hello there, this is not a notice")]
    public void AnythingElseIsNoAddress(string notice)
    {
        Assert.IsNull(RemoteAddress.Read(notice, Token, Now));
    }
}
