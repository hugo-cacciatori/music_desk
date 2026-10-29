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
        Directory.CreateDirectory(folder);
        var stamp = (now ?? DateTime.Now).ToString("yyyyMMdd-HHmmss", CultureInfo.InvariantCulture);
        var target = System.IO.Path.Combine(folder, $"{Prefix}{stamp}.m3u8");
        for (var n = 2; File.Exists(target); n++) target = System.IO.Path.Combine(folder, $"{Prefix}{stamp}-{n}.m3u8");
        File.WriteAllText(target, Render(entries), new UTF8Encoding(false));
        Prune(folder);
        return target;
    }

    /// <summary>Removes the oldest of the app's own <c>play-*.m3u8</c> files in that folder, and nothing else.</summary>
    public static void Prune(string folder, int keep = Keep)
    {
        var files = Directory.GetFiles(folder, $"{Prefix}*.m3u8").Order(NaturalOrder.Instance).ToList();
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
