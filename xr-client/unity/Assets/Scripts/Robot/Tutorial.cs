// Short guided tour on the first start: a card in front of you, one control or feature per card, with a small animated pinch
// for the hand it is about. Left pinch = next card (RobotControl). Shown again from "Help" in the QuickMenu.
// German if the headset is set to German, else English. Reachy keeps following your head meanwhile.

using UnityEngine;

public class Tutorial : MonoBehaviour
{
    const string DoneKey = "tutorial";
    const float Distance = 1.5f;         // m in front of the eye
    const float FollowDeg = 18f;         // the card moves back in front once the gaze is further away than this
    const float FollowRate = 4f;         // 1/s
    const float TextScale = 0.7f, TitleScale = 0.95f;
    const int BodyChars = 42;
    const float Pad = 0.06f, StripH = 0.012f, Dot = 0.018f;
    static readonly Color Panel = new Color(0.06f, 0.09f, 0.16f, 0.94f);
    static readonly Color Accent = new Color(0.22f, 0.74f, 0.97f);          // #38bdf8, left hand
    static readonly Color Right = new Color(1f, 0.58f, 0f);                 // #ff9500, right hand
    static readonly Color DotOff = new Color(1, 1, 1, 0.25f);

    enum Hand { None, Left, Right }

    struct Card
    {
        public string Title, Body;
        public Hand Hand;
        public Card(string title, string body, Hand hand) { Title = title; Body = body; Hand = hand; }
    }

    public static bool German => Application.systemLanguage == SystemLanguage.German;

    static Card[] Cards() => German
        ? new[]
        {
            new Card("Willkommen bei Reachy Meetings",
                "Du siehst durch Reachys Kamera und sitzt mit am Tisch. Dreh einfach den Kopf: Reachy schaut mit.", Hand.None),
            new Card("Sprechen",
                "Rechte Hand: Daumen und Zeigefinger zusammen und halten, solange du sprichst, wie bei einem Walkie-Talkie. Loslassen schaltet das Mikrofon aus.", Hand.Right),
            new Card("Übersetzung",
                "Was im Raum gesagt wird, erscheint als Sprechblase über der Person, in deiner Sprache. Mit «Translate me» sagt Reachy deine Sätze übersetzt in der Sprache des Raums.", Hand.None),
            new Card("Meeting-Notizen",
                "Schau nach rechts: Dort steht laufend, worum es geht, wer was bis wann erledigt und was als Nächstes ansteht. Sie werden jede Minute neu geschrieben.", Hand.None),
            new Card("Einstellungen",
                "Schau unter das Video: Dort erscheint eine kleine Leiste. Eintrag anschauen und mit der linken Hand pinchen, um ihn umzuschalten.", Hand.Left),
            new Card("Geradeaus neu setzen",
                "Linke Hand pinchen, ohne auf die Leiste zu schauen: Deine Blickrichtung wird zu «geradeaus». Diese Anleitung findest du jederzeit unter «Hilfe» in der Leiste.", Hand.Left),
        }
        : new[]
        {
            new Card("Welcome to Reachy Meetings",
                "You see through Reachy's camera and sit at the table. Just turn your head: Reachy looks along.", Hand.None),
            new Card("Talking",
                "Right hand: touch thumb and index finger and hold while you talk, like a walkie-talkie. Let go and the mic is off.", Hand.Right),
            new Card("Translation",
                "What is said in the room appears as a speech bubble above the person, in your language. With \"Translate me\", Reachy says your sentences in the room's language.", Hand.None),
            new Card("Meeting notes",
                "Look to your right: a live summary of what is being discussed, who does what by when, and what comes next. Rewritten every minute.", Hand.None),
            new Card("Settings",
                "Look below the video: a small row appears. Look at an entry and pinch with your left hand to switch it.", Hand.Left),
            new Card("Recenter",
                "Pinch left while not looking at the row: where you look becomes \"straight ahead\". You can open this guide again from \"Help\" in the row.", Hand.Left),
        };

