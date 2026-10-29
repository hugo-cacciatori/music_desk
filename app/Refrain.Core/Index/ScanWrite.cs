using Microsoft.Data.Sqlite;
using Refrain.Core.Tags;

namespace Refrain.Core.Index;

/// <summary>One item of a folder as listed on disk.</summary>
public sealed record DiskEntry(string Name, bool IsDir, string? Ext, long? Size, double? Mtime);

/// <summary>An audio file whose tags need reading: new, or changed in size or mtime since the last read.</summary>
public sealed record ToRead(long Id, long FolderId, string Path, long? Size, double? Mtime);

/// <summary>What reconciling one folder found: its subfolders (to walk), the files to read, and whether the index
/// changed.</summary>
public sealed record Reconciled(List<(long Id, string Path)> Subfolders, List<ToRead> ToRead, bool Changed);

/// <summary>The index writes of a scan (SPEC §4), each run on the writer thread.</summary>
public static class ScanWrite
{
    /// <summary>Makes a folder's indexed children equal its disk listing: vanished entries go with their subtrees,
    /// new ones are added, changed ones updated. Returns what the walk and the tag read need next.</summary>
    public static Reconciled Reconcile(SqliteConnection conn, SqliteTransaction tx, long folderId, string folderPath,
        IReadOnlyList<DiskEntry> listing)
    {
        var rootId = (long?)conn.Scalar("SELECT root_id FROM entries WHERE id = $id", tx, ("$id", folderId));
        if (rootId is null) return new([], [], false);  // the folder left the index meanwhile
        var indexed = new Dictionary<string, (long Id, string Name, bool IsDir, long? Size, double? Mtime,
            bool HasTrack, long? ReadSize, double? ReadMtime)>(StringComparer.OrdinalIgnoreCase);
        using (var cmd = conn.Command("""
                   SELECT e.id, e.name, e.is_dir, e.size, e.mtime, t.entry_id IS NOT NULL, t.read_size, t.read_mtime
                   FROM entries e LEFT JOIN tracks t ON t.entry_id = e.id WHERE e.parent_id = $p
                   """, tx, ("$p", folderId)))
        using (var r = cmd.ExecuteReader())
            while (r.Read())
                indexed[r.GetString(1)] = (r.GetInt64(0), r.GetString(1), r.GetInt64(2) != 0, r.NullableLong(3),
                    r.NullableDouble(4), r.GetInt64(5) != 0, r.NullableLong(6), r.NullableDouble(7));

        var changed = false;
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var subfolders = new List<(long, string)>();
        var toRead = new List<ToRead>();
        foreach (var item in listing)
        {
            if (!seen.Add(item.Name)) continue;
            var path = Path.Combine(folderPath, item.Name);
            long id;
            if (indexed.TryGetValue(item.Name, out var old) && old.IsDir == item.IsDir)
            {
                id = old.Id;
                if (old.Name != item.Name || old.Size != item.Size || old.Mtime != item.Mtime)
                {
                    conn.Execute("UPDATE entries SET name = $n, ext = $x, size = $s, mtime = $m WHERE id = $id", tx,
                        ("$n", item.Name), ("$x", item.Ext), ("$s", item.Size), ("$m", item.Mtime), ("$id", id));
                    if (old.Name != item.Name)
                        conn.Execute("UPDATE search SET name = $n WHERE rowid = $id", tx, ("$n", item.Name), ("$id", id));
                    changed = true;
                }
                if (!item.IsDir && Formats.IsRead(item.Ext) &&
                    (!old.HasTrack || old.ReadSize != item.Size || old.ReadMtime != item.Mtime))
                    toRead.Add(new ToRead(id, folderId, path, item.Size, item.Mtime));
            }
            else
            {
                if (old.Id != 0)
                    conn.Execute("DELETE FROM entries WHERE id = $id", tx, ("$id", old.Id));  // a file became a folder
                id = (long)conn.Scalar("""
                    INSERT INTO entries (root_id, parent_id, name, is_dir, ext, size, mtime)
                    VALUES ($r, $p, $n, $d, $x, $s, $m) RETURNING id
                    """, tx, ("$r", rootId), ("$p", folderId), ("$n", item.Name), ("$d", item.IsDir ? 1 : 0),
                    ("$x", item.Ext), ("$s", item.Size), ("$m", item.Mtime))!;
                conn.Execute("INSERT INTO search (rowid, name) VALUES ($id, $n)", tx, ("$id", id), ("$n", item.Name));
                if (!item.IsDir && Formats.IsRead(item.Ext))
                    toRead.Add(new ToRead(id, folderId, path, item.Size, item.Mtime));
                changed = true;
            }
            if (item.IsDir) subfolders.Add((id, path));
        }
        foreach (var (name, old) in indexed)
        {
            if (seen.Contains(name)) continue;
            conn.Execute("DELETE FROM entries WHERE id = $id", tx, ("$id", old.Id));  // its subtree cascades
            changed = true;
        }
        return new(subfolders, toRead, changed);
    }

