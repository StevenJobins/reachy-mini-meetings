// What a headset shows that does not know a laptop yet: a number pad for the six-digit code on the screen of the
// laptop next to Reachy (start_meeting.sh shows it). With the code complete, the headset asks the Wi-Fi who is a
// gateway (LanSearch), sends the code to it (POST /pair) and gets its token: no cable, no address to type.
// It comes back by itself when the laptop says it does not know this headset any more (401: removed there).
//
// Also here, without anything on show: where the laptop is now (Refind). On this Wi-Fi the same search finds it
// again when its address changed; on another network (at home) the headset looks up the public address the laptop
// posted for it (RemoteAddress), and the app carries on there.
//
// Keys are pressed like everything else: point with either hand, or look, and pinch (RobotControl.HandlePress).
// For tests without wearing the headset:  adb shell am start -n <app> -e pair <code>

using System;
using System.Collections;
using System.Collections.Generic;
using System.Text;
using System.Threading.Tasks;
using UnityEngine;
using UnityEngine.Networking;

public class Pairing : MonoBehaviour
{
    const float Distance = 1.3f, Below = 0.1f, FollowRate = 3f, FollowFromDeg = 30f, FollowToDeg = 3f;
    const float PanelW = 0.66f, PanelH = 1.02f, TextW = PanelW - 0.1f;
    const float KeyW = 0.16f, KeyH = 0.115f, KeyGap = 0.02f, KeysTop = 0.07f, SlotW = 0.075f, SlotH = 0.1f, SlotsY = 0.225f;
    const float QuietS = 8f;          // after pairing: the laptop's "unknown headset" from before must not reopen the pad
    const float RefindEveryS = 6f, LookUpEveryS = 30f;   // the board allows about one question in five seconds
    const int SearchMs = 1500;
    const string IdKey = "headset_id";
    static readonly string[] Keys = { "1", "2", "3", "4", "5", "6", "7", "8", "9", "←", "0", "" };
    static readonly Color KeyColor = new Color(1, 1, 1, 0.08f), KeyOver = new Color(0.30f, 0.76f, 1f, 0.30f),
        SlotColor = new Color(0, 0, 0, 0.35f);

    public RobotVideo video;
    public RobotControl control;
    public Tutorial tutorial;
    public Transform head;
    public Material textMaterial, panelMaterial;

    /// <summary>The pad is on show: no picture, no menu, nothing else to press (Curtain).</summary>
    public bool Open { get; private set; }
    /// <summary>That hand points at a key: its pinch goes here (RobotControl).</summary>
    public bool Hovered(bool right) => Open && over[right ? 1 : 0] >= 0;

    [Serializable] class Ask { public string code, name; }
    [Serializable] class Reply { public string token, id, name; }

    enum Step { Typing, Searching, Asking }

    readonly CodeEntry entry = new CodeEntry();
    readonly int[] over = { -1, -1 };
    readonly Ui.Rect[] keys = new Ui.Rect[12], slots = new Ui.Rect[CodeEntry.Length];
    readonly TextMesh[] digits = new TextMesh[CodeEntry.Length];
    readonly Ui.Cursor[] cursors = new Ui.Cursor[2];
    Transform card;
    TextMesh title, hint, status;
    Step step = Step.Typing;
    string message, hintShown, statusShown;
    float yaw, quietUntil, nextRefind, nextLookUp;
    bool following, locating, startChecked;

    static Vector3 KeyAt(int k) => new Vector3((k % 3 - 1) * (KeyW + KeyGap), KeysTop - k / 3 * (KeyH + KeyGap), -0.001f);
    static Vector3 SlotAt(int i) => new Vector3((i - 2.5f) * (SlotW + 0.012f) + (i < 3 ? -0.012f : 0.012f), SlotsY, -0.001f);

    static int KeyUnder(Vector3? p)
    {
        for (int k = 0; k < Keys.Length; k++)
            if (Keys[k].Length > 0 && Ui.Inside(p, KeyAt(k), KeyW + KeyGap, KeyH + KeyGap)) return k;
        return -1;
    }