    public Transform head;
    public Material textMaterial, panelMaterial;

    Card[] cards;
    int index = -1;
    Transform root;
    TextMesh title, body, footer;
    Renderer titleR, bodyR, footerR;
    Material text;
    Font font;
    Part panel, strip, thumb, finger;
    Part[] dots;
    bool following;
    float width, height;

    struct Part
    {
        public Transform T;
        public Material M;
        public void Set(float w, float h, float r, Vector3 at, Color c)
        {
            T.localScale = new Vector3(w, h, 1);
            T.localPosition = at;
            M.SetVector("_Rect", new Vector4(w, h, r, 0));
            M.color = c;
        }
    }

    public bool Open => index >= 0;

    void Awake()
    {
        font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
        text = new Material(textMaterial) { renderQueue = 3023 };
        UpdateAtlas(font);
        Font.textureRebuilt += UpdateAtlas;
        cards = Cards();

        root = new GameObject("Card").transform;
        root.SetParent(transform, false);
        panel = Quad("Panel", 3020);
        strip = Quad("Strip", 3021);
        thumb = Quad("Thumb", 3022);
        finger = Quad("Finger", 3022);
        dots = new Part[cards.Length];
        for (int i = 0; i < dots.Length; i++) dots[i] = Quad($"Dot{i}", 3021);
        (title, titleR) = Label("Title", 64, TitleScale, FontStyle.Bold);
        (body, bodyR) = Label("Body", 52, TextScale, FontStyle.Normal);
        (footer, footerR) = Label("Footer", 44, TextScale, FontStyle.Normal);
        root.gameObject.SetActive(false);

        if (PlayerPrefs.GetString(DoneKey, "") != "done") Show();
    }

    void OnDestroy() => Font.textureRebuilt -= UpdateAtlas;

    void UpdateAtlas(Font f)
    {
        if (f == font) text.mainTexture = font.material.mainTexture;
    }

    Part Quad(string name, int queue)
    {
        var go = GameObject.CreatePrimitive(PrimitiveType.Quad);
        go.name = name;
        Destroy(go.GetComponent<Collider>());
        go.transform.SetParent(root, false);
        var m = new Material(panelMaterial) { renderQueue = queue };
        go.GetComponent<Renderer>().sharedMaterial = m;
        return new Part { T = go.transform, M = m };
    }

    (TextMesh, Renderer) Label(string name, int size, float scale, FontStyle style)
    {
        var go = new GameObject(name);
        go.transform.SetParent(root, false);
        go.transform.localScale = Vector3.one * scale;
        var t = go.AddComponent<TextMesh>();
        t.font = font;
        t.fontSize = size;
        t.fontStyle = style;
        t.characterSize = 0.01f;
        t.anchor = TextAnchor.UpperLeft;
        t.alignment = TextAlignment.Left;
        t.richText = true;
        t.color = Color.white;
        var r = go.GetComponent<MeshRenderer>();
        r.sharedMaterial = text;
        return (t, r);
    }

    /// <summary>From the first card, in front of you.</summary>
    public void Show()
    {
        index = 0;
        root.gameObject.SetActive(true);
        var f = head.forward;
        root.position = head.position + f * Distance;
        root.rotation = Quaternion.LookRotation(f, Vector3.up);
        Redraw();
        Debug.Log("[tutorial] shown");
    }

    /// <summary>Left pinch: next card, after the last one closed for good.</summary>
    public void Next()
    {
        if (!Open) return;
        index++;
        if (index >= cards.Length)
        {
            index = -1;
            root.gameObject.SetActive(false);
            PlayerPrefs.SetString(DoneKey, "done");
            PlayerPrefs.Save();
            Debug.Log("[tutorial] done");
            return;
        }
        Redraw();
    }

