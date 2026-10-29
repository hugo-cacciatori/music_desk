using Microsoft.Data.Sqlite;

namespace Refrain.Core.Index;

/// <summary>The side panel's folder summary (SPEC §7 "As built (M3 task 5)"): its whole subtree's tracks, duration,
/// formats and ReplayGain coverage, and what it holds directly.</summary>
public sealed record FolderSummary(string Name, string Path, long Tracks, double Duration, IReadOnlyList<string> Formats,
    long WithReplayGain, long Subfolders, long Files, long OwnTracks)
{
    static readonly string[] CoverNames = ["folder.jpg", "cover.jpg", "front.jpg", "folder.png", "cover.png", "front.png"];

    public static FolderSummary? Read(SqliteConnection conn, long folderId)
    {
        var path = Queries.PathOf(conn, folderId);
        if (path is null) return null;
        using var cmd = conn.Command("""
            WITH RECURSIVE down(id) AS (
                SELECT $id
                UNION ALL
                SELECT e.id FROM entries e JOIN down ON e.parent_id = down.id
            )
            SELECT count(t.entry_id), coalesce(sum(t.duration), 0), group_concat(DISTINCT t.format),
                   sum(t.rg_track_gain IS NOT NULL)
            FROM down JOIN tracks t ON t.entry_id = down.id WHERE t.read_error IS NULL
            """, null, ("$id", folderId));
        using var r = cmd.ExecuteReader();
        r.Read();
        var formats = (r.NullableString(2) ?? "").Split(',', StringSplitOptions.RemoveEmptyEntries)
            .Order(StringComparer.Ordinal).ToList();
        using var counts = conn.Command($"""
            SELECT sum(is_dir), sum(1 - is_dir), sum(is_dir = 0 AND ext IN ({string.Join(", ", Refrain.Core.Formats.Audio.Select(e => $"'{e}'"))}))
            FROM entries WHERE parent_id = $id
            """, null, ("$id", folderId));
        using var c = counts.ExecuteReader();
        c.Read();
        return new FolderSummary(System.IO.Path.GetFileName(path.TrimEnd('\\')) is { Length: > 0 } name ? name : path, path,
            r.GetInt64(0), r.GetDouble(1), formats, r.NullableLong(3) ?? 0, c.NullableLong(0) ?? 0, c.NullableLong(1) ?? 0,
            c.NullableLong(2) ?? 0);
    }

    /// <summary>Where the folder's cover comes from: its folder image, else the first track with an embedded cover,
    /// searched level by level in natural order. Null when none.</summary>
    public static (string Path, bool Embedded)? CoverSource(SqliteConnection conn, long folderId)
    {
        var path = Queries.PathOf(conn, folderId);
        if (path is null) return null;
        var level = new List<(long Id, string Path)> { (folderId, path) };
        var first = true;
        while (level.Count > 0)
        {
            var next = new List<(long, string)>();
            foreach (var (id, folder) in level)
            {
                var rows = Queries.Listing(conn, id);
                rows.Sort(Browser.Columns.Sort("name", false));
                if (first && FolderImage(rows) is { } image) return (System.IO.Path.Combine(folder, image), false);
                if (rows.FirstOrDefault(r => r.IsAudio && HasCover(conn, r.Id)) is { } track)
                    return (System.IO.Path.Combine(folder, track.Name), true);
                next.AddRange(rows.Where(r => r.IsDir).Select(r => (r.Id, System.IO.Path.Combine(folder, r.Name))));
            }
            first = false;
            level = next;
        }
        return null;
    }

    /// <summary>A folder's own image (folder.jpg, cover.jpg, front.jpg, or .png), shown when a track has no embedded
    /// cover; never written.</summary>
    public static string? FolderImage(SqliteConnection conn, long folderId) =>
        FolderImage(Queries.Listing(conn, folderId)) is { } name && Queries.PathOf(conn, folderId) is { } folder
            ? System.IO.Path.Combine(folder, name)
            : null;

    static string? FolderImage(List<FileRow> rows) =>
        CoverNames.Select(name => rows.FirstOrDefault(r => !r.IsDir && r.Name.Equals(name, StringComparison.OrdinalIgnoreCase)))
            .FirstOrDefault(r => r is not null)?.Name;

    static bool HasCover(SqliteConnection conn, long id) =>
        conn.Scalar("SELECT has_cover FROM tracks WHERE entry_id = $id", null, ("$id", id)) is long n && n != 0;
}
