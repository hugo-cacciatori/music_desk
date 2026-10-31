using Microsoft.Data.Sqlite;
using Refrain.Core.Index;

namespace Refrain.Core.Search;

/// <summary>What a folder holds, for the preview (SPEC §37 "Folders"): an <b>album folder</b> (every song below it has
/// one album, by <see cref="Names.AlbumKey"/>), an <b>artist folder</b> (one artist, several albums), or any other.</summary>
public enum PreviewKind { Album, Artist, Other }

/// <summary>A song of a folder's preview: its number, title and length.</summary>
public sealed record FolderSong(long Id, int? Disc, int? Track, string Title, double? Seconds);

/// <summary>An album of an artist folder's preview: its name, year and songs.</summary>
public sealed record FolderAlbum(string Name, string? Year, int Songs);

/// <summary>The preview of a folder (SPEC §37): its kind, the album's or artist's name, the songs (copies of a song in
/// the same album counted once, as the discography view counts them), the albums, the artists, how many songs have
/// ReplayGain tags, and the folder's path.</summary>
public sealed record FolderPreview(PreviewKind Kind, string Name, string? Artist, string? Year, IReadOnlyList<FolderSong> Songs,
    IReadOnlyList<FolderAlbum> Albums, IReadOnlyList<string> Artists, int WithReplayGain, string? Path)
{
    /// <summary>Null when no track is indexed below the folder.</summary>
    public static FolderPreview? Read(SqliteConnection conn, long folderId)
    {
        var rows = new List<(long Id, string? Artist, string Album, string Title, string? Date, int? Disc, int? Track, double? Seconds, bool Gain)>();
        using (var cmd = conn.CreateCommand())
        {
            cmd.CommandText = """
                WITH RECURSIVE down(id) AS (
                    SELECT $id UNION ALL SELECT e.id FROM entries e JOIN down d ON e.parent_id = d.id
                )
                SELECT t.entry_id, COALESCE(NULLIF(TRIM(t.albumartist), ''), NULLIF(TRIM(t.artist), '')), COALESCE(t.album, ''),
                       COALESCE(NULLIF(TRIM(t.title), ''), e.name), t.date, t.disc, t.track, t.duration,
                       t.rg_track_gain IS NOT NULL OR t.rg_album_gain IS NOT NULL
                FROM tracks t JOIN down ON down.id = t.entry_id JOIN entries e ON e.id = t.entry_id
                WHERE t.read_error IS NULL
                ORDER BY t.entry_id
                """;
            cmd.Parameters.AddWithValue("$id", folderId);
            using var r = cmd.ExecuteReader();
            while (r.Read())
                rows.Add((r.GetInt64(0), r.IsDBNull(1) ? null : r.GetString(1), r.GetString(2), r.GetString(3),
                    r.IsDBNull(4) ? null : r.GetString(4), r.IsDBNull(5) ? null : r.GetInt32(5), r.IsDBNull(6) ? null : r.GetInt32(6),
                    r.IsDBNull(7) ? null : r.GetDouble(7), r.GetInt64(8) != 0));
        }
        if (rows.Count == 0) return null;

        // One song per album, disc, number and title, as the discography view counts them.
        var seen = new HashSet<(string, int, int?, string)>();
        var songs = rows.Where(s => seen.Add((Names.AlbumKey(s.Album), s.Disc ?? 1, s.Track, Names.Key(s.Title))))
            .OrderBy(s => Names.AlbumKey(s.Album), StringComparer.Ordinal).ThenBy(s => s.Disc ?? 1).ThenBy(s => s.Track ?? int.MaxValue)
            .ThenBy(s => s.Title, NaturalOrder.Instance).ToList();
        var artistKeys = rows.Where(s => s.Artist is not null).GroupBy(s => Names.Key(s.Artist)).Where(g => g.Key.Length > 0).ToList();
        var albums = rows.GroupBy(s => Names.AlbumKey(s.Album)).ToList();
        var artists = artistKeys.Select(g => Names.Shown(g.Select(s => s.Artist!))).OrderBy(n => n, NaturalOrder.Instance).ToList();
        var kind = albums.Count == 1 && albums[0].Key.Length > 0 ? PreviewKind.Album
            : artistKeys.Count == 1 && albums.Count > 1 ? PreviewKind.Artist
            : PreviewKind.Other;
        static string? Year(string? date) => date is { Length: >= 4 } && date[..4].All(char.IsAsciiDigit) ? date[..4] : null;
        var path = Queries.PathOf(conn, folderId);
        var folderName = System.IO.Path.GetFileName(path?.TrimEnd('\\') ?? "") is { Length: > 0 } n ? n : path ?? "";
        var name = kind switch
        {
            PreviewKind.Album => Names.Shown(albums[0].Select(s => Names.WithoutMarkers(s.Album))),
            PreviewKind.Artist => artists[0],
            _ => folderName,
        };
        var albumList = kind == PreviewKind.Artist
            ? albums.Select(g => new FolderAlbum(g.Key.Length > 0 ? Names.Shown(g.Select(s => Names.WithoutMarkers(s.Album))) : "Other songs",
                    g.Select(s => Year(s.Date)).Where(y => y is not null).Order(StringComparer.Ordinal).FirstOrDefault(),
                    songs.Count(s => Names.AlbumKey(s.Album) == g.Key)))
                .OrderBy(a => a.Year is null).ThenBy(a => a.Year, StringComparer.Ordinal).ThenBy(a => a.Name, NaturalOrder.Instance).ToList()
            : [];
        return new FolderPreview(kind, name, artists.Count == 1 ? artists[0] : null,
            kind == PreviewKind.Album ? rows.Select(s => Year(s.Date)).Where(y => y is not null).Order(StringComparer.Ordinal).FirstOrDefault() : null,
            songs.Select(s => new FolderSong(s.Id, s.Disc, s.Track, s.Title, s.Seconds)).ToList(), albumList, artists,
            songs.Count(s => s.Gain), path);
    }
}
