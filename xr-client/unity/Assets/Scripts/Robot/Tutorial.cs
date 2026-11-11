// Interactive guided tour on the first start. Reachy stays still meanwhile (RobotControl: no wake-up, no following,
// mic to nobody): you practise each gesture and the card tells you when it worked. The two dots on the card are your
// real thumb and index finger (pinch strength from the hand tracking), so you see the pinch being recognised.
// Cards: welcome, talking (hold right), straight ahead (pinch left), menu + language (look + pinch left), translation,
// meeting notes (look right), ready. Opened again from Help in the menu. German if the headset is German, else English.

using UnityEngine;

public class Tutorial : MonoBehaviour
{
    const string DoneKey = "tutorial";
    const float Distance = 1.5f;         // m in front of the eye
    const float FollowDeg = 18f;         // the card glides back in front once the gaze is further away than this
    const float FollowRate = 4f;         // 1/s
    const float BodyScale = 0.7f, TitleScale = 0.95f;
    const int BodyChars = 42;
    const float Pad = 0.06f, StripH = 0.012f, Dot = 0.018f, Illus = 0.14f;
    const float HoldS = 1.5f, NotesDeg = 20f;
    static readonly Color Panel = new Color(0.08f, 0.11f, 0.17f, 0.97f);
    static readonly Color Left = new Color(0.22f, 0.74f, 0.97f);           // #38bdf8
    static readonly Color Right = new Color(1f, 0.58f, 0f);                // #ff9500
    static readonly Color Good = new Color(0.29f, 0.87f, 0.5f);            // #4ade80
    static readonly Color DotOff = new Color(1, 1, 1, 0.25f);

    enum Task { Start, HoldRight, Recenter, PickLanguage, Info, LookNotes, Finish }
    enum Hand { None, Left, Right }

    class Card
    {
        public Task Task;
        public Hand Hand;
        public string Title, Body, Footer;
        public float DoneS = 1.6f;   // how long the "well done" stays before the next card
        public bool Stay;            // the card stays put: the task is to look somewhere else
    }

    public RobotControl control;
    public QuickMenu menu;
    public NotesPanel notes;
    public Transform head;
    public Material textMaterial, panelMaterial;

    Card[] cards;
    int index = -1;
    Transform root;
    TextMesh title, body, hint, footer;
    Ui.Rect panel, strip, thumb, finger, skipBtn, nextBtn, hintBox, fingerBack;
    TextMesh skipText, nextText, stepText;
    Vector3 skipAt, nextAt;
    readonly bool[] onSkip = new bool[2], onNext = new bool[2];   // per hand: left, right
    readonly Ui.Cursor[] cursors = new Ui.Cursor[2];
    const float BtnW = 0.3f, BtnH = 0.075f;
    Ui.Rect[] dots;
    bool following;
    float width, height;
    string hintText;
    bool done;
    float doneAt, heldSince = -1f;
    bool wasHolding;
    int picksAtStart;

    public bool Open => index >= 0;
    /// <summary>This card's task is the held left pinch (RobotControl counts the time).</summary>
    public bool WantsHold => Open && cards[index].Task == Task.Recenter;
    static bool De => Ui.German;

    void Awake()
    {
        cards = Cards();
        var text = Ui.TextMaterial(textMaterial, 3043);
        root = new GameObject("Card").transform;
        root.SetParent(transform, false);
        panel = Ui.Quad(root, "Panel", panelMaterial, 3040);
        strip = Ui.Quad(root, "Strip", panelMaterial, 3041);
        thumb = Ui.Quad(root, "Thumb", panelMaterial, 3042);
        finger = Ui.Quad(root, "Finger", panelMaterial, 3042);
        dots = new Ui.Rect[cards.Length];
        for (int i = 0; i < dots.Length; i++) dots[i] = Ui.Quad(root, $"Dot{i}", panelMaterial, 3041);
        title = Ui.Text(root, "Title", text, 64, TitleScale, TextAnchor.UpperLeft, FontStyle.Bold);
        body = Ui.Text(root, "Body", text, 52, BodyScale, TextAnchor.UpperLeft);
        hint = Ui.Text(root, "Hint", text, 52, BodyScale, TextAnchor.UpperLeft, FontStyle.Bold);
        footer = Ui.Text(root, "Footer", text, 46, BodyScale, TextAnchor.UpperLeft);
        hintBox = Ui.Quad(root, "HintBox", panelMaterial, 3041);
        fingerBack = Ui.Quad(root, "FingerBack", panelMaterial, 3041);
        stepText = Ui.Text(root, "Step", text, 40, BodyScale, TextAnchor.MiddleRight);
        skipBtn = Ui.Quad(root, "Skip", panelMaterial, 3041);
        nextBtn = Ui.Quad(root, "Next", panelMaterial, 3041);
        skipText = Ui.Text(root, "SkipText", text, 44, BodyScale);
        nextText = Ui.Text(root, "NextText", text, 44, BodyScale, TextAnchor.MiddleCenter, FontStyle.Bold);
        for (int i = 0; i < 2; i++) cursors[i] = new Ui.Cursor(root, panelMaterial);
        root.gameObject.SetActive(false);
    }

