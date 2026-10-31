using Microsoft.Data.Sqlite;
using Refrain.Core.Index;
using Refrain.Core.Lyrics;

namespace Refrain.Core.Search;

/// <summary>A song of the discography view: its title and its lyrics as LRCLIB gave them (null with a reason when there
/// are none), synced too when LRCLIB has them timed (the sung line, SPEC §37).</summary>
public sealed record AlbumSong(long Id, string Title, int? Disc, int? Track, string? Lyrics, string Note, string? Synced = null);

/// <summary>An album, by its grouping key (<see cref="Names.AlbumKey"/>) and the name it shows.</summary>
public sealed record ArtistAlbum(string Key, string Name, string? Year, IReadOnlyList<AlbumSong> Songs);

/// <summary>An artist of the library level (SPEC §37): its key (<see cref="Names.Key"/>), the name it shows, its albums
/// and songs.</summary>
public sealed record LibraryArtist(string Key, string Name, int Albums, int Songs);

/// <summary>The discography view's data (SPEC §36 "11.", §37 "The library by tags"): an artist's albums, rebuilt from the
/// tags of every track in the library wherever the files are, earliest first, each with its songs and their lyrics;
/// <see cref="StartAlbum"/> and <see cref="StartSong"/> say where to open. The artist is a track's album artist, else its
/// artist, grouped regardless of case, accents and punctuation (<see cref="Names"/>).</summary>
public sealed record ArtistAlbums(string Key, string Artist, IReadOnlyList<ArtistAlbum> Albums, int StartAlbum, int StartSong)
{
    const string ArtistColumn = "COALESCE(NULLIF(TRIM(albumartist), ''), NULLIF(TRIM(artist), ''))";

    /// <summary>The discography of the track <paramref name="entryId"/>'s artist, opened on that track; null when it
    /// has no artist.</summary>
    public static ArtistAlbums? For(SqliteConnection conn, long entryId)
    {
        var artist = conn.Scalar($"SELECT {ArtistColumn} FROM tracks WHERE entry_id = $id", null, ("$id", entryId)) as string;
        var key = Names.Key(artist);
        return key.Length == 0 ? null : ForArtist(conn, key, entryId);
    }

