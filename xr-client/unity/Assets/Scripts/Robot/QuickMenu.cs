// The app's few settings, as a column of tiles at the left edge of the video, always visible. Look at a tile (with
// the eyes when the headset tracks them, see XrInput; it gets a yellow frame) and pinch left (RobotControl):
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

    // Place in the video window's frame (origin at the eye): on the left edge of the image, which spans ±44°
    const float SideDeg = -37f, Distance = 2.5f;
    const float TileW = 0.27f, TileH = 0.19f, Gap = 0.022f, Pad = 0.026f, Frame = 0.008f;
    const float RowW = 0.3f, RowH = 0.085f;
    const float ListCloseS = 3f, MeS = 6f, FlashS = 4f;
    const int MeChars = 30, MeLines = 4;
    static readonly Color Back = new Color(0.07f, 0.08f, 0.11f, 0.9f);
    static readonly Color Tile = new Color(1, 1, 1, 0.08f);
    static readonly Color On = new Color(0.22f, 0.74f, 0.97f, 0.9f);       // #38bdf8
    static readonly Color Gaze = new Color(0.98f, 0.8f, 0.08f);            // #facc15

    public RoomSound room;
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
    Item gazed = Item.None;
    int gazedRow = -1;
    bool listOpen;
    float listLookedAt;
    int sentConnects = -1;
    float meUntil;
    string meetingLang;   // what Reachy speaks for you, known from the first "me" message

    public bool Translate { get; private set; }
    public string Target { get; private set; }
    public bool Gazed => gazed != Item.None || gazedRow >= 0;
    /// <summary>For the tutorial: what is looked at and what happened.</summary>
    public bool LanguageGazed => gazed == Item.Language;
    public bool ListOpen => listOpen;
    public int LanguagePicks { get; private set; }

    void Awake()
    {
        float a = SideDeg * Mathf.Deg2Rad;
        transform.localPosition = new Vector3(Mathf.Sin(a), 0, Mathf.Cos(a)) * Distance;
        transform.localRotation = Quaternion.Euler(0, SideDeg, 0);
        text = Ui.TextMaterial(textMaterial, 3033);

        Translate = PlayerPrefs.GetInt(TranslateKey, 1) == 1;
        Target = PlayerPrefs.GetString(TargetKey, SystemLang());

        float h = 3 * TileH + 2 * Gap + 2 * Pad;
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
        ShowList(false);
        Redraw();
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
        }

        var p = Ui.GazeOn(transform, head);
        gazed = Item.None;
        for (int i = 0; i < 3; i++)
            if (Ui.Inside(p, tileAt[i], TileW + 2 * Frame, TileH + 2 * Frame)) gazed = (Item)i;
        gazedRow = -1;
        if (listOpen)
        {
            for (int i = 0; i < rows.Length; i++)
                if (Ui.Inside(p, rowAt[i], RowW, RowH)) gazedRow = i;
            if (gazedRow >= 0 || gazed == Item.Language) listLookedAt = Time.unscaledTime;
            else if (Time.unscaledTime - listLookedAt > ListCloseS) ShowList(false);
        }
        if (me.gameObject.activeSelf && Time.unscaledTime > meUntil) me.gameObject.SetActive(false);
        Redraw();
    }

    /// <summary>Left pinch while looking at a tile or a language.</summary>
    public void Select()
    {
        if (gazedRow >= 0)
        {
            Target = Langs[gazedRow];
            PlayerPrefs.SetString(TargetKey, Target);
            PlayerPrefs.Save();
            SendLang();
            ShowList(false);
            LanguagePicks++;
            Debug.Log($"[menu] language {Target}");
            return;
        }
        switch (gazed)
        {
            case Item.Translate:
                Translate = !Translate;
                PlayerPrefs.SetInt(TranslateKey, Translate ? 1 : 0);
                PlayerPrefs.Save();
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
            frames[i].Show(gazed == (Item)i);
        }
        if (!listOpen) return;
        for (int i = 0; i < rows.Length; i++)
        {
            bool current = i == lang, look = i == gazedRow;
            rows[i].Set(RowW, RowH - 0.01f, 0.02f, rowAt[i], look ? Gaze : current ? On : Tile);
            rowText[i].text = look ? $"<color=#111111><b>{LangNames[i]}</b></color>" : current ? $"<b>{LangNames[i]}</b>" : LangNames[i];
        }
    }
}
