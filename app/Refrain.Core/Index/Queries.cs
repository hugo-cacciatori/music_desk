using Microsoft.Data.Sqlite;

namespace Refrain.Core.Index;

/// <summary>Read queries for the browser (SPEC §5): they only touch indexed data, so a folder opens at once even on a
/// slow drive.</summary>
public static class Queries
{
    /// <summary>The file list's columns for a set of entries, then <paramref name="extra"/> columns; the WHERE
    /// clause is the caller's.</summary>
    internal static string ListingSelect(string extra = "") => ListingColumns + extra + ListingFrom;

    internal const int ListingColumnCount = 21;

    const string ListingColumns = """
        SELECT e.id, e.name, e.is_dir, e.ext, e.size, e.mtime,
               t.entry_id IS NOT NULL, t.track, t.title, t.artist, t.album, t.date, coalesce(t.duration, s.duration),
               t.format, t.bitrate, (t.rg_track_gain IS NOT NULL) + (t.rg_track_peak IS NOT NULL)
                   + (t.rg_album_gain IS NOT NULL) + (t.rg_album_peak IS NOT NULL),
               l.status, l.synced, t.read_error, s.n_tracks, t.lyrics_key
        """;

    const string ListingFrom = """

        FROM entries e
        LEFT JOIN tracks t ON t.entry_id = e.id
        LEFT JOIN lyrics l ON l.key = t.lyrics_key
        LEFT JOIN folder_stats s ON s.entry_id = e.id
        """;

    internal static FileRow ReadRow(SqliteDataReader r)
    {
        var isDir = r.GetInt64(2) != 0;
        var isRead = r.GetInt64(6) != 0;
        var rg = isRead ? (int)r.GetInt64(15) : 0;
        return new FileRow
        {
            Id = r.GetInt64(0), Name = r.GetString(1), IsDir = isDir, Ext = r.NullableString(3),
            Size = r.NullableLong(4), Mtime = r.NullableDouble(5), IsRead = isRead, Track = r.NullableLong(7),
            Title = r.NullableString(8), Artist = r.NullableString(9), Album = r.NullableString(10),
            Year = r.NullableString(11), Duration = r.NullableDouble(12), Format = r.NullableString(13),
            Bitrate = r.NullableLong(14),
            ReplayGain = isRead ? rg == 4 ? ReplayGainState.Full : rg > 0 ? ReplayGainState.Partial : ReplayGainState.None : null,
            Lrclib = isRead ? LrclibState(r.NullableString(16), r.NullableString(17), r.NullableString(20), r.NullableString(18)) : null,
            ReadError = r.NullableString(18),
            TrackCount = isDir ? r.NullableLong(19) ?? 0 : null,
            LyricsKey = r.NullableString(20),
        };
    }

    /// <summary>The LRCLIB column (SPEC §5): blank when the tags can't be read; "no tags" without artist or
    /// title (never looked up).</summary>
    static string LrclibState(string? status, string? synced, string? key, string? readError) =>
        readError is not null ? ""
        : key is null ? "no tags"
        : status switch
        {
            "found" => string.IsNullOrEmpty(synced) ? "plain" : "synced",
            "instrumental" => "instrumental",
            "not_found" => "not found",
            _ => "pending",
        };

    /// <summary>Every entry of a folder, folders and files, in no particular order: the view sorts.</summary>
    public static List<FileRow> Listing(SqliteConnection conn, long folderId)
    {
        using var cmd = conn.Command(ListingSelect() + " WHERE e.parent_id = $p", null, ("$p", folderId));
        using var r = cmd.ExecuteReader();
        var rows = new List<FileRow>();
        while (r.Read()) rows.Add(ReadRow(r));
        return rows;
    }