    /// <summary>The discography of the artist <paramref name="key"/>, opened on <paramref name="start"/>'s song (else on
    /// the first album); null when no track has that artist. Albums are grouped by <see cref="Names.AlbumKey"/> (a copy
    /// of an album in a subfolder adds no songs: a disc, number and title already there is left out), ordered by year
    /// (an album with no year last), then name.</summary>
    public static ArtistAlbums? ForArtist(SqliteConnection conn, string key, long? start = null)
    {
        // Matched in .NET: SQLite folds neither accents nor punctuation.
        var ids = new List<long>();
        var spellings = new List<string>();
        using (var cmd = conn.CreateCommand())
        {
            cmd.CommandText = $"SELECT entry_id, {ArtistColumn} FROM tracks ORDER BY entry_id";
            using var r = cmd.ExecuteReader();
            while (r.Read())
                if (!r.IsDBNull(1) && Names.Key(r.GetString(1)) == key)
                {
                    ids.Add(r.GetInt64(0));
                    spellings.Add(r.GetString(1));
                }
        }
        if (ids.Count == 0) return null;

        var rows = new List<(long Id, string Title, string Album, string? Date, int? Disc, int? Track, string? Status, string? Plain, string? Synced)>();
        foreach (var chunk in ids.Chunk(500))
        {
            using var cmd = conn.CreateCommand();
            cmd.CommandText = $"""
                SELECT t.entry_id, COALESCE(NULLIF(TRIM(t.title), ''), e.name), COALESCE(NULLIF(TRIM(t.album), ''), ''),
                       t.date, t.disc, t.track, l.status, l.plain, l.synced
                FROM tracks t
                JOIN entries e ON e.id = t.entry_id
                LEFT JOIN lyrics l ON l.key = t.lyrics_key
                WHERE t.entry_id IN ({string.Join(",", chunk)})
                ORDER BY e.id
                """;
            using var r = cmd.ExecuteReader();
            while (r.Read())
                rows.Add((r.GetInt64(0), r.GetString(1), r.GetString(2), r.IsDBNull(3) ? null : r.GetString(3),
                    r.IsDBNull(4) ? null : r.GetInt32(4), r.IsDBNull(5) ? null : r.GetInt32(5),
                    r.IsDBNull(6) ? null : r.GetString(6), r.IsDBNull(7) ? null : r.GetString(7),
                    r.IsDBNull(8) ? null : r.GetString(8)));
        }

        var albums = rows.GroupBy(row => Names.AlbumKey(row.Album), StringComparer.Ordinal).Select(group =>
        {
            var seen = new HashSet<(int?, int?, string)>();
            var songs = group
                .OrderBy(s => s.Disc ?? 1).ThenBy(s => s.Track ?? int.MaxValue).ThenBy(s => s.Title, NaturalOrder.Instance)
                .Where(s => seen.Add((s.Disc ?? 1, s.Track, Names.Key(s.Title))) || s.Id == start)
                .Select(s => new AlbumSong(s.Id, s.Title, s.Disc, s.Track, Text(s.Status, s.Plain), Note(s.Status, s.Plain),
                    s.Status == LyricsEntry.Found && !string.IsNullOrWhiteSpace(s.Synced) ? s.Synced : null))
                .ToList();
            var year = group.Select(s => Year(s.Date)).Where(y => y is not null).Order(StringComparer.Ordinal).FirstOrDefault();
            var name = group.Key.Length > 0 ? Names.Shown(group.Select(s => Names.WithoutMarkers(s.Album))) : "Other songs";
            return new ArtistAlbum(group.Key, name, year, songs);
        })
            .OrderBy(a => a.Year is null).ThenBy(a => a.Year, StringComparer.Ordinal).ThenBy(a => a.Name, NaturalOrder.Instance)
            .ToList();
        var album = start is { } id ? albums.FindIndex(a => a.Songs.Any(s => s.Id == id)) : 0;
        if (album < 0) album = 0;
        var song = start is { } id2 ? Math.Max(0, albums[album].Songs.ToList().FindIndex(s => s.Id == id2)) : 0;
        return new ArtistAlbums(key, Names.Shown(spellings), albums, album, song);
    }

    /// <summary>Every artist of the library (the library level, SPEC §37), by name; songs counted as the artist level
    /// counts them (a copy of a song in the same album counts once).</summary>
    public static List<LibraryArtist> Library(SqliteConnection conn)
    {
        var groups = new Dictionary<string, (List<string> Spellings, HashSet<string> Albums, HashSet<(string, int, int?, string)> Songs)>(StringComparer.Ordinal);
        using (var cmd = conn.CreateCommand())
        {
            cmd.CommandText = $"""
                SELECT {ArtistColumn}, COALESCE(t.album, ''), t.disc, t.track, COALESCE(NULLIF(TRIM(t.title), ''), e.name)
                FROM tracks t JOIN entries e ON e.id = t.entry_id
                ORDER BY t.entry_id
                """;
            using var r = cmd.ExecuteReader();
            while (r.Read())
            {
                if (r.IsDBNull(0)) continue;
                var name = r.GetString(0);
                var key = Names.Key(name);
                if (key.Length == 0) continue;
                if (!groups.TryGetValue(key, out var group)) groups[key] = group = ([], [], []);
                var albumKey = Names.AlbumKey(r.GetString(1));
                group.Spellings.Add(name);
                group.Albums.Add(albumKey);
                group.Songs.Add((albumKey, r.IsDBNull(2) ? 1 : r.GetInt32(2), r.IsDBNull(3) ? null : r.GetInt32(3), Names.Key(r.GetString(4))));
            }
        }
        return groups.Select(g => new LibraryArtist(g.Key, Names.Shown(g.Value.Spellings), g.Value.Albums.Count, g.Value.Songs.Count))
            .OrderBy(a => a.Key, StringComparer.Ordinal).ToList();
    }