    void Start()
    {
        card = new GameObject("Card").transform;
        card.SetParent(transform, false);
        var panel = Ui.Quad(card, "Panel", panelMaterial, 3034);
        panel.Set(PanelW, PanelH, 0.06f, Vector3.zero, Ui.Glass);
        panel.Border(Ui.Edge);
        var ink = Ui.TextMaterial(textMaterial, 3037);
        title = Label("Title", ink, 0.6f, 0.42f, FontStyle.Bold);
        title.text = Ui.German ? "Mit Reachy verbinden" : "Connect to Reachy";
        hint = Label("Hint", ink, 0.36f, 0.335f);
        status = Label("Status", ink, 0.34f, -0.445f);
        for (int i = 0; i < slots.Length; i++)
        {
            slots[i] = Ui.Quad(card, "Slot", panelMaterial, 3035);
            digits[i] = Label("Digit", ink, 0.9f, 0, FontStyle.Bold);
            digits[i].transform.localPosition = SlotAt(i) + new Vector3(0, 0, -0.002f);
        }
        for (int k = 0; k < keys.Length; k++)
        {
            keys[k] = Ui.Quad(card, "Key " + Keys[k], panelMaterial, 3035);
            keys[k].Show(Keys[k].Length > 0);
            var t = Label("KeyText", ink, 0.8f, 0, FontStyle.Bold);
            t.transform.localPosition = KeyAt(k) + new Vector3(0, 0, -0.002f);
            t.text = Keys[k];
        }
        for (int i = 0; i < 2; i++) cursors[i] = new Ui.Cursor(card, panelMaterial);
        card.gameObject.SetActive(false);

        var given = CodeEntry.Clean(RobotVideo.ReadIntentExtra("pair"));
        if (given != null) StartCoroutine(Run(given));
    }

    TextMesh Label(string name, Material ink, float scale, float y, FontStyle style = FontStyle.Normal)
    {
        var t = Ui.Text(card, name, ink, 64, scale, TextAnchor.MiddleCenter, style);
        t.transform.localPosition = new Vector3(0, y, -0.002f);
        return t;
    }

    /// <summary>Set a text, smaller if it would stick out of the panel.</summary>
    static void Fit(TextMesh t, string text, float scale, ref string shown)
    {
        if (text == shown) return;
        shown = text;
        t.text = text;
        t.transform.localScale = Vector3.one * scale;
        float w = Ui.Size(t).x;
        if (w > TextW) t.transform.localScale = Vector3.one * scale * TextW / w;
    }

    void Update()
    {
        bool tour = tutorial != null && tutorial.Open;   // the tutorial first: it teaches the pinch the pad needs
        bool unknown = video.Laptop != null && control.DaemonState == "unreachable" && control.LaptopAnswer == 401;
        bool open = !tour && Time.unscaledTime >= quietUntil && (video.Laptop == null || unknown);
        if (open != Open)
        {
            Open = open;
            card.gameObject.SetActive(open);
            over[0] = over[1] = -1;
            if (open)
            {
                entry.Clear();
                message = null;
                yaw = head.localEulerAngles.y;
                Debug.Log($"[pairing] pad open ({(unknown ? "the laptop does not know this headset" : "not set up")})");
            }
        }
        Refind();
        if (!Open) return;

        // in front of you; it stays put while you look from key to key and comes after you once you look away
        float off = Mathf.Abs(Mathf.DeltaAngle(yaw, head.localEulerAngles.y));
        if (off > FollowFromDeg) following = true;
        else if (off < FollowToDeg) following = false;
        if (following) yaw = Mathf.LerpAngle(yaw, head.localEulerAngles.y, 1 - Mathf.Exp(-Time.unscaledDeltaTime * FollowRate));
        card.localRotation = Quaternion.Euler(0, yaw, 0);
        card.localPosition = head.localPosition + card.localRotation * new Vector3(0, -Below, Distance);

        for (int hand = 0; hand < 2; hand++)
        {
            bool right = hand == 1;
            var aim = XrInput.Aim(right, head);
            var p = Ui.Pick(card, aim, head, q => KeyUnder(q) >= 0, out var byHand);
            over[hand] = step == Step.Typing ? KeyUnder(p) : -1;
            if (aim != null && Ui.Inside(byHand, Vector3.zero, PanelW + 0.2f, PanelH + 0.2f))
                cursors[hand].Show(byHand.Value, right ? control.RightLevel : control.LeftLevel);
            else cursors[hand].Hide();
        }
        Draw(unknown);
    }