    void Start()
    {
        if (PlayerPrefs.GetString(DoneKey, "") != "done") Show();
    }

    static Card[] Cards() => De
        ? new[]
        {
            new Card { Task = Task.Start, Hand = Hand.Left, Title = "Willkommen bei Reachy Meetings",
                Body = "Du sitzt gleich durch Reachy mit am Tisch. Bedient wird alles mit einer Geste: Daumen und Zeigefinger zusammen, ein «Pinch». Reachy macht sich währenddessen bereit.",
                Footer = "Auf «Weiter» zeigen und pinchen" },
            new Card { Task = Task.HoldRight, Hand = Hand.Right, Title = "Sprechen",
                Body = "Rechte Hand: Daumen und Zeigefinger zusammen und halten, wie bei einem Walkie-Talkie. Probier es jetzt.",
                Footer = "Rechts pinchen und halten" },
            new Card { Task = Task.Recenter, Hand = Hand.Left, Title = "Geradeaus festlegen",
                Body = "Schau dorthin, wo für dich «geradeaus» sein soll, und halte den linken Pinch etwa eine Sekunde. Das geht später jederzeit wieder.",
                Footer = "Links pinchen und halten" },
            new Card { Task = Task.PickLanguage, Hand = Hand.Left, Stay = true, DoneS = 2.5f, Title = "Menü und Sprache",
                Body = "Unten am Bild ist das Menü. Zeig mit einer Hand auf «Sprache»: Ein Punkt zeigt, wohin du zeigst, und sein Ring schliesst sich beim Pinchen. Ist «Sprache» gelb umrandet, pinche mit dieser Hand.",
                Footer = "Auf «Sprache» zeigen, pinchen" },
            new Card { Task = Task.Info, Hand = Hand.Left, Title = "Übersetzung",
                Body = "Was im Raum gesagt wird, erscheint als Sprechblase über der Person, in deiner Sprache. Ist «Translate me» an (im Menü), sagt Reachy deine Sätze übersetzt in der Sprache des Raums. Ist es aus, hört man deine eigene Stimme.",
                Footer = "«Weiter»" },
            new Card { Task = Task.LookNotes, Hand = Hand.None, Stay = true, DoneS = 4f, Title = "Meeting-Notizen",
                Body = "Schau nach rechts: Dort stehen laufend die Zusammenfassung, wer was bis wann erledigt, und die nächsten Schritte.",
                Footer = "Nach rechts schauen" },
            new Card { Task = Task.Finish, Hand = Hand.Left, Title = "Bereit",
                Body = "Das war's. Diese Anleitung findest du jederzeit unter «Hilfe» im Menü.",
                Footer = "«Fertig»: los geht's" },
        }
        : new[]
        {
            new Card { Task = Task.Start, Hand = Hand.Left, Title = "Welcome to Reachy Meetings",
                Body = "You are about to sit at the table through Reachy. Everything works with one gesture: thumb and index finger together, a \"pinch\". Reachy gets ready meanwhile.",
                Footer = "Point at \"Next\" and pinch" },
            new Card { Task = Task.HoldRight, Hand = Hand.Right, Title = "Talking",
                Body = "Right hand: touch thumb and index finger and hold, like a walkie-talkie. Try it now.",
                Footer = "Pinch right and hold" },
            new Card { Task = Task.Recenter, Hand = Hand.Left, Title = "Set straight ahead",
                Body = "Look where \"straight ahead\" should be for you and hold the left pinch for about a second. You can do this again any time.",
                Footer = "Pinch left and hold" },
            new Card { Task = Task.PickLanguage, Hand = Hand.Left, Stay = true, DoneS = 2.5f, Title = "Menu and language",
                Body = "The menu is at the foot of the picture. Point at \"Language\" with one hand: a dot shows where you point, and its ring closes as you pinch. Once \"Language\" has a yellow frame, pinch with that hand.",
                Footer = "Point at \"Language\", pinch" },
            new Card { Task = Task.Info, Hand = Hand.Left, Title = "Translation",
                Body = "What is said in the room appears as a speech bubble above the person, in your language. With \"Translate me\" on (in the menu), Reachy says your sentences in the room's language. With it off, they hear your own voice.",
                Footer = "\"Next\"" },
            new Card { Task = Task.LookNotes, Hand = Hand.None, Stay = true, DoneS = 4f, Title = "Meeting notes",
                Body = "Look to your right: a live summary, who does what by when, and the next steps.",
                Footer = "Look to your right" },
            new Card { Task = Task.Finish, Hand = Hand.Left, Title = "Ready",
                Body = "That's it. You find this guide again under \"Help\" in the menu.",
                Footer = "\"Done\": off you go" },
        };

