using System.Globalization;
using System.Text;
using Microsoft.Data.Sqlite;
using Refrain.Core.Index;

namespace Refrain.Core;

/// <summary>One track of a playlist: its path, duration and tags, from the index.</summary>
public sealed record PlayEntry(string Path, double? Duration, string? Artist, string? Title)
{
    /// <summary>"Artist - Title", else the title, else the file name without its extension.</summary>
    public string Label
    {
        get
        {
            var (artist, title) = ((Artist ?? "").Trim(), (Title ?? "").Trim());
            if (artist.Length > 0 && title.Length > 0) return $"{artist} - {title}";
            return title.Length > 0 ? title : System.IO.Path.GetFileNameWithoutExtension(Path);
        }
    }
}

/// <summary>Playing through the default app (SPEC §14): Refrain has no player of its own. A temporary UTF-8
/// <c>.m3u8</c> goes to the data folder's <c>playlists\</c>, the default player opens it, and the newest 20 are
/// kept. Ported from the Python app's <c>core/playlists.py</c>.</summary>
public static class Playlists
{
    const int Keep = 20;
    const string Prefix = "play-";

    public static string Folder => System.IO.Path.Combine(AppPaths.DataFolder, "playlists");

    /// <summary>Extended M3U text: audio files only, absolute paths, Windows line endings.</summary>
    public static string Render(IEnumerable<PlayEntry> entries)
    {
        var text = new StringBuilder("#EXTM3U\r\n");
        foreach (var entry in entries.Where(e => Formats.IsAudio(System.IO.Path.GetExtension(e.Path))))
        {
            var seconds = entry.Duration is > 0 ? Math.Round(entry.Duration.Value, MidpointRounding.ToEven) : -1;
            text.Append(CultureInfo.InvariantCulture, $"#EXTINF:{seconds},{entry.Label}\r\n{entry.Path}\r\n");
        }
        return text.ToString();
    }

    /// <summary>Writes a playlist under a new timestamped name (<c>play-YYYYMMDD-HHMMSS[-n].m3u8</c>), then removes all
    /// but the newest 20.</summary>
    public static string Write(IReadOnlyList<PlayEntry> entries, string? folder = null, DateTime? now = null)
    {
        folder ??= Folder;
        var target = NewFile(folder, Prefix, ".m3u8", now);
        File.WriteAllText(target, Render(entries), new UTF8Encoding(false));
        Prune(folder);
        return target;
    }

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

    /// <summary>Writes <c>preview-YYYYMMDD-HHMMSS[-n].cue</c>, then removes all but the newest 20 preview sheets; the
    /// temporary playlists are left alone, and they leave these alone.</summary>
    public static string WritePreview(string text, string? folder = null, DateTime? now = null)
    {
        folder ??= Folder;
        var target = NewFile(folder, "preview-", ".cue", now);
        File.WriteAllText(target, text, new UTF8Encoding(false));
        Prune(folder, Keep, "preview-*.cue");
        return target;
    }

    /// <summary>Removes the oldest of the app's own temporary files of one kind (<c>play-*.m3u8</c>, or
    /// <c>preview-*.cue</c>) in that folder, and nothing else.</summary>
    public static void Prune(string folder, int keep = Keep, string pattern = Prefix + "*.m3u8")
    {
        var files = Directory.GetFiles(folder, pattern).Order(NaturalOrder.Instance).ToList();
        foreach (var old in files.Take(Math.Max(0, files.Count - keep)))
        {
            try
            {
                File.Delete(old);
            }
            catch (IOException e)
            {
                Log.Info($"Couldn't remove the old playlist {old}: {e.Message}");
            }
        }
    }

    /// <summary>The audio files among these entries, in this order (Play selection, Play results): folders and
    /// other files are skipped; a file whose tags aren't read yet plays too.</summary>
    public static List<PlayEntry> ForIds(SqliteConnection conn, IReadOnlyList<long> ids)
    {
        var entries = new List<PlayEntry>();
        foreach (var id in ids)
        {
            using var cmd = conn.Command("""
                SELECT e.is_dir, e.ext, t.duration, t.artist, t.title FROM entries e
                LEFT JOIN tracks t ON t.entry_id = e.id WHERE e.id = $id
                """, null, ("$id", id));
            using var r = cmd.ExecuteReader();
            if (!r.Read() || r.GetInt64(0) != 0 || !Formats.IsAudio(r.NullableString(1))) continue;
            if (Queries.PathOf(conn, id) is { } path)
                entries.Add(new PlayEntry(path, r.NullableDouble(2), r.NullableString(3), r.NullableString(4)));
        }
        return entries;
    }

    /// <summary>Play folder: its audio files in Explorer's order, then (with subfolders) each subfolder's in turn.</summary>
    public static List<PlayEntry> ForFolder(SqliteConnection conn, long folderId, bool subfolders)
    {
        var entries = new List<PlayEntry>();
        void Walk(long id, string path)
        {
            var rows = Queries.Listing(conn, id);
            rows.Sort(Browser.Columns.Sort("name", false));
            foreach (var row in rows.Where(r => r.IsAudio))
                entries.Add(new PlayEntry(System.IO.Path.Combine(path, row.Name), row.Duration, row.Artist, row.Title));
            if (!subfolders) return;
            foreach (var folder in rows.Where(r => r.IsDir)) Walk(folder.Id, System.IO.Path.Combine(path, folder.Name));
        }
        if (Queries.PathOf(conn, folderId) is { } top) Walk(folderId, top);
        return entries;
    }
}
