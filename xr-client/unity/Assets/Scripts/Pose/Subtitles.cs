// What the subtitles show: the last few things said in the room and by the headset user, oldest first. Pure logic, no
// Unity, tested in Assets/Tests. Room lines are updated by caption id (partial -> final -> + translation) and carry
// the backend's participant number and name (backend roster.py: from the voice, the mouths and the mic direction);
// "You" lines are the backend's transcription of the headset mic, and while you speak into the open mic (and until
// that text arrives) a live "You" line stands at the end.
//
// One person, one label, everywhere (SpeakerLabel): the tag above the head and every subtitle line of that person,
// original or translated, live or final, carry the same name in the same colour. A line keeps who said it through
// all its updates; only the backend saying so changes it (it found out later whose voice it was, or their name).

using System;
using System.Collections.Generic;
using System.Linq;

public class SubtitleLine
{
    public int Seq;              // order of appearance
    public int? CaptionId;
    public bool You, Final, Live;
    public bool Waiting;         // being translated and nothing translated to show yet: dots
    public bool Translated;
    public int? Speaker;         // 1, 2, ... the participant; null = not known
    public string Name;          // the name they gave of themselves, null = not known ("Speaker A")
    public string Text = "", Original;
    public double Until;
}

/// <summary>How a participant is written and coloured, on the tag above the head and in the subtitles.</summary>
public static class SpeakerLabel
{
    public static readonly string[] Colors =
        { "#ff9500", "#38bdf8", "#4ade80", "#f472b6", "#a78bfa", "#facc15", "#f87171", "#2dd4bf" };
    public const string Unknown = "#9ca3af";

    /// <summary>Their name if they said it, else "Speaker A", "Speaker B", ...; "" when not known who.</summary>
    public static string Name(int? speaker, string name) =>
        !string.IsNullOrEmpty(name) ? name : speaker == null ? "" : $"Speaker {(char)('A' + (speaker.Value - 1) % 26)}";

    public static string Color(int? speaker) =>
        speaker == null ? Unknown : Colors[((speaker.Value - 1) % Colors.Length + Colors.Length) % Colors.Length];
}

public class Subtitles
{
    public const int MaxLines = 3;
    public const double FinalS = 10, PartialS = 6;   // a line stays this long after its last update
    public const double YouWaitS = 4;                // the live "You" line waits this long after release for the text

    readonly List<SubtitleLine> lines = new List<SubtitleLine>();
    int seq;
    double liveUntil = double.NegativeInfinity;

    /// <summary>A room caption (same id = update). Empty text removes it: nothing was said.</summary>
    /// <summary>translating: the backend is translating this text. The original is not shown meanwhile (reading German,
    /// then English, then German again as the sentence grows is hard): an earlier translation stays, else dots.</summary>
    public void Caption(int id, bool final, string text, string translation, string lang, int? speaker, double now,
        bool translating = false, string name = null)
    {
        var line = lines.Find(l => l.CaptionId == id);
        if (string.IsNullOrEmpty(text))
        {
            if (line != null) lines.Remove(line);
            return;
        }
        if (line == null) lines.Add(line = new SubtitleLine { CaptionId = id, Seq = seq++ });
        bool translated = !string.IsNullOrEmpty(translation);
        if (speaker != null)
        {
            if (line.Speaker != speaker) line.Name = null;   // someone else after all: not under the old name
            line.Speaker = speaker;
            if (!string.IsNullOrEmpty(name)) line.Name = name;
        }
        if (translating && !translated)
        {
            line.Waiting = !line.Translated;
            line.Final = false;
            line.Until = now + (final ? FinalS : PartialS);
            return;
        }
        line.Waiting = false;
        line.Translated = translated;
        line.Text = translated ? translation : text;
        line.Original = translated ? $"{lang}: {text}" : null;
        line.Final = final;   // (a final too short to tell who it was keeps the live text's guess)
        line.Until = now + (final ? FinalS : PartialS);
    }

    /// <summary>What the headset user said; ends the live line.</summary>
    public void Me(string text, double now)
    {
        liveUntil = double.NegativeInfinity;
        if (!string.IsNullOrEmpty(text))
            lines.Add(new SubtitleLine { You = true, Final = true, Text = text, Seq = seq++, Until = now + FinalS });
    }

    /// <summary>Every frame: speaking into the open mic or not (MicSender's voice gate).</summary>
    public void Mic(bool on, double now)
    {
        if (on) liveUntil = now + YouWaitS;
    }

    /// <summary>The backend learnt this participant's name: their lines on show carry it from now on.</summary>
    public void Named(int speaker, string name)
    {
        foreach (var l in lines.Where(l => l.Speaker == speaker)) l.Name = name;
    }

    public List<SubtitleLine> Shown(double now)
    {
        lines.RemoveAll(l => now > l.Until);
        var shown = lines.OrderBy(l => l.Seq).ToList();
        if (now < liveUntil) shown.Add(new SubtitleLine { You = true, Live = true, Seq = int.MaxValue });
        return shown.Skip(Math.Max(0, shown.Count - MaxLines)).ToList();
    }
}
