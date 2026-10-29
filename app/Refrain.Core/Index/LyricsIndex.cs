using Microsoft.Data.Sqlite;
using Refrain.Core.Lyrics;

namespace Refrain.Core.Index;

/// <summary>A track that needs a lookup: one per key, since one lookup serves every track with that key.</summary>
public sealed record PendingTrack(long EntryId, long FolderId, string Key, LyricsTrack Track);

/// <summary>What the Lyrics tab shows for one track, all from the index: no file is read.</summary>
public sealed record LyricsInfo(long EntryId, string Name, LyricsTrack? Track, string? Key, LyricsEntry? Entry,
    string? ReadError);

/// <summary>The index side of the lyrics service (SPEC §9), ported from the Python app's
/// <c>core/index/lyrics_index.py</c>: which tracks still need an LRCLIB lookup, and recording a result with the
/// search index following it.</summary>
public static class LyricsIndex
{
    const string TrackSelect = """
        SELECT t.entry_id, e.parent_id, e.name, t.artist, t.albumartist, t.title, t.album, t.duration, t.lyrics_key
        FROM tracks t
        JOIN entries e ON e.id = t.entry_id
        """;

    static PendingTrack? Read(SqliteDataReader r)
    {
        var track = LyricsTrack.From(r.NullableString(3), r.NullableString(4), r.NullableString(5), r.NullableString(6),
            r.NullableDouble(7), r.GetString(2));
        return track is null ? null : new PendingTrack(r.GetInt64(0), r.GetInt64(1), r.GetString(8), track);
    }

    /// <summary>Tracks whose key has no entry, or only a stale not_found, one per key, folder by folder. Tracks
    /// without artist or title and unreadable files are never pending; keys in <paramref name="skip"/> are left
    /// out.</summary>
    public static List<PendingTrack> Pending(SqliteConnection conn, string retryBefore, long? folderId = null,
        IReadOnlySet<string>? skip = null, int? limit = null)
    {
        using var cmd = conn.Command(TrackSelect + """

            LEFT JOIN lyrics l ON l.key = t.lyrics_key
            WHERE t.lyrics_key IS NOT NULL AND t.read_error IS NULL
              AND (l.key IS NULL OR (l.status = 'not_found' AND l.checked < $before))
              AND ($folder IS NULL OR e.parent_id = $folder)
            ORDER BY e.parent_id, e.name COLLATE NOCASE
            """, null, ("$before", retryBefore), ("$folder", folderId));
        using var r = cmd.ExecuteReader();
        var seen = new HashSet<string>(skip ?? new HashSet<string>());
        var pending = new List<PendingTrack>();
        while (r.Read() && (limit is null || pending.Count < limit))
            if (seen.Add(r.GetString(8)) && Read(r) is { } track)
                pending.Add(track);
        return pending;
    }

    /// <summary>How many keys still need a lookup (Pending's rule, counted).</summary>
    public static long PendingCount(SqliteConnection conn, string retryBefore) =>
        (long)conn.Scalar("""
            SELECT count(DISTINCT t.lyrics_key) FROM tracks t LEFT JOIN lyrics l ON l.key = t.lyrics_key
            WHERE t.lyrics_key IS NOT NULL AND t.read_error IS NULL
              AND (l.key IS NULL OR (l.status = 'not_found' AND l.checked < $before))
            """, null, ("$before", retryBefore))!;

    /// <summary>One track to look up, whatever its entry says (Retry now, the selected track); null without a key.</summary>
    public static PendingTrack? TrackFor(SqliteConnection conn, long entryId)
    {
        using var cmd = conn.Command(TrackSelect + " WHERE t.entry_id = $id AND t.lyrics_key IS NOT NULL AND t.read_error IS NULL",
            null, ("$id", entryId));
        using var r = cmd.ExecuteReader();
        return r.Read() ? Read(r) : null;
    }

    public static LyricsEntry? Get(SqliteConnection conn, string key, SqliteTransaction? tx = null)
    {
        using var cmd = conn.Command("SELECT status, lrclib_id, plain, synced, checked FROM lyrics WHERE key = $k", tx, ("$k", key));
        using var r = cmd.ExecuteReader();
        return r.Read()
            ? new LyricsEntry(r.GetString(0), r.NullableLong(1), r.NullableString(2), r.NullableString(3), r.NullableString(4) ?? "")
            : null;
    }

    /// <summary>The Lyrics tab's view of one audio file; null when it isn't an indexed track.</summary>
    public static LyricsInfo? Info(SqliteConnection conn, long entryId)
    {
        using var cmd = conn.Command("""
            SELECT e.name, t.artist, t.albumartist, t.title, t.album, t.duration, t.lyrics_key, t.read_error
            FROM tracks t JOIN entries e ON e.id = t.entry_id WHERE t.entry_id = $id
            """, null, ("$id", entryId));
        using var r = cmd.ExecuteReader();
        if (!r.Read()) return null;
        var key = r.NullableString(6);
        var track = LyricsTrack.From(r.NullableString(1), r.NullableString(2), r.NullableString(3), r.NullableString(4),
            r.NullableDouble(5), r.GetString(0));
        return new LyricsInfo(entryId, r.GetString(0), track, key, key is null ? null : Get(conn, key), r.NullableString(7));
    }

    /// <summary>Stores a lookup result (a writer job); every track with the key gets its LRCLIB text in the search
    /// index (or none when the key isn't found). Returns the folders holding the key.</summary>
    public static List<long> Record(SqliteConnection conn, SqliteTransaction tx, string key, LyricsEntry entry)
    {
        conn.Execute("""
            INSERT INTO lyrics (key, status, lrclib_id, plain, synced, checked) VALUES ($k, $s, $id, $p, $sy, $c)
            ON CONFLICT (key) DO UPDATE SET status = $s, lrclib_id = $id, plain = $p, synced = $sy, checked = $c
            """, tx, ("$k", key), ("$s", entry.Status), ("$id", entry.LrclibId), ("$p", entry.Plain),
            ("$sy", entry.Synced), ("$c", entry.Checked));
        conn.Execute("UPDATE search SET lrclib = $p WHERE rowid IN (SELECT entry_id FROM tracks WHERE lyrics_key = $k)",
            tx, ("$p", entry.Status == LyricsEntry.Found ? entry.Plain : null), ("$k", key));
        using var cmd = conn.Command("""
            SELECT DISTINCT e.parent_id FROM tracks t JOIN entries e ON e.id = t.entry_id
            WHERE t.lyrics_key = $k AND t.read_error IS NULL
            """, tx, ("$k", key));
        using var r = cmd.ExecuteReader();
        var folders = new List<long>();
        while (r.Read()) folders.Add(r.GetInt64(0));
        return folders;
    }
}
