using System.Globalization;
using System.Text;

namespace Refrain.Core;

/// <summary>Preview in player (SPEC §22, §23): Refrain has no player of its own. A temporary cue sheet goes to the data
/// folder's <c>playlists\</c>, the default player opens it, and the newest 20 are kept. Ported from the Python app's
/// <c>core/playlists.py</c>; Play itself went with the main window (SPEC §36 "11.").</summary>
public static class Playlists
{
    const int Keep = 20;

    public static string Folder => System.IO.Path.Combine(AppPaths.DataFolder, "playlists");

    static string NewFile(string folder, string prefix, string ext, DateTime? now)
    {
        Directory.CreateDirectory(folder);
        var stamp = (now ?? DateTime.Now).ToString("yyyyMMdd-HHmmss", CultureInfo.InvariantCulture);
        var target = System.IO.Path.Combine(folder, $"{prefix}{stamp}{ext}");
        for (var n = 2; File.Exists(target); n++) target = System.IO.Path.Combine(folder, $"{prefix}{stamp}-{n}{ext}");
        return target;
    }

    /// <summary>Preview in player (SPEC §22, §23): one audio file cut at sample positions, as a temporary cue sheet
    /// laid out as EAC does, with the audio's full path (the sheet isn't beside it). A track that rounds to no frame is
    /// left out, so no two tracks start on the same frame. UTF-8 with a BOM, CRLF.</summary>
    public static string RenderPreview(string audio, int rate, long samples, IReadOnlyList<(long Start, string Title, string Performer)> tracks,
        string title = "", string performer = "")
    {
        long Frames(long s) => (2 * s * Cue.CueSheet.FramesPerSecond + rate) / (2 * rate);  // the nearest CD frame, halves up
        static string Quoted(string text) => "\"" + string.Join(" ", text.Replace('"', '\'').Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries)) + "\"";
        var starts = tracks.Select(t => Frames(t.Start)).ToList();
        var end = Frames(samples);
        var lines = new List<string>();
        if (performer.Trim().Length > 0) lines.Add($"PERFORMER {Quoted(performer)}");
        if (title.Trim().Length > 0) lines.Add($"TITLE {Quoted(title)}");
        lines.Add($"FILE {Quoted(System.IO.Path.GetFullPath(audio))} WAVE");
        var number = 0;
        for (var i = 0; i < tracks.Count; i++)
        {
            if (starts[i] >= (i + 1 < starts.Count ? starts[i + 1] : end)) continue;
            lines.Add($"  TRACK {++number:00} AUDIO");
            if (tracks[i].Title.Trim().Length > 0) lines.Add($"    TITLE {Quoted(tracks[i].Title)}");
            if (tracks[i].Performer.Trim().Length > 0) lines.Add($"    PERFORMER {Quoted(tracks[i].Performer)}");
            lines.Add($"    INDEX 01 {Cue.CueDocument.FormatTime(starts[i])}");
        }
        return (char)0xFEFF + string.Join("\r\n", lines) + "\r\n";
    }

    /// <summary>Writes <c>preview-YYYYMMDD-HHMMSS[-n].cue</c>, then removes all but the newest 20 preview sheets.</summary>
    public static string WritePreview(string text, string? folder = null, DateTime? now = null)
    {
        folder ??= Folder;
        var target = NewFile(folder, "preview-", ".cue", now);
        File.WriteAllText(target, text, new UTF8Encoding(false));
        Prune(folder, Keep);
        return target;
    }

    /// <summary>Removes the oldest of the app's own preview sheets in that folder, and nothing else.</summary>
    public static void Prune(string folder, int keep = Keep)
    {
        var files = Directory.GetFiles(folder, "preview-*.cue").Order(NaturalOrder.Instance).ToList();
        foreach (var old in files.Take(Math.Max(0, files.Count - keep)))
        {
            try
            {
                File.Delete(old);
            }
            catch (IOException e)
            {
                Log.Info($"Couldn't remove the old preview sheet {old}: {e.Message}");
            }
        }
    }
}
