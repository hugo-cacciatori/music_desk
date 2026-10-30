using Refrain.Core.Index;
using Refrain.Core.Lyrics;
using Refrain.Core.Search;
using Xunit;

namespace Refrain.Tests;

/// <summary>The discography view's data (SPEC §36 "11."): an artist's albums from the index, earliest first, with
/// their songs in order and their lyrics.</summary>
public sealed class ArtistAlbumsTests : IDisposable
{
    readonly HandIndex lib = new();

    public void Dispose() => lib.Dispose();

    long Song(long folder, string title, string album, string? date, int track, string? key = null, string artist = "Björk",
        string? albumArtist = null)
    {
        var id = lib.Track(folder, $"{track:00} {title}.flac", title: title, artist: artist, album: album, date: date, key: key,
            albumArtist: albumArtist);
        lib.Scalar("UPDATE tracks SET track = $n WHERE entry_id = $id", ("$n", track), ("$id", id));
        return id;
    }

    void Lyrics(string key, LyricsEntry entry)
    {
        using var tx = lib.Conn.BeginTransaction();
        LyricsIndex.Record(lib.Conn, tx, key, entry);
        tx.Commit();
    }

    [Fact]
    public void An_artists_albums_come_earliest_first_with_their_songs_and_lyrics()
    {
        var homogenic = lib.Folder(lib.Top, "Homogenic");
        var joga = Song(homogenic, "Jóga", "Homogenic", "1997-09-22", 2, key: "k-joga");
        Song(homogenic, "Hunter", "Homogenic", "1997", 1, key: "k-hunter");
        var lite = lib.Folder(homogenic, "lite");
        Song(lite, "Hunter", "Homogenic", "1997", 1);  // a copy adds no song
        var debut = lib.Folder(lib.Top, "Debut");
        Song(debut, "Human Behaviour", "Debut", "1993", 1);
        var live = lib.Folder(lib.Top, "Live");
        Song(live, "Hyperballad", "Live Box", null, 1);
        Song(live, "Army of Me", "Live Box", null, 2, artist: "Someone else", albumArtist: "BJÖRK");  // the album artist counts
        Song(lib.Folder(lib.Top, "Other"), "Glove", "Amnesiac", "2001", 1, artist: "Radiohead");
        Lyrics("k-joga", new LyricsEntry(LyricsEntry.Found, 1, "Emotional landscapes\r\nThey puzzle me", null, "2026-10-30"));
        Lyrics("k-hunter", new LyricsEntry(LyricsEntry.NotFound, Checked: "2026-10-30"));

        var found = ArtistAlbums.For(lib.Conn, joga)!;
        Assert.Equal("Björk", found.Artist);
        Assert.Equal(["Debut 1993", "Homogenic 1997", "Live Box "], found.Albums.Select(a => $"{a.Name} {a.Year}"));
        var homogenicAlbum = found.Albums[found.StartAlbum];
        Assert.Equal("Homogenic", homogenicAlbum.Name);
        Assert.Equal(["Hunter", "Jóga"], homogenicAlbum.Songs.Select(s => s.Title));
        Assert.Equal(1, found.StartSong);
        Assert.Equal("Emotional landscapes\nThey puzzle me", homogenicAlbum.Songs[1].Lyrics);
        Assert.Equal((null, "No lyrics found"), (homogenicAlbum.Songs[0].Lyrics, homogenicAlbum.Songs[0].Note));
        Assert.Equal(["Hyperballad", "Army of Me"], found.Albums[2].Songs.Select(s => s.Title));
        Assert.Equal("Not looked up yet", found.Albums[2].Songs[0].Note);
    }

    [Fact]
    public void A_track_without_an_artist_has_no_discography()
    {
        var id = lib.Track(lib.Folder(lib.Top, "Loose"), "untitled.flac");
        Assert.Null(ArtistAlbums.For(lib.Conn, id));
    }
}
