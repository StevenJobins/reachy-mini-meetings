// What the subtitles show: the last few things said in the room and by the headset user, oldest first. Pure logic, no
// Unity, tested in Assets/Tests. Room lines are updated by caption id (partial -> final -> + translation) and carry
// the backend's speaker id (from the voice); "You" lines are the backend's transcription of the headset mic, and while
// push to talk is held (and until that text arrives) a live "You" line stands at the end.

using System;
using System.Collections.Generic;
using System.Linq;

public class SubtitleLine
{
    public int Seq;              // order of appearance
    public int? CaptionId;
    public bool You, Final, Live;
    public int? Speaker;         // 1, 2, ... from the voice; null = not known
    public string Text = "", Original;
    public double Until;
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
    public void Caption(int id, bool final, string text, string translation, string lang, int? speaker, double now)
    {
        var line = lines.Find(l => l.CaptionId == id);
        if (string.IsNullOrEmpty(text))
        {
            if (line != null) lines.Remove(line);
            return;
        }
        if (line == null) lines.Add(line = new SubtitleLine { CaptionId = id, Seq = seq++ });
        bool translated = !string.IsNullOrEmpty(translation);
        line.Text = translated ? translation : text;
        line.Original = translated ? $"{lang}: {text}" : null;
        line.Final = final;
        if (speaker != null) line.Speaker = speaker;   // a final too short to tell keeps the partial's guess
        line.Until = now + (final ? FinalS : PartialS);
    }

    /// <summary>What the headset user said; ends the live line.</summary>
    public void Me(string text, double now)
    {
        liveUntil = double.NegativeInfinity;
        if (!string.IsNullOrEmpty(text))
            lines.Add(new SubtitleLine { You = true, Final = true, Text = text, Seq = seq++, Until = now + FinalS });
    }

    /// <summary>Every frame: push to talk held or not.</summary>
    public void Mic(bool on, double now)
    {
        if (on) liveUntil = now + YouWaitS;
    }

    public List<SubtitleLine> Shown(double now)
    {
        lines.RemoveAll(l => now > l.Until);
        var shown = lines.OrderBy(l => l.Seq).ToList();
        if (now < liveUntil) shown.Add(new SubtitleLine { You = true, Live = true, Seq = int.MaxValue });
        return shown.Skip(Math.Max(0, shown.Count - MaxLines)).ToList();
    }
}