    void Draw(bool unknown)
    {
        bool de = Ui.German;
        Fit(hint, "<color=" + Ui.SoftHex + ">" + (unknown
            ? (de ? "Der Laptop kennt diese Brille nicht mehr.\nGib den Code ein, den er zeigt."
                  : "The laptop does not know this headset any more.\nEnter the code it shows.")
            : (de ? "Gib den Code ein, den der Laptop\nbei Reachy zeigt."
                  : "Enter the code shown on the laptop\nnext to Reachy.")) + "</color>", 0.36f, ref hintShown);
        for (int i = 0; i < slots.Length; i++)
        {
            slots[i].Set(SlotW, SlotH, 0.014f, SlotAt(i), SlotColor);
            slots[i].Border(i == entry.Count && step == Step.Typing ? Ui.Accent : Ui.Edge);
            string d = i < entry.Count ? entry.Code[i].ToString() : "";
            if (digits[i].text != d) digits[i].text = d;
        }
        for (int k = 0; k < keys.Length; k++)
        {
            bool on = over[0] == k || over[1] == k;
            keys[k].Set(KeyW, KeyH, 0.03f, KeyAt(k), on ? KeyOver : KeyColor);
            keys[k].Border(on ? Ui.Accent : Color.clear);
        }
        string say = step == Step.Searching ? (de ? "Suche den Laptop " : "Looking for the laptop ") + Ui.Dots()
            : step == Step.Asking ? (de ? "Verbinde " : "Connecting ") + Ui.Dots()
            : message != null ? "<color=#fbbf24>" + message + "</color>" : "";
        Fit(status, say, 0.34f, ref statusShown);
    }

    public void Press(bool right)
    {
        int k = over[right ? 1 : 0];
        if (k < 0 || step != Step.Typing) return;
        message = null;
        if (Keys[k] == "←") entry.Back();
        else if (entry.Add(Keys[k][0])) StartCoroutine(Run(entry.Code));
    }

    /// <summary>Model and four characters of its own, so two headsets of one kind stay two on the laptop's list.</summary>
    static string HeadsetName()
    {
        string id = PlayerPrefs.GetString(IdKey, "");
        if (id.Length == 0)
        {
            id = Guid.NewGuid().ToString("N").Substring(0, 4);
            PlayerPrefs.SetString(IdKey, id);
            PlayerPrefs.Save();
        }
        return $"{SystemInfo.deviceModel} {id}";
    }

    IEnumerator Run(string code)
    {
        step = Step.Searching;
        message = null;
        var search = Task.Run(() => LanSearch.Find(SearchMs));
        while (!search.IsCompleted) yield return null;
        var found = search.IsFaulted ? new List<LanSearch.Found>() : search.Result;
        Debug.Log($"[pairing] {found.Count} laptop(s) answered on this network");
        if (found.Count == 0)
        {
            Fail(Ui.German ? "Kein Laptop gefunden. Läuft dort das Meeting-Programm?\nSind beide im gleichen WLAN?"
                           : "No laptop found. Is the meeting program running there?\nAre both on the same Wi-Fi?");
            yield break;
        }
        step = Step.Asking;
        bool wrong = false, paused = false;
        var body = Encoding.UTF8.GetBytes(JsonUtility.ToJson(new Ask { code = code, name = HeadsetName() }));
        foreach (var laptop in found)   // several laptops with Reachys on one Wi-Fi: the code fits one of them
        {
            using var r = new UnityWebRequest(laptop.PairUrl, "POST")
            {
                uploadHandler = new UploadHandlerRaw(body), downloadHandler = new DownloadHandlerBuffer(), timeout = 5,
            };
            r.SetRequestHeader("Content-Type", "application/json");
            yield return r.SendWebRequest();
            if (r.result == UnityWebRequest.Result.Success)
            {
                var reply = JsonUtility.FromJson<Reply>(r.downloadHandler.text);
                if (!string.IsNullOrEmpty(reply?.token))
                {
                    Debug.Log($"[pairing] paired with \"{laptop.Name}\" at {laptop.Gateway}");
                    quietUntil = Time.unscaledTime + QuietS;
                    entry.Clear();
                    step = Step.Typing;
                    video.SetLaptop(laptop.Gateway, reply.token, string.IsNullOrEmpty(reply.id) ? laptop.Id : reply.id);
                    yield break;
                }
            }
            wrong |= r.responseCode == 403;
            paused |= r.responseCode == 429;
            Debug.Log($"[pairing] \"{laptop.Name}\" at {laptop.Gateway}: {r.responseCode} {r.error}");
        }
        bool de = Ui.German;
        Fail(wrong ? (de ? "Der Code stimmt nicht. Bitte nochmals eingeben." : "That code is not right. Please enter it again.")
            : paused ? (de ? "Zu viele falsche Codes. Bitte eine Minute warten:\nder Laptop zeigt dann einen neuen Code."
                           : "Too many wrong codes. Please wait a minute:\nthe laptop then shows a new code.")
            : (de ? "Der Laptop antwortet nicht. Bitte nochmals versuchen." : "The laptop does not answer. Please try again."));
    }

