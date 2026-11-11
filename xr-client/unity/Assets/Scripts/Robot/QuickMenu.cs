// The app's few settings, as a column of tiles at the lower left, always visible. It stays still in the room
// (relative to straight ahead, like the notes), not on the video window, which moves whenever Reachy turns. Look at a
// tile with either hand (a dot shows where it points, its ring closes as you pinch, like the headset's own menus) and
// pinch with that hand (RobotControl); without hand rays the eyes pick. The tile pointed at gets a yellow frame:
//   Translate me  on / off: your voice goes to the backend, Reachy says it in the room's language (MicSender)
//   Language      opens a short list next to it: the language the room is translated into (bubbles and notes);
//                 starts as the headset's system language
//   Reachy        awake / asleep: sends Reachy to sleep (lies down, motors off) and wakes it up again, so the
//                 remote user needs nobody at the robot for it (RobotControl.Sleep / WakeUp)
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

    // A row at the foot of the picture, in the middle, below the subtitles, 1.5 m away, three quarters size.
    // It stays put while you look around and glides after you once you look clearly elsewhere, like the subtitles.
    // At the lower left it was too far out (headset test 2026-11-11); fixed in the room it covered things.
    // On the video window at 49°, 37° and 26° it was too far out or moved away when Reachy turned.
    const float DownDeg = 31f, Distance = 1.5f, Scale = 0.75f;
    const float GlideAwayDeg = 25f, GlideAfterS = 0.5f, GlideS = 0.25f;
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
    /// <summary>Not there at all (Curtain: while Reachy gets ready there is nothing to work yet).</summary>
    public bool Hidden;

    enum Item { None = -1, Translate, Language, Robot, Help }
    /// <summary>The laptop answers, but the robot behind it does not run (e.g. a motor fault).</summary>
    bool RobotFault => control.DaemonState != null && control.DaemonState != "running" && control.DaemonState != "unreachable";
    const int Tiles = 4;

    Material text;
    Ui.Rect back, listBack;
    readonly Ui.Rect[] tiles = new Ui.Rect[Tiles], frames = new Ui.Rect[Tiles];
    readonly TextMesh[] labels = new TextMesh[Tiles], values = new TextMesh[Tiles];
    readonly Ui.Rect[] rows = new Ui.Rect[5];
    readonly TextMesh[] rowText = new TextMesh[5];
    TextMesh me;
    Vector3[] tileAt, rowAt;
    class Hover { public Item Item = Item.None; public int Row = -1; public float ItemAt, RowAt; }
    readonly Hover[] hover = { new Hover(), new Hover() };   // left, right hand
    readonly Ui.Cursor[] cursors = new Ui.Cursor[2];
    float w, h, viewYaw, awaySince = -1f;
    bool listOpen, haveYaw, glide;
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

        w = Tiles * TileW + (Tiles - 1) * Gap + 2 * Pad;
        h = TileH + 2 * Pad;
        back = Ui.Quad(transform, "Back", panelMaterial, 3030);
        back.Set(w, h, 0.04f, Vector3.zero, Back);
        tileAt = new Vector3[Tiles];
        for (int i = 0; i < Tiles; i++)
        {
            tileAt[i] = new Vector3(-w / 2 + Pad + TileW / 2 + i * (TileW + Gap), 0, -0.001f);
            frames[i] = Ui.Quad(transform, $"Frame{i}", panelMaterial, 3031);
            frames[i].Set(TileW + 2 * Frame, TileH + 2 * Frame, 0.03f, tileAt[i], Gaze);
            tiles[i] = Ui.Quad(transform, $"Tile{i}", panelMaterial, 3032);
            labels[i] = Ui.Text(transform, $"Label{i}", text, 34, 1f);
            labels[i].transform.localPosition = tileAt[i] + new Vector3(0, 0.05f, -0.002f);
            values[i] = Ui.Text(transform, $"Value{i}", text, 64, 1f, TextAnchor.MiddleCenter, FontStyle.Bold);
            values[i].transform.localPosition = tileAt[i] + new Vector3(0, -0.025f, -0.002f);
        }

        // the language list, above the Language tile
        float listH = rows.Length * RowH + 2 * Pad * 0.6f;
        var listCentre = new Vector3(tileAt[1].x, h / 2 + 0.03f + listH / 2, -0.003f);
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

        me = Ui.Text(transform, "Me", text, 36, 1f, TextAnchor.LowerCenter);
        me.transform.localPosition = new Vector3(tileAt[2].x + TileW / 2 + Gap / 2, h / 2 + 0.03f, -0.002f);   // above the right half
        me.gameObject.SetActive(false);
        for (int i = 0; i < 2; i++) cursors[i] = new Ui.Cursor(transform, panelMaterial);
        ShowList(false);
        Redraw();
    }


    /// <summary>Fixed relative to straight ahead (recenter), heading only, facing the eye.</summary>
    void Place()
    {
        float yaw = head.localEulerAngles.y;
        if (!haveYaw) { viewYaw = yaw; haveYaw = true; }
        float diff = Mathf.DeltaAngle(viewYaw, yaw);
        if (Mathf.Abs(diff) > GlideAwayDeg)
        {
            if (awaySince < 0) awaySince = Time.unscaledTime;
            if (Time.unscaledTime - awaySince > GlideAfterS) glide = true;
        }
        else
        {
            awaySince = -1f;
            if (Mathf.Abs(diff) < 2f) glide = false;
        }
        if (glide) viewYaw += diff * (1 - Mathf.Exp(-Time.unscaledDeltaTime / GlideS));
        var rot = Quaternion.Euler(DownDeg, viewYaw, 0);
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
        transform.localScale = Hidden ? Vector3.zero : Vector3.one * Scale;
        if (Hidden)
        {
            foreach (var hv in hover) { hv.Item = Item.None; hv.Row = -1; }
            foreach (var c in cursors) c.Hide();
            return;
        }
        bool onList = false;
        for (int hand = 0; hand < 2; hand++)
        {
            bool right = hand == 1;
            var aim = XrInput.Aim(right, head);
            var p = Ui.PointOn(transform, aim ?? XrInput.Gaze(head));
            var hv = hover[hand];
            var now = Item.None;
            for (int i = 0; i < Tiles; i++)
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
            bool onMenu = p is Vector3 q && (Ui.Inside(q, Vector3.zero, w + 0.1f, h + 0.1f)
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
            case Item.Robot:
                if (RobotFault) control.RestartDaemon();
                else if (control.Asleep) control.WakeUp();
                else control.Sleep();
                Debug.Log($"[menu] reachy {(RobotFault ? "restart" : control.Asleep ? "to sleep" : "wake up")}");
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
        labels[2].text = "<color=#cbd5e1>Reachy</color>";
        values[2].text = control.Restarting ? $"<size=40>{(de ? "startet neu …" : "restarting …")}</size>"
            : RobotFault ? $"<color=#ff9500><size=40>{(de ? "Fehler: neu starten" : "error: restart")}</size></color>"
            : control.GoingToSleep ? "<size=40>…</size>"
            : control.Asleep ? $"<color=#94a3b8>{(de ? "SCHLÄFT" : "ASLEEP")}</color>"
            : control.IsFollowing ? (de ? "WACH" : "AWAKE")
            : control.Waking ? "<size=40>…</size>" : $"<color=#94a3b8><size=40>{(de ? "nicht da" : "not there")}</size></color>";
        labels[3].text = $"<color=#cbd5e1>{(de ? "Hilfe" : "Help")}</color>";
        values[3].text = "?";
        for (int i = 0; i < Tiles; i++)
        {
            tiles[i].Set(TileW, TileH, 0.025f, tileAt[i], i == 0 && Translate || i == 2 && control.IsFollowing ? On : Tile);
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
