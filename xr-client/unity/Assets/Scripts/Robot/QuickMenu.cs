// The few settings the app has, in a row under the video window that shows only while you look at it.
// Look at an entry and pinch left to switch it (RobotControl); otherwise the left pinch recenters as before.
//   Translate me on / off: your voice goes to the backend, Reachy says it in the meeting language (MicSender)
//   Bubbles: the language the room is translated into; starts as the headset's system language
//   Help: the guided tour again (Tutorial)
// Always there, small: "translated -> EN" while Translate me is on, and for a few seconds what Reachy said for you
// (the backend's "me" message). Settings are remembered on the headset and sent again on every reconnect.

using System.Linq;
using UnityEngine;

public class QuickMenu : MonoBehaviour
{
    [System.Serializable] public class MeMsg { public string text, lang, translation, target; }

    const string TranslateKey = "translate", TargetKey = "target";
    static readonly string[] Langs = { "de", "en", "fr", "it", "es" };
    static readonly string[] LangNames = { "Deutsch", "English", "Français", "Italiano", "Español" };
    const float ShowDeg = 14f;            // the row shows while the gaze is this close to it
    // menu-local metres (the row is scaled 1.6 in ProjectConfigure): centre and width of each entry
    static readonly float[] ItemX = { -0.78f, 0.06f, 0.78f }, ItemW = { 0.6f, 0.78f, 0.36f };
    const float ItemH = 0.14f;
    const float MeS = 6f;
    const int MeChars = 60, MeLines = 2;

    public RoomSound room;
    public Transform head;
    public Material textMaterial;
    public Tutorial tutorial;

    TextMesh[] items;
    TextMesh line, me;
    Font font;
    Material material;
    int gazed = -1;
    int sentConnects = -1;
    float meUntil;
    string meetingLang;   // what Reachy speaks for you, known from the first "me" message

    public bool Translate { get; private set; }
    public string Target { get; private set; }
    public bool Gazed => gazed >= 0;

    void Awake()
    {
        font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
        material = new Material(textMaterial);
        UpdateAtlas(font);
        Font.textureRebuilt += UpdateAtlas;

        Translate = PlayerPrefs.GetInt(TranslateKey, 1) == 1;
        Target = PlayerPrefs.GetString(TargetKey, SystemLang());
        items = new[] { "Translate", "Bubbles", "Help" }.Select((n, i) => Text(n, new Vector3(ItemX[i], 0, 0), 48)).ToArray();
        line = Text("Line", new Vector3(0, -0.13f, 0), 36);
        me = Text("Me", new Vector3(0, 0.16f, 0), 44);
        me.anchor = TextAnchor.LowerCenter;
        Redraw();
    }

    void OnDestroy() => Font.textureRebuilt -= UpdateAtlas;

    void UpdateAtlas(Font f)
    {
        if (f == font) material.mainTexture = font.material.mainTexture;
    }

    TextMesh Text(string name, Vector3 at, int size)
    {
        var go = new GameObject(name);
        go.transform.SetParent(transform, false);
        go.transform.localPosition = at;
        var t = go.AddComponent<TextMesh>();
        t.font = font;
        t.fontSize = size;
        t.characterSize = 0.01f;
        t.anchor = TextAnchor.MiddleCenter;
        t.alignment = TextAlignment.Center;
        t.richText = true;
        t.color = Color.white;
        go.GetComponent<MeshRenderer>().sharedMaterial = material;
        return t;
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

        // where the gaze meets the menu plane, in menu coordinates
        var ray = new Ray(head.position, head.forward);
        bool near = Vector3.Angle(head.forward, transform.position - head.position) < ShowDeg;
        gazed = -1;
        if (near && new Plane(-transform.forward, transform.position).Raycast(ray, out float d))
        {
            var p = transform.InverseTransformPoint(ray.GetPoint(d));
            for (int i = 0; i < items.Length; i++)
            {
                var c = items[i].transform.localPosition;
                if (Mathf.Abs(p.x - c.x) < ItemW[i] / 2 && Mathf.Abs(p.y - c.y) < ItemH / 2) gazed = i;
            }
        }
        foreach (var t in items) t.gameObject.SetActive(near);
        if (me.gameObject.activeSelf && Time.unscaledTime > meUntil) me.gameObject.SetActive(false);
        Redraw();
    }

    /// <summary>Left pinch while looking at an entry.</summary>
    public void Select()
    {
        if (gazed == 0)
        {
            Translate = !Translate;
            PlayerPrefs.SetInt(TranslateKey, Translate ? 1 : 0);
            Debug.Log($"[menu] translate {(Translate ? "on" : "off")}");
        }
        else if (gazed == 1)
        {
            Target = Langs[(System.Array.IndexOf(Langs, Target) + 1) % Langs.Length];
            PlayerPrefs.SetString(TargetKey, Target);
            SendLang();
            Debug.Log($"[menu] bubbles in {Target}");
        }
        else if (gazed == 2 && tutorial != null) tutorial.Show();
        PlayerPrefs.Save();
    }

    void SendLang()
    {
        if (room.Backend == null) return;
        // what Reachy speaks stays "auto": the language most spoken in the room (backend/README.md)
        room.Backend.Send($"{{\"type\": \"lang\", \"meeting\": \"auto\", \"target\": \"{Target}\"}}");
    }

    /// <summary>A short note in the same place as the "me" line, e.g. new meeting notes (NotesPanel).</summary>
    public void Flash(string message)
    {
        me.text = $"<color=#ff9500>{message}</color>";
        me.gameObject.SetActive(true);
        meUntil = Time.unscaledTime + 4f;
    }

    /// <summary>What Reachy just said for you (from CaptionView).</summary>
    public void ShowMe(MeMsg m)
    {
        bool translated = !string.IsNullOrEmpty(m.translation);
        meetingLang = m.target;
        var said = translated ? m.translation : m.text;
        var arrow = translated ? $"{m.lang}→{m.target}" : m.lang;
        me.text = $"<color=#9ca3af>Reachy ({arrow}):</color> "
                  + string.Join("\n", CaptionView.Wrap(CaptionView.Escape(said), MeChars, MeLines));
        me.gameObject.SetActive(true);
        meUntil = Time.unscaledTime + MeS;
    }

    void Redraw()
    {
        bool de = Tutorial.German;
        items[0].text = Item(0, $"Translate me: {(Translate ? $"<b>{(de ? "an" : "on")}</b>" : de ? "aus" : "off")}");
        int i = System.Array.IndexOf(Langs, Target);
        items[1].text = Item(1, $"{(de ? "Sprechblasen" : "Bubbles")}: <b>{(i >= 0 ? LangNames[i] : Target)}</b> >");
        items[2].text = Item(2, de ? "Hilfe" : "Help");
        line.text = !Translate ? ""
            : meetingLang != null ? $"<color=#9ca3af>{(de ? "deine Stimme übersetzt" : "your voice translated")} → {meetingLang.ToUpper()}</color>"
            : $"<color=#9ca3af>{(de ? "deine Stimme wird in die Sprache des Raums übersetzt" : "your voice translated into the room's language")}</color>";
    }

    string Item(int i, string label) => gazed == i ? $"<color=#facc15>{label}</color>" : label;
}