    /// <summary>From the first card, in front of you. Reachy pauses until the end.</summary>
    public void Show()
    {
        root.gameObject.SetActive(true);
        root.position = head.position + head.forward * Distance;
        root.rotation = Quaternion.LookRotation(head.forward, Vector3.up);
        Go(0);
        Debug.Log("[tutorial] shown");
    }

    void Go(int i)
    {
        if (i >= cards.Length) { Close(); return; }
        index = i;
        done = false;
        hintText = null;
        heldSince = -1f;
        wasHolding = false;
        picksAtStart = menu != null ? menu.LanguagePicks : 0;
        Redraw();
    }

    void Close()
    {
        index = -1;
        root.gameObject.SetActive(false);
        PlayerPrefs.SetString(DoneKey, "done");
        PlayerPrefs.Save();
        Debug.Log("[tutorial] done");
    }

    void Succeed(string message)
    {
        done = true;
        doneAt = Time.unscaledTime;
        hintText = message;
        Redraw();
        Debug.Log($"[tutorial] card {index + 1} done");
    }

    void Hint(string message)
    {
        if (hintText == message) return;
        hintText = message;
        Redraw();
    }

    /// <summary>That hand points at Skip or Next: its pinch goes here (RobotControl).</summary>
    public bool Hovered(bool right) => Open && (onSkip[right ? 1 : 0] || onNext[right ? 1 : 0]);

    public void Press(bool right)
    {
        int hand = right ? 1 : 0;
        if (onSkip[hand]) { Debug.Log("[tutorial] skipped"); Close(); }
        else if (onNext[hand]) Go(index + 1);
    }

    /// <summary>Left pinch, when not on the menu (RobotControl).</summary>
    public void LeftPinch()
    {
        if (!Open || done) return;
        switch (cards[index].Task)
        {
            case Task.Start:
            case Task.Info:
                Go(index + 1);
                break;
            case Task.Recenter:
                control.Recenter();
                Succeed(De ? "Gesetzt: Das ist jetzt geradeaus." : "Set: this is straight ahead now.");
                break;
            case Task.PickLanguage:
                Hint(De ? "Zeig dazu auf das Menü unten am Bild." : "Point at the menu at the foot of the picture for this.");
                break;
            case Task.Finish:
                Close();
                break;
        }
    }

    void Update()
    {
        if (!Open) return;
        var c = cards[index];
        if (done)
        {
            if (Time.unscaledTime - doneAt > c.DoneS) { Go(index + 1); if (!Open) return; c = cards[index]; }
        }
        else Check(c);

        var want = head.position + head.forward * Distance;
        if (!c.Stay && Vector3.Angle(head.forward, root.position - head.position) > FollowDeg) following = true;
        if (following)
        {
            float k = 1 - Mathf.Exp(-FollowRate * Time.unscaledDeltaTime);
            root.position = Vector3.Lerp(root.position, want, k);
            root.rotation = Quaternion.Slerp(root.rotation, Quaternion.LookRotation(root.position - head.position, Vector3.up), k);
            if ((root.position - want).sqrMagnitude < 0.0004f) following = false;
        }
        DrawFingers(c);

        // Skip and Next, picked with either hand's ray (or the gaze) and a pinch of that hand
        for (int hand = 0; hand < 2; hand++)
        {
            bool right = hand == 1;
            var aim = XrInput.Aim(right, head);
            var p = Ui.Pick(root, aim, head, q => Ui.Inside(q, skipAt, BtnW + 0.04f, BtnH + 0.04f)
                || Ui.Inside(q, nextAt, BtnW + 0.04f, BtnH + 0.04f), out var byHand);
            onSkip[hand] = Ui.Inside(p, skipAt, BtnW + 0.04f, BtnH + 0.04f);
            onNext[hand] = Ui.Inside(p, nextAt, BtnW + 0.04f, BtnH + 0.04f);
            if (aim != null && Ui.Inside(byHand, Vector3.zero, width + 0.1f, height + 0.1f))
                cursors[hand].Show(byHand.Value, right ? control.RightLevel : control.LeftLevel);
            else cursors[hand].Hide();
        }
        skipBtn.Set(BtnW, BtnH, BtnH / 2, skipAt, new Color(1, 1, 1, onSkip[0] || onSkip[1] ? 0.22f : 0f));
        skipBtn.Border(new Color(1, 1, 1, 0.3f), 0.003f);
        var accent = new Color(Ui.Accent.r, Ui.Accent.g, Ui.Accent.b, onNext[0] || onNext[1] ? 1f : 0.85f);
        nextBtn.Set(BtnW, BtnH, BtnH / 2, nextAt, accent);
    }

