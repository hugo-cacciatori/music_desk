using Microsoft.Data.Sqlite;
using Refrain.Core.Lyrics;

namespace Refrain.Core.Search;

/// <summary>A song of the discography view: its title and its lyrics as LRCLIB gave them (null with a reason when there
/// are none).</summary>
public sealed record AlbumSong(long Id, string Title, int? Disc, int? Track, string? Lyrics, string Note);

public sealed record ArtistAlbum(string Name, string? Year, IReadOnlyList<AlbumSong> Songs);

/// <summary>The discography view's data (SPEC §36 "11."): an artist's albums in the library, earliest first, each with
/// its songs and their lyrics; <see cref="StartAlbum"/> and <see cref="StartSong"/> say where the double-clicked
/// result sits.</summary>
public sealed record ArtistAlbums(string Artist, IReadOnlyList<ArtistAlbum> Albums, int StartAlbum, int StartSong)
{
    /// <summary>The discography of the track <paramref name="entryId"/>'s artist (its album artist, else its artist),
    /// read from the index; null when the track has no artist. Albums are grouped by name (a copy of an album in a
    /// subfolder adds no songs: a disc, number and title already there is left out), ordered by year (an album with
    /// no year last), then name.</summary>
    public static ArtistAlbums? For(SqliteConnection conn, long entryId)
    {
        string? artist;
        using (var cmd = conn.CreateCommand())
        {
            cmd.CommandText = "SELECT COALESCE(NULLIF(TRIM(albumartist), ''), NULLIF(TRIM(artist), '')) FROM tracks WHERE entry_id = $id";
            cmd.Parameters.AddWithValue("$id", entryId);
            artist = cmd.ExecuteScalar() as string;
        }
        if (artist is null) return null;

        // The artist's tracks, matched in .NET: SQLite's NOCASE folds ASCII letters only ("BJÖRK" is "Björk").
        var ids = new List<long>();
        using (var cmd = conn.CreateCommand())
        {
            cmd.CommandText = "SELECT entry_id, COALESCE(NULLIF(TRIM(albumartist), ''), NULLIF(TRIM(artist), '')) FROM tracks";
            using var r = cmd.ExecuteReader();
            while (r.Read())
                if (!r.IsDBNull(1) && string.Equals(r.GetString(1), artist, StringComparison.OrdinalIgnoreCase)) ids.Add(r.GetInt64(0));
        }

        var rows = new List<(long Id, string Title, string Album, string? Date, int? Disc, int? Track, string? Status, string? Plain)>();
        foreach (var chunk in ids.Chunk(500))
        {
            using var cmd = conn.CreateCommand();
            cmd.CommandText = $"""
                SELECT t.entry_id, COALESCE(NULLIF(TRIM(t.title), ''), e.name), COALESCE(NULLIF(TRIM(t.album), ''), ''),
                       t.date, t.disc, t.track, l.status, l.plain
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
                    r.IsDBNull(6) ? null : r.GetString(6), r.IsDBNull(7) ? null : r.GetString(7)));
        }

        var albums = rows.GroupBy(row => row.Album, StringComparer.OrdinalIgnoreCase).Select(group =>
        {
            var seen = new HashSet<(int?, int?, string)>();
            var songs = group
                .OrderBy(s => s.Disc ?? 1).ThenBy(s => s.Track ?? int.MaxValue).ThenBy(s => s.Title, NaturalOrder.Instance)
                .Where(s => seen.Add((s.Disc ?? 1, s.Track, s.Title.ToLowerInvariant())) || s.Id == entryId)
                .Select(s => new AlbumSong(s.Id, s.Title, s.Disc, s.Track, Text(s.Status, s.Plain), Note(s.Status, s.Plain)))
                .ToList();
            var year = group.Select(s => Year(s.Date)).Where(y => y is not null).Order(StringComparer.Ordinal).FirstOrDefault();
            return new ArtistAlbum(group.Key.Length > 0 ? group.Key : "Other songs", year, songs);
        })
            .OrderBy(a => a.Year is null).ThenBy(a => a.Year, StringComparer.Ordinal).ThenBy(a => a.Name, NaturalOrder.Instance)
            .ToList();
        var album = albums.FindIndex(a => a.Songs.Any(s => s.Id == entryId));
        if (album < 0) return null;
        var song = albums[album].Songs.ToList().FindIndex(s => s.Id == entryId);
        return new ArtistAlbums(artist, albums, album, song);
    }

    /// <summary>What Open in Refrain opens on a folder (SPEC §37 step a), from the tracks below it: one album of one artist
    /// opens that album on its first song; several albums of one artist open the artist's albums (<c>AllAlbums</c>).
    /// Null view: no track below it yet, or tracks of <c>Artists</c> artists (the library level comes with step b).</summary>
    public static (ArtistAlbums? View, bool AllAlbums, int Artists) ForFolder(SqliteConnection conn, long folderId)
    {
        var tracks = new List<(long Id, string? Artist, string Album)>();
        using (var cmd = conn.CreateCommand())
        {
            cmd.CommandText = """
                WITH RECURSIVE down(id) AS (
                    SELECT $id UNION ALL SELECT e.id FROM entries e JOIN down d ON e.parent_id = d.id
                )
                SELECT t.entry_id, COALESCE(NULLIF(TRIM(t.albumartist), ''), NULLIF(TRIM(t.artist), '')),
                       COALESCE(NULLIF(TRIM(t.album), ''), '')
                FROM tracks t JOIN down ON down.id = t.entry_id
                ORDER BY t.entry_id
                """;
            cmd.Parameters.AddWithValue("$id", folderId);
            using var r = cmd.ExecuteReader();
            while (r.Read()) tracks.Add((r.GetInt64(0), r.IsDBNull(1) ? null : r.GetString(1), r.GetString(2)));
        }
        var artists = tracks.Where(t => t.Artist is not null).Select(t => t.Artist!).Distinct(StringComparer.OrdinalIgnoreCase).Count();
        if (artists != 1) return (null, false, artists);
        var first = tracks.First(t => t.Artist is not null);
        if (For(conn, first.Id) is not { } view) return (null, false, artists);
        var oneAlbum = tracks.Where(t => t.Artist is not null).Select(t => t.Album).Distinct(StringComparer.OrdinalIgnoreCase).Count() == 1;
        return oneAlbum ? (view with { StartSong = 0 }, false, 1) : (view, true, 1);
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