    /// <summary>Subfolders in Explorer's natural order, for the tree.</summary>
    public static List<FolderNode> ChildFolders(SqliteConnection conn, long parentId)
    {
        using var cmd = conn.Command("""
            SELECT e.id, e.name, EXISTS (SELECT 1 FROM entries c WHERE c.parent_id = e.id AND c.is_dir = 1),
                   coalesce(s.n_tracks, 0)
            FROM entries e LEFT JOIN folder_stats s ON s.entry_id = e.id
            WHERE e.parent_id = $p AND e.is_dir = 1
            """, null, ("$p", parentId));
        using var r = cmd.ExecuteReader();
        var folders = new List<FolderNode>();
        while (r.Read()) folders.Add(new FolderNode(r.GetInt64(0), r.GetString(1), r.GetInt64(2) != 0, r.GetInt64(3)));
        folders.Sort((a, b) => NaturalOrder.Instance.Compare(a.Name, b.Name));
        return folders;
    }

    /// <summary>The top entry of each root, by root id.</summary>
    public static long? TopEntry(SqliteConnection conn, long rootId) =>
        (long?)conn.Scalar("SELECT id FROM entries WHERE root_id = $r AND parent_id IS NULL", null, ("$r", rootId));

    /// <summary>The chain from the root's top entry down to this entry: (id, name), the first name a full path.</summary>
    public static List<(long Id, string Name)> Chain(SqliteConnection conn, long entryId)
    {
        using var cmd = conn.Command("""
            WITH RECURSIVE up(id, parent_id, name, depth) AS (
                SELECT id, parent_id, name, 0 FROM entries WHERE id = $id
                UNION ALL
                SELECT e.id, e.parent_id, e.name, up.depth + 1 FROM entries e JOIN up ON e.id = up.parent_id
            )
            SELECT id, name FROM up ORDER BY depth DESC
            """, null, ("$id", entryId));
        using var r = cmd.ExecuteReader();
        var chain = new List<(long, string)>();
        while (r.Read()) chain.Add((r.GetInt64(0), r.GetString(1)));
        return chain;
    }

    /// <summary>An entry's full path, or null when it left the index.</summary>
    public static string? PathOf(SqliteConnection conn, long entryId)
    {
        var chain = Chain(conn, entryId);
        return chain.Count == 0 ? null : Path.Combine(chain.Select(c => c.Name).ToArray());
    }

    public static bool IsDir(SqliteConnection conn, long entryId) =>
        conn.Scalar("SELECT is_dir FROM entries WHERE id = $id", null, ("$id", entryId)) is long d && d != 0;

    public static long? ParentOf(SqliteConnection conn, long entryId) =>
        (long?)conn.Scalar("SELECT parent_id FROM entries WHERE id = $id", null, ("$id", entryId));

    /// <summary>The indexed entry for a path, and how much of the path was found: an exact match, or the nearest
    /// indexed ancestor (Exact false); null outside every root.</summary>
    public static (long Id, bool Exact)? Find(SqliteConnection conn, string path)
    {
        path = RealPath.Normalize(path);
        var root = Roots.All(conn)
            .Where(r => RealPath.IsInside(path, r.Path))
            .MaxBy(r => r.Path.Length);
        if (root is null) return null;
        var current = TopEntry(conn, root.Id)!.Value;
        var rest = path[root.Path.TrimEnd('\\').Length..].Split('\\', StringSplitOptions.RemoveEmptyEntries);
        foreach (var name in rest)
        {
            var child = (long?)conn.Scalar("SELECT id FROM entries WHERE parent_id = $p AND name = $n COLLATE NOCASE",
                null, ("$p", current), ("$n", name));
            if (child is null) return (current, false);
            current = child.Value;
        }
        return (current, true);
    }

    /// <summary>The nearest ancestor of a vanished entry that is still indexed, from a remembered chain of ids.</summary>
    public static long? NearestSurviving(SqliteConnection conn, IEnumerable<long> chainFromTop)
    {
        long? found = null;
        foreach (var id in chainFromTop)
        {
            if (conn.Scalar("SELECT 1 FROM entries WHERE id = $id", null, ("$id", id)) is null) break;
            found = id;
        }
        return found;
    }

    public static bool Exists(SqliteConnection conn, long entryId) =>
        conn.Scalar("SELECT 1 FROM entries WHERE id = $id", null, ("$id", entryId)) is not null;
}