    /// <summary>Watches the task of the current card.</summary>
    void Check(Card c)
    {
        switch (c.Task)
        {
            case Task.HoldRight:
                bool holding = control.RightLevel > 0.8f || wasHolding && control.RightLevel > 0.5f;
                if (holding && heldSince < 0) heldSince = Time.unscaledTime;
                float held = heldSince < 0 ? 0 : Time.unscaledTime - heldSince;
                if (holding)
                    Hint(held < HoldS
                        ? De ? "Gut! Die Finger berühren sich: Jetzt würde man dich hören. (Übung, das Mikro ist noch aus.)"
                             : "Good! Your fingers touch: now they would hear you. (Practice, the mic is still off.)"
                        : De ? "Und jetzt loslassen: Mikro aus." : "Now let go: mic off.");
                else if (wasHolding)
                {
                    if (held >= HoldS) Succeed(De ? "Genau so. Solange du hältst, hört man dich." : "Exactly. While you hold, they hear you.");
                    else Hint(De ? "Etwas länger halten, so lange du sprichst." : "Hold a bit longer, as long as you talk.");
                    heldSince = -1f;
                }
                wasHolding = holding;
                break;
            case Task.PickLanguage:
                if (menu.LanguagePicks > picksAtStart)
                {
                    int i = System.Array.IndexOf(QuickMenu.Langs, menu.Target);
                    var name = i >= 0 ? QuickMenu.LangNames[i] : menu.Target;
                    Succeed(De ? $"Gut! Sprechblasen und Notizen kommen jetzt auf {name}." : $"Good! Bubbles and notes now come in {name}.");
                }
                else if (menu.ListOpen) Hint(De ? "Zeig auf deine Sprache und pinche." : "Point at your language and pinch.");
                else if (menu.LanguageGazed) Hint(De ? "Genau, jetzt pinchen." : "That's it, now pinch.");
                break;
            case Task.LookNotes:
                if (notes != null && Vector3.Angle(XrInput.Gaze(head).direction, notes.Card.position - head.position) < NotesDeg)
                    Succeed(De ? "Hier sind sie. Am Griff unten kannst du sie verschieben: darauf zeigen, pinchen, halten und die Hand bewegen."
                               : "Here they are. Move them by the handle below: point at it, pinch, hold and move your hand.");
                break;
        }
    }

