// The app's few settings, as a column of tiles at the lower left, always visible. It stays still in the room
// (relative to straight ahead, like the notes), not on the video window, which moves whenever Reachy turns. Look at a
// tile with either hand (a dot shows where it points, its ring closes as you pinch, like the headset's own menus) and
// pinch with that hand (RobotControl); without hand rays the eyes pick. The tile pointed at gets a yellow frame:
//   Translate me  on / off: your voice goes to the backend, Reachy says it in the room's language (MicSender)
//   Language      opens a short list next to it: the language the room is translated into (bubbles and notes);
//                 starts as the headset's system language
//   Help          the guided tour again (Tutorial)
// Under the column, for a few seconds: what Reachy said for you (the backend's "me" message) or a short note
// (new meeting notes). Settings are remembered on the headset and sent again on every reconnect.

using UnityEngine;

public class QuickMenu : MonoBehaviour
{
    [System.Serializable] public class MeMsg { public string text, lang, translation, target; }

    const string TranslateKey = "translate", TargetKey = "target";
    public static readonly string[] Langs = { "de", "en", "fr", "it", "es" };
    public static readonly string[] LangNames = { "Deutsch", "English", "Français", "Italiano", "Español" };

    // Left of and below straight ahead, 1.5 m away, three quarters size: in view without turning the head much.
    // On the video window at 49°, 37° and 26° it was too far out or moved away when Reachy turned.
    const float SideDeg = -28f, DownDeg = 14f, Distance = 1.5f, Scale = 0.75f;
    const float HitPad = 0.04f, StickyS = 0.3f;   // larger hit area, and the gaze stays on a tile briefly after leaving
    const float TileW = 0.27f, TileH = 0.19f, Gap = 0.022f, Pad = 0.026f, Frame = 0.008f;
    const float RowW = 0.3f, RowH = 0.085f;
    const float ListCloseS = 3f, MeS = 6f, FlashS = 4f;
    const int MeChars = 30, MeLines = 4;
    static readonly Color Back = new Color(0.07f, 0.08f, 0.11f, 0.9f);
    static readonly Color Tile = new Color(1, 1, 1, 0.08f);
    static readonly Color On = new Color(0.22f, 0.74f, 0.97f, 0.9f);       // #38bdf8
    static readonly Color Gaze = new Color(0.98f, 0.8f, 0.08f);            // #facc15

    public RoomSound room;
    public RobotControl control;
    public Transform head;
    public Material textMaterial, panelMaterial;
    public Tutorial tutorial;

    enum Item { None = -1, Translate, Language, Help }

    Material text;
    Ui.Rect back, listBack;
    readonly Ui.Rect[] tiles = new Ui.Rect[3], frames = new Ui.Rect[3];
    readonly TextMesh[] labels = new TextMesh[3], values = new TextMesh[3];
    readonly Ui.Rect[] rows = new Ui.Rect[5];
    readonly TextMesh[] rowText = new TextMesh[5];
    TextMesh me;
    Vector3[] tileAt, rowAt;
    class Hover { public Item Item = Item.None; public int Row = -1; public float ItemAt, RowAt; }
    readonly Hover[] hover = { new Hover(), new Hover() };   // left, right hand
    readonly Ui.Cursor[] cursors = new Ui.Cursor[2];
    float h;
    bool listOpen;
    float listLookedAt;
    int sentConnects = -1;
    float meUntil;
    string meetingLang;   // what Reachy speaks for you, known from the first "me" message

    public bool Translate { get; private set; }
    public string Target { get; private set; }
    /// <summary>That hand points at a tile or a language: its pinch goes here (RobotControl).</summary>
    public bool Hovered(bool right) => hover[right ? 1 : 0].Item != Item.None || hover[right ? 1 : 0].Row >= 0;
    /// <summary>For the tutorial: what is pointed at and what happened.</summary>
    public bool LanguageGazed => hover[0].Item == Item.Language || hover[1].Item == Item.Language;
    public bool ListOpen => listOpen;
    public int LanguagePicks { get; private set; }