    void Update()
    {
        if (!Open) return;
        // stays put while you read, comes back in front when you look away
        var want = head.position + head.forward * Distance;
        if (Vector3.Angle(head.forward, root.position - head.position) > FollowDeg) following = true;
        if (following)
        {
            float k = 1 - Mathf.Exp(-FollowRate * Time.unscaledDeltaTime);
            root.position = Vector3.Lerp(root.position, want, k);
            root.rotation = Quaternion.Slerp(root.rotation, Quaternion.LookRotation(root.position - head.position, Vector3.up), k);
            if ((root.position - want).sqrMagnitude < 0.0004f) following = false;
        }
        AnimatePinch();
    }

    void Redraw()
    {
        var c = cards[index];
        title.text = CaptionView.Escape(c.Title);
        body.text = "<color=#cbd5e1>" + string.Join("\n", CaptionView.Wrap(CaptionView.Escape(c.Body), BodyChars, 8)) + "</color>";
        bool last = index == cards.Length - 1;
        var next = German ? (last ? "Los geht's" : "weiter") : (last ? "Let's go" : "next");
        footer.text = $"<color=#38bdf8><b>{(German ? "Links pinchen" : "Pinch left")}</b></color><color=#94a3b8>  →  {next}</color>";

        var tb = titleR.localBounds.size * TitleScale;
        var bb = bodyR.localBounds.size * TextScale;
        var fb = footerR.localBounds.size * TextScale;
        float illus = c.Hand == Hand.None ? 0 : 0.16f;
        width = Mathf.Max(0.7f, Mathf.Max(tb.x + illus, bb.x, fb.x) + 2 * Pad);
        float gap = 0.035f, dotsH = Dot + gap;
        height = Pad + dotsH + tb.y + gap + bb.y + gap * 1.5f + fb.y + Pad;

        float left = -width / 2 + Pad, y = height / 2 - Pad;
        panel.Set(width, height, 0.04f, Vector3.zero, Panel);
        strip.Set(width - 0.08f, StripH, StripH / 2, new Vector3(0, height / 2 - StripH / 2 - 0.004f, -0.001f),
            c.Hand == Hand.Right ? Right : Accent);
        float x = left;
        for (int i = 0; i < dots.Length; i++)   // progress: the current card is a wider pill
        {
            float w = i == index ? Dot * 2.4f : Dot;
            dots[i].Set(w, Dot, Dot / 2, new Vector3(x + w / 2, y - Dot / 2 - 0.012f, -0.001f), i == index ? Color.white : DotOff);
            x += w + Dot * 0.8f;
        }
        y -= dotsH + 0.012f;
        title.transform.localPosition = new Vector3(left, y, -0.002f);
        y -= tb.y + gap;
        body.transform.localPosition = new Vector3(left, y, -0.002f);
        y -= bb.y + gap * 1.5f;
        footer.transform.localPosition = new Vector3(left, y, -0.002f);

        thumb.T.gameObject.SetActive(c.Hand != Hand.None);
        finger.T.gameObject.SetActive(c.Hand != Hand.None);
    }

    /// <summary>Two fingertips that meet and part, top right on cards about a hand.</summary>
    void AnimatePinch()
    {
        var c = cards[index];
        if (c.Hand == Hand.None) return;
        var col = c.Hand == Hand.Right ? Right : Accent;
        float open = 0.5f + 0.5f * Mathf.Sin(Time.unscaledTime * 3.5f);
        float gap = 0.004f + 0.05f * open, r = 0.024f;
        var centre = new Vector3(width / 2 - Pad - 0.05f, height / 2 - Pad - 0.07f, -0.002f);
        thumb.Set(r * 1.15f, r * 1.15f, r * 0.575f, centre + new Vector3(-0.012f, -(gap + r) / 2, 0), col);
        finger.Set(r, r, r / 2, centre + new Vector3(0.012f, (gap + r) / 2, 0),
            new Color(col.r, col.g, col.b, 0.75f + 0.25f * (1 - open)));
    }
}