    /// <summary>Writes read tags: the tracks rows and the search rows' tag fields (with LRCLIB's text when its key
    /// is found). Entries gone since are skipped. Returns the folders touched.</summary>
    public static HashSet<long> WriteTracks(SqliteConnection conn, SqliteTransaction tx,
        IEnumerable<(ToRead File, TrackInfo Info)> results)
    {
        var folders = new HashSet<long>();
        using var track = conn.Command("""
            INSERT OR REPLACE INTO tracks (entry_id, format, title, artist, albumartist, album, track, track_total,
                disc, disc_total, date, genre, has_cover, duration, bitrate, sample_rate, channels, rg_track_gain,
                rg_track_peak, rg_album_gain, rg_album_peak, lyrics_key, read_size, read_mtime, read_error)
            SELECT $id, $format, $title, $artist, $albumartist, $album, $track, $track_total, $disc, $disc_total,
                $date, $genre, $has_cover, $duration, $bitrate, $sample_rate, $channels, $rg_track_gain,
                $rg_track_peak, $rg_album_gain, $rg_album_peak, $lyrics_key, $read_size, $read_mtime, $read_error
            WHERE EXISTS (SELECT 1 FROM entries WHERE id = $id)
            """, tx);
        using var search = conn.Command("""
            UPDATE search SET title = $title, artist = $artist, albumartist = $albumartist, album = $album,
                genre = $genre,
                lrclib = (SELECT plain FROM lyrics WHERE key = $lyrics_key AND status = 'found')
            WHERE rowid = $id
            """, tx);
        foreach (var (file, info) in results)
        {
            Bind(track, file.Id, info);
            if (track.ExecuteNonQuery() == 0) continue;
            Bind(search, file.Id, info);
            search.ExecuteNonQuery();
            folders.Add(file.FolderId);
        }
        return folders;
    }

    static void Bind(SqliteCommand cmd, long id, TrackInfo t)
    {
        cmd.Parameters.Clear();
        (string, object?)[] values =
        [
            ("$id", id), ("$format", t.Format), ("$title", t.Title), ("$artist", t.Artist),
            ("$albumartist", t.AlbumArtist), ("$album", t.Album), ("$track", t.Track), ("$track_total", t.TrackTotal),
            ("$disc", t.Disc), ("$disc_total", t.DiscTotal), ("$date", t.Date), ("$genre", t.Genre),
            ("$has_cover", t.HasCover ? 1 : 0), ("$duration", t.Duration), ("$bitrate", t.Bitrate),
            ("$sample_rate", t.SampleRate), ("$channels", t.Channels), ("$rg_track_gain", t.RgTrackGain),
            ("$rg_track_peak", t.RgTrackPeak), ("$rg_album_gain", t.RgAlbumGain), ("$rg_album_peak", t.RgAlbumPeak),
            ("$lyrics_key", t.LyricsKey), ("$read_size", t.ReadSize), ("$read_mtime", t.ReadMtime),
            ("$read_error", t.ReadError),
        ];
        foreach (var (name, value) in values)
            cmd.Parameters.AddWithValue(name, value ?? DBNull.Value);  // names a statement doesn't use are ignored
    }

    /// <summary>Recomputes every folder's subtree totals for a root (SPEC §2 folder_stats): readable tracks and
    /// their duration, counted in each folder above them.</summary>
    public static void RecomputeStats(SqliteConnection conn, SqliteTransaction tx, long rootId)
    {
        conn.Execute("DELETE FROM folder_stats WHERE entry_id IN (SELECT id FROM entries WHERE root_id = $r)", tx,
            ("$r", rootId));
        conn.Execute("""
            WITH RECURSIVE up(folder, duration) AS (
                SELECT e.parent_id, coalesce(t.duration, 0)
                FROM tracks t JOIN entries e ON e.id = t.entry_id
                WHERE e.root_id = $r AND t.read_error IS NULL
                UNION ALL
                SELECT e.parent_id, up.duration FROM up JOIN entries e ON e.id = up.folder
                WHERE e.parent_id IS NOT NULL
            )
            INSERT INTO folder_stats (entry_id, n_tracks, duration)
            SELECT folder, count(*), sum(duration) FROM up GROUP BY folder
            """, tx, ("$r", rootId));
        conn.Execute("UPDATE roots SET last_scan = $t WHERE id = $r", tx,
            ("$t", DateTime.UtcNow.ToString("O")), ("$r", rootId));
    }
}