    /// <summary>What Open in Refrain opens on a folder (SPEC §37), from the tracks below it: one album of one artist
    /// opens that album on its first song; several albums of one artist open the artist's albums (<c>AllAlbums</c>);
    /// several artists, none (<c>Artists</c> lists their keys, for the library level).</summary>
    public static (ArtistAlbums? View, bool AllAlbums, IReadOnlyList<string> Artists) ForFolder(SqliteConnection conn, long folderId)
    {
        var tracks = new List<(long Id, string Artist, string Album)>();
        using (var cmd = conn.CreateCommand())
        {
            cmd.CommandText = $"""
                WITH RECURSIVE down(id) AS (
                    SELECT $id UNION ALL SELECT e.id FROM entries e JOIN down d ON e.parent_id = d.id
                )
                SELECT t.entry_id, {ArtistColumn}, COALESCE(t.album, '')
                FROM tracks t JOIN down ON down.id = t.entry_id
                ORDER BY t.entry_id
                """;
            cmd.Parameters.AddWithValue("$id", folderId);
            using var r = cmd.ExecuteReader();
            while (r.Read())
                if (!r.IsDBNull(1) && Names.Key(r.GetString(1)) is { Length: > 0 } key)
                    tracks.Add((r.GetInt64(0), key, Names.AlbumKey(r.GetString(2))));
        }
        var artists = tracks.Select(t => t.Artist).Distinct().ToList();
        if (artists.Count != 1) return (null, false, artists);
        var albumKeys = tracks.Select(t => t.Album).Distinct().ToList();
        if (ForArtist(conn, artists[0], tracks[0].Id) is not { } view) return (null, false, artists);
        return albumKeys.Count == 1 ? (view with { StartSong = 0 }, false, artists) : (view, true, artists);
    }

    /// <summary>The folder that holds every one of these tracks (their nearest common folder), for Open in File
    /// Explorer on an album or an artist; null when they have none in common (two drives).</summary>
    public static string? CommonFolder(SqliteConnection conn, IEnumerable<long> trackIds)
    {
        var folders = trackIds.Select(id => Queries.ParentOf(conn, id)).Where(p => p is not null).Distinct()
            .Select(p => Queries.PathOf(conn, p!.Value)).Where(p => p is not null).Select(p => p!).ToList();
        if (folders.Count == 0) return null;
        var common = folders[0].TrimEnd('\\').Split('\\');
        var length = common.Length;
        foreach (var folder in folders.Skip(1))
        {
            var parts = folder.TrimEnd('\\').Split('\\');
            var same = 0;
            while (same < Math.Min(length, parts.Length) && string.Equals(parts[same], common[same], StringComparison.OrdinalIgnoreCase)) same++;
            length = same;
        }
        if (length == 0) return null;
        var path = string.Join('\\', common.Take(length));
        return path.EndsWith(':') ? path + "\\" : path;
    }

    /// <summary>The year at the start of a date tag ("1997-09-22", "1997"), or null.</summary>
    static string? Year(string? date) =>
        date is { Length: >= 4 } && date[..4].All(char.IsAsciiDigit) ? date[..4] : null;

    static string? Text(string? status, string? plain) =>
        status == LyricsEntry.Found && !string.IsNullOrWhiteSpace(plain) ? plain.Replace("\r\n", "\n").Trim() : null;

    /// <summary>What shows in the lyrics' place when there are none.</summary>
    static string Note(string? status, string? plain) => status switch
    {
        LyricsEntry.Found when !string.IsNullOrWhiteSpace(plain) => "",
        LyricsEntry.Instrumental => "An instrumental",
        LyricsEntry.NotFound => "No lyrics found",
        _ => "Not looked up yet",
    };
}