    void Awake()
    {
        transform.localScale = Vector3.one * Scale;
        Place();
        text = Ui.TextMaterial(textMaterial, 3033);

        Translate = PlayerPrefs.GetInt(TranslateKey, 1) == 1;
        Target = PlayerPrefs.GetString(TargetKey, SystemLang());

        h = 3 * TileH + 2 * Gap + 2 * Pad;
        back = Ui.Quad(transform, "Back", panelMaterial, 3030);
        back.Set(TileW + 2 * Pad, h, 0.04f, Vector3.zero, Back);
        tileAt = new Vector3[3];
        for (int i = 0; i < 3; i++)
        {
            tileAt[i] = new Vector3(0, h / 2 - Pad - TileH / 2 - i * (TileH + Gap), -0.001f);
            frames[i] = Ui.Quad(transform, $"Frame{i}", panelMaterial, 3031);
            frames[i].Set(TileW + 2 * Frame, TileH + 2 * Frame, 0.03f, tileAt[i], Gaze);
            tiles[i] = Ui.Quad(transform, $"Tile{i}", panelMaterial, 3032);
            labels[i] = Ui.Text(transform, $"Label{i}", text, 34, 1f);
            labels[i].transform.localPosition = tileAt[i] + new Vector3(0, 0.05f, -0.002f);
            values[i] = Ui.Text(transform, $"Value{i}", text, 64, 1f, TextAnchor.MiddleCenter, FontStyle.Bold);
            values[i].transform.localPosition = tileAt[i] + new Vector3(0, -0.025f, -0.002f);
        }

        // the language list, right of the Language tile, over the edge of the video
        float listH = rows.Length * RowH + 2 * Pad * 0.6f;
        var listCentre = new Vector3(TileW / 2 + Pad + 0.03f + RowW / 2 + Pad * 0.6f, tileAt[1].y, -0.003f);
        listBack = Ui.Quad(transform, "ListBack", panelMaterial, 3034);
        listBack.Set(RowW + 1.2f * Pad, listH, 0.03f, listCentre, Back);
        rowAt = new Vector3[rows.Length];
        for (int i = 0; i < rows.Length; i++)
        {
            rowAt[i] = listCentre + new Vector3(0, listH / 2 - Pad * 0.6f - RowH / 2 - i * RowH, -0.001f);
            rows[i] = Ui.Quad(transform, $"Row{i}", panelMaterial, 3035);
            rowText[i] = Ui.Text(transform, $"RowText{i}", text, 44, 1f);
            rowText[i].transform.localPosition = rowAt[i] + new Vector3(0, 0, -0.002f);
        }

        me = Ui.Text(transform, "Me", text, 36, 1f, TextAnchor.UpperCenter);
        me.transform.localPosition = new Vector3(0, -h / 2 - 0.04f, -0.002f);
        me.gameObject.SetActive(false);
        for (int i = 0; i < 2; i++) cursors[i] = new Ui.Cursor(transform, panelMaterial);
        ShowList(false);
        Redraw();
    }

    static Quaternion ToUnity(Quat q) => new Quaternion(-(float)q.X, -(float)q.Y, (float)q.Z, (float)q.W);

    /// <summary>Fixed relative to straight ahead (recenter), heading only, facing the eye.</summary>
    void Place()
    {
        float frameYaw = control != null ? ToUnity(control.Frame.ToWorld(Quat.Identity)).eulerAngles.y : 0;
        var rot = Quaternion.Euler(DownDeg, frameYaw + SideDeg, 0);
        transform.localPosition = head.localPosition + rot * Vector3.forward * Distance;
        transform.localRotation = rot;
    }

    /// <summary>Headset language if the backend can translate into it, else English.</summary>
    static string SystemLang() => Application.systemLanguage switch
    {
        SystemLanguage.German => "de",
        SystemLanguage.French => "fr",
        SystemLanguage.Italian => "it",
        SystemLanguage.Spanish => "es",
        _ => "en",
    };

    void Update()
    {
        var backend = room.Backend;
        if (backend != null && backend.Connected && backend.Connects != sentConnects)
        {
            sentConnects = backend.Connects;
            SendLang();
            SendMeMode();
        }

        Place();
        bool onList = false;
        for (int hand = 0; hand < 2; hand++)
        {
            bool right = hand == 1;
            var aim = XrInput.Aim(right, head);
            var p = Ui.PointOn(transform, aim ?? XrInput.Gaze(head));
            var hv = hover[hand];
            var now = Item.None;
            for (int i = 0; i < 3; i++)
                if (Ui.Inside(p, tileAt[i], TileW + 2 * HitPad, TileH + Gap + HitPad)) now = (Item)i;
            if (now != Item.None || Time.unscaledTime - hv.ItemAt > StickyS) hv.Item = now;
            if (now != Item.None) hv.ItemAt = Time.unscaledTime;
            int row = -1;
            if (listOpen)
                for (int i = 0; i < rows.Length; i++)
                    if (Ui.Inside(p, rowAt[i], RowW + 2 * HitPad, RowH)) row = i;
            if (row >= 0 || Time.unscaledTime - hv.RowAt > StickyS) hv.Row = listOpen ? row : -1;
            if (row >= 0) hv.RowAt = Time.unscaledTime;
            onList |= hv.Row >= 0 || hv.Item == Item.Language;

            // the cursor, only for a real hand ray and only on the menu
            bool onMenu = p is Vector3 q && (Ui.Inside(q, Vector3.zero, TileW + 2 * Pad + 0.1f, h + 0.1f)
                                             || listOpen && Ui.Inside(q, rowAt[2], RowW + 0.15f, rows.Length * RowH + 0.1f));
            if (aim != null && onMenu) cursors[hand].Show(p.Value, right ? control.RightLevel : control.LeftLevel);
            else cursors[hand].Hide();
        }
        if (listOpen)
        {
            if (onList) listLookedAt = Time.unscaledTime;
            else if (Time.unscaledTime - listLookedAt > ListCloseS) ShowList(false);
        }
        if (me.gameObject.activeSelf && Time.unscaledTime > meUntil) me.gameObject.SetActive(false);
        Redraw();
    }