    void Fail(string why)
    {
        message = why;
        entry.Clear();
        step = Step.Typing;
    }

    /// <summary>Where is the laptop now? Asked once at the start and, while it is silent at its address, every
    /// few seconds. First this Wi-Fi (the direct way, also when it got a new address there); if it is not here,
    /// the address it posted for other networks (RemoteAddress), at the start only if that is how we reached it
    /// last time. A different laptop is left alone: this headset has no token for it.</summary>
    void Refind()
    {
        if (locating || video.Laptop == null || step != Step.Typing) return;
        bool far = video.Laptop.Gateway.StartsWith("wss://");
        if (!startChecked)
        {
            startChecked = true;
            StartCoroutine(Locate(far));
            return;
        }
        // 401: the pad's business. 502 from the laptop's own door: it is there, Reachy Mini Control is not; from a
        // public address it can also be a tunnel that is gone.
        long answer = control.LaptopAnswer;
        bool lost = control.DaemonState == "unreachable" && answer != 401 && (answer != 502 || far);
        if (!lost || Time.unscaledTime < nextRefind) return;
        nextRefind = Time.unscaledTime + RefindEveryS;
        bool lookUp = Time.unscaledTime >= nextLookUp;
        if (lookUp) nextLookUp = Time.unscaledTime + LookUpEveryS;
        StartCoroutine(Locate(lookUp));
    }

    IEnumerator Locate(bool lookUp)
    {
        locating = true;
        var search = Task.Run(() => LanSearch.Find(SearchMs));
        while (!search.IsCompleted) yield return null;
        if (!search.IsFaulted && video.Laptop != null && video.LaptopId.Length > 0)
        {
            foreach (var laptop in search.Result)
            {
                if (laptop.Id != video.LaptopId) continue;
                if (laptop.Gateway != video.Laptop.Gateway)
                {
                    Debug.Log($"[pairing] the laptop is on this Wi-Fi at {laptop.Gateway} (was {video.Laptop.Gateway})");
                    video.SetLaptop(laptop.Gateway, null, laptop.Id);
                }
                locating = false;
                yield break;
            }
        }
        if (lookUp && video.Laptop != null)
        {
            using var r = UnityWebRequest.Get(video.AddressBoardUrl);
            r.timeout = 6;
            yield return r.SendWebRequest();
            string url = r.result == UnityWebRequest.Result.Success ? Posted(r.downloadHandler.text) : null;
            if (r.result != UnityWebRequest.Result.Success) Debug.Log($"[pairing] address board: {r.responseCode} {r.error}");
            else if (url == null) Debug.Log("[pairing] no address posted for this headset (laptop off, or not open to other networks)");
            else if (video.Laptop != null && url != video.Laptop.Gateway)
            {
                Debug.Log($"[pairing] the laptop is on another network at {url} (was {video.Laptop.Gateway})");
                video.SetLaptop(url, null, video.LaptopId);
            }
        }
        locating = false;
    }

    [Serializable] class BoardLine { public string message; }

    /// <summary>The board's answer is one JSON object per line; the last notice that is really ours counts.</summary>
    string Posted(string lines)
    {
        string url = null;
        foreach (var line in lines.Split('\n'))
        {
            if (line.Trim().Length == 0) continue;
            try { url = video.ReadNotice(JsonUtility.FromJson<BoardLine>(line)?.message) ?? url; }
            catch (ArgumentException) { }   // not JSON
        }
        return url;
    }
}