    void Redraw()
    {
        var c = cards[index];
        title.text = CaptionView.Escape(c.Title);
        body.text = "<color=#cbd5e1>" + Wrap(c.Body) + "</color>";
        var good = done ? "#4ade80" : "#facc15";
        hint.text = hintText == null ? "" : $"<color={good}>{Wrap(hintText)}</color>";
        var line = c.Task == Task.Finish && control.IsFollowing ? (De ? "«Fertig»: weiter mit Reachy" : "\"Done\": back to Reachy") : c.Footer;
        var col = c.Hand == Hand.Right ? "#ff9500" : "#38bdf8";
        footer.text = done ? "" : $"<color={col}><b>{CaptionView.Escape(line)}</b></color>";

        var tb = Ui.Size(title);
        var bb = Ui.Size(body);
        var hb = hintText == null ? Vector3.zero : Ui.Size(hint);
        var fb = done ? Vector3.zero : Ui.Size(footer);
        float illus = c.Hand == Hand.None ? 0 : Illus;
        width = Mathf.Max(0.8f, Mathf.Max(tb.x + illus, Mathf.Max(bb.x, Mathf.Max(hb.x, fb.x))) + 2 * Pad);
        float gap = 0.03f, dotsH = Dot + gap;
        height = Pad + dotsH + tb.y + gap + bb.y + (hintText == null ? 0 : gap + hb.y) + (done ? 0 : gap * 1.5f + fb.y)
                 + (hintText == null ? 0 : 0.04f) + gap * 2 + BtnH + Pad;
        skipAt = new Vector3(-width / 2 + Pad + BtnW / 2, -height / 2 + Pad + BtnH / 2, -0.001f);
        nextAt = new Vector3(width / 2 - Pad - BtnW / 2, -height / 2 + Pad + BtnH / 2, -0.001f);
        skipText.text = $"<color=#cbd5e1>{(De ? "Überspringen" : "Skip")}</color>";
        nextText.text = $"<color={Ui.InkHex}>{(c.Task == Task.Finish ? (De ? "Fertig" : "Done") : (De ? "Weiter" : "Next"))}</color>";
        skipText.transform.localPosition = skipAt + new Vector3(0, 0, -0.002f);
        nextText.transform.localPosition = nextAt + new Vector3(0, 0, -0.002f);

        float left = -width / 2 + Pad, y = height / 2 - Pad;
        panel.Set(width, height, 0.07f, Vector3.zero, Panel);
        panel.Border(Ui.Edge);
        strip.Show(false);   // the look of 2026-11-11 has no bar at the top: the dots say where you are
        stepText.text = $"<color={Ui.SoftHex}>{index + 1} {(De ? "von" : "of")} {cards.Length}</color>";
        stepText.transform.localPosition = new Vector3(width / 2 - Pad, y - Dot / 2 - 0.012f, -0.002f);
        // the hint on a tinted box: amber while it guides, green once the step is done
        var tint = done ? Good : new Color(0.98f, 0.8f, 0.08f);
        hintBox.Show(hintText != null);
        fingerBack.Show(c.Hand != Hand.None);
        if (c.Hand != Hand.None)
        {
            fingerBack.Set(0.16f, 0.16f, 0.08f, new Vector3(width / 2 - Pad - 0.05f, height / 2 - Pad - 0.1f, -0.001f), new Color(1, 1, 1, 0.05f));
            fingerBack.Border(new Color(1, 1, 1, 0.12f), 0.003f);
        }
        float x = left;
        for (int i = 0; i < dots.Length; i++)   // progress: the current card is a wider pill
        {
            float w = i == index ? Dot * 2.4f : Dot;
            dots[i].Set(w, Dot, Dot / 2, new Vector3(x + w / 2, y - Dot / 2 - 0.012f, -0.001f),
                i < index || i == index && done ? Good : i == index ? Color.white : DotOff);
            x += w + Dot * 0.8f;
        }
        y -= dotsH + 0.012f;
        title.transform.localPosition = new Vector3(left, y, -0.002f);
        y -= tb.y + gap;
        body.transform.localPosition = new Vector3(left, y, -0.002f);
        y -= bb.y + gap;
        if (hintText != null)
        {
            y -= 0.02f;
            hintBox.Set(width - 2 * Pad, hb.y + 0.045f, 0.03f, new Vector3(0, y - hb.y / 2, -0.001f), new Color(tint.r, tint.g, tint.b, 0.14f));
            hintBox.Border(new Color(tint.r, tint.g, tint.b, 0.5f), 0.003f);
        }
        hint.transform.localPosition = new Vector3(left + 0.02f, y, -0.002f);
        if (hintText != null) y -= hb.y + 0.02f + gap * 1.5f;
        footer.transform.localPosition = new Vector3(left, y, -0.002f);

        thumb.Show(c.Hand != Hand.None);
        finger.Show(c.Hand != Hand.None);
    }

    static string Wrap(string s) => string.Join("\n", CaptionView.Wrap(CaptionView.Escape(s), BodyChars, int.MaxValue));

    /// <summary>Thumb and index finger, top right: their gap is your real pinch, they light up once it counts.</summary>
    void DrawFingers(Card c)
    {
        if (c.Hand == Hand.None) return;
        float level = c.Hand == Hand.Right ? control.RightLevel : control.LeftLevel;
        var col = c.Hand == Hand.Right ? Right : Left;
        if (level > 0.8f) col = Color.Lerp(col, Color.white, 0.35f);
        float gap = 0.004f + 0.05f * (1 - Mathf.Clamp01(level)), r = 0.024f;
        var centre = new Vector3(width / 2 - Pad - 0.05f, height / 2 - Pad - 0.1f, -0.002f);
        thumb.Set(r * 1.15f, r * 1.15f, r * 0.575f, centre + new Vector3(-0.012f, -(gap + r) / 2, 0), col);
        finger.Set(r, r, r / 2, centre + new Vector3(0.012f, (gap + r) / 2, 0), new Color(col.r, col.g, col.b, 0.6f + 0.4f * level));
    }
}