    /// <summary>A pinch of the hand that points at a tile or a language.</summary>
    public void Select(bool right)
    {
        var hv = hover[right ? 1 : 0];
        if (hv.Row >= 0)
        {
            Target = Langs[hv.Row];
            PlayerPrefs.SetString(TargetKey, Target);
            PlayerPrefs.Save();
            SendLang();
            ShowList(false);
            LanguagePicks++;
            Debug.Log($"[menu] language {Target}");
            return;
        }
        switch (hv.Item)
        {
            case Item.Translate:
                Translate = !Translate;
                PlayerPrefs.SetInt(TranslateKey, Translate ? 1 : 0);
                PlayerPrefs.Save();
                SendMeMode();
                Debug.Log($"[menu] translate {(Translate ? "on" : "off")}");
                break;
            case Item.Language:
                ShowList(!listOpen);
                break;
            case Item.Help:
                if (tutorial != null && !tutorial.Open) tutorial.Show();
                break;
        }
    }

    void ShowList(bool on)
    {
        listOpen = on;
        listLookedAt = Time.unscaledTime;
        listBack.Show(on);
        foreach (var r in rows) r.Show(on);
        foreach (var t in rowText) t.gameObject.SetActive(on);
    }

    void SendLang()
    {
        if (room.Backend == null) return;
        // what Reachy speaks stays "auto": the language most spoken in the room (backend/README.md)
        room.Backend.Send($"{{\"type\": \"lang\", \"meeting\": \"auto\", \"target\": \"{Target}\"}}");
    }

    /// <summary>Translate me: Reachy says your words; else it plays your voice and the backend only transcribes it.</summary>
    void SendMeMode()
    {
        if (room.Backend == null) return;
        room.Backend.Send($"{{\"type\": \"me_mode\", \"speak\": {(Translate ? "true" : "false")}}}");
    }

    /// <summary>What Reachy just said for you (from CaptionView).</summary>
    public void ShowMe(MeMsg m)
    {
        bool translated = !string.IsNullOrEmpty(m.translation);
        meetingLang = m.target;
        var said = translated ? m.translation : m.text;
        var arrow = translated ? $"{m.lang}→{m.target}" : m.lang;
        me.text = $"<color=#9ca3af>Reachy ({arrow}):</color>\n"
                  + string.Join("\n", CaptionView.Wrap(CaptionView.Escape(said), MeChars, MeLines));
        me.gameObject.SetActive(true);
        meUntil = Time.unscaledTime + MeS;
    }

    /// <summary>A short note in the same place, e.g. new meeting notes (NotesPanel).</summary>
    public void Flash(string message)
    {
        me.text = $"<color=#ff9500>{message}</color>";
        me.gameObject.SetActive(true);
        meUntil = Time.unscaledTime + FlashS;
    }

    void Redraw()
    {
        bool de = Ui.German;
        int lang = System.Array.IndexOf(Langs, Target);
        labels[0].text = "<color=#cbd5e1>Translate me</color>";
        values[0].text = Translate
            ? (de ? "AN" : "ON") + (meetingLang != null ? $" <size=40>→ {meetingLang.ToUpper()}</size>" : "")
            : $"<color=#94a3b8>{(de ? "AUS" : "OFF")}</color>";
        labels[1].text = $"<color=#cbd5e1>{(de ? "Sprache" : "Language")}</color>";
        values[1].text = $"{Target.ToUpper()} <size=40>{(listOpen ? "‹" : "›")}</size>";
        labels[2].text = $"<color=#cbd5e1>{(de ? "Hilfe" : "Help")}</color>";
        values[2].text = "?";
        for (int i = 0; i < 3; i++)
        {
            tiles[i].Set(TileW, TileH, 0.025f, tileAt[i], i == 0 && Translate ? On : Tile);
            frames[i].Show(hover[0].Item == (Item)i || hover[1].Item == (Item)i);
        }
        if (!listOpen) return;
        for (int i = 0; i < rows.Length; i++)
        {
            bool current = i == lang, look = hover[0].Row == i || hover[1].Row == i;
            rows[i].Set(RowW, RowH - 0.01f, 0.02f, rowAt[i], look ? Gaze : current ? On : Tile);
            rowText[i].text = look ? $"<color=#111111><b>{LangNames[i]}</b></color>" : current ? $"<b>{LangNames[i]}</b>" : LangNames[i];
        }
    }
}
