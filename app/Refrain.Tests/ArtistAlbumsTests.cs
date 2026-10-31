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
    public void Open_in_Refrain_opens_a_folder_as_its_album_or_its_artist()
    {
        var artist = lib.Folder(lib.Top, "Björk");
        var homogenic = lib.Folder(artist, "Homogenic");
        Song(homogenic, "Jóga", "Homogenic", "1997", 2);
        Song(lib.Folder(homogenic, "CD2"), "Hunter", "HOMOGENIC", "1997", 1);
        Song(lib.Folder(artist, "Debut"), "Human Behaviour", "Debut", "1993", 1);
        var mixed = lib.Folder(lib.Top, "Mixed");
        Song(mixed, "Glove", "Amnesiac", "2001", 1, artist: "Radiohead");
        Song(mixed, "Venus", "Debut", "1993", 2);

        var album = ArtistAlbums.ForFolder(lib.Conn, homogenic);  // one album, its subfolders with it: zoomed on its first song
        Assert.False(album.AllAlbums);
        Assert.Equal(("Homogenic", 0), (album.View!.Albums[album.View.StartAlbum].Name, album.View.StartSong));
        var discography = ArtistAlbums.ForFolder(lib.Conn, artist);  // an artist's albums
        Assert.True(discography.AllAlbums);
        Assert.Equal(["Debut", "Homogenic"], discography.View!.Albums.Select(a => a.Name));
        var several = ArtistAlbums.ForFolder(lib.Conn, mixed);  // several artists: the library level, those lit
        Assert.Null(several.View);
        Assert.Equal(["radiohead", "bjork"], several.Artists);
        var empty = ArtistAlbums.ForFolder(lib.Conn, lib.Folder(lib.Top, "Empty"));  // nothing indexed below it yet
        Assert.Equal((null, 0), (empty.View, empty.Artists.Count));
    }

    [Fact]
    public void Artists_and_albums_are_grouped_by_their_names_wherever_their_files_are()
    {
        // SPEC §37: case, accents and punctuation don't split an artist; disc and edition markers don't split an album.
        var a = lib.Folder(lib.Top, "A");
        var b = lib.Folder(lib.Folder(lib.Top, "Elsewhere"), "B");
        Song(a, "Emerald Sword", "Symphony of Enchanted Lands (CD1)", "1998", 1, artist: "Rhapsody of Fire");
        Song(b, "Wisdom of the Kings", "Symphony of Enchanted Lands - Disc 2", "1998", 1, artist: "RHAPSODY OF FIRE");
        Song(b, "Holy Thunderforce", "Dawn of Victory [Remastered]", "2000", 1, artist: "Rhapsody Of Fire");
        Song(a, "Glove", "Amnesiac", "2001", 1, artist: "Radiohead");

        var found = ArtistAlbums.ForArtist(lib.Conn, Names.Key("rhapsody-of-fire"))!;
        Assert.Equal("Rhapsody of Fire", found.Artist);  // the first of the most frequent spellings
        Assert.Equal(["Symphony of Enchanted Lands", "Dawn of Victory"], found.Albums.Select(x => x.Name));
        Assert.Equal(["Emerald Sword", "Wisdom of the Kings"], found.Albums[0].Songs.Select(x => x.Title));
        Assert.Equal((0, 0), (found.StartAlbum, found.StartSong));
        Assert.Equal(["Radiohead 1 1", "Rhapsody of Fire 2 3"], ArtistAlbums.Library(lib.Conn).Select(x => $"{x.Name} {x.Albums} {x.Songs}"));
        Assert.Equal(@"C:\Music", ArtistAlbums.CommonFolder(lib.Conn, found.Albums.SelectMany(x => x.Songs).Select(x => x.Id)));
        Assert.Equal(@"C:\Music\Elsewhere\B", ArtistAlbums.CommonFolder(lib.Conn, found.Albums[1].Songs.Select(x => x.Id)));
    }

    [Fact]
    public void Names_ignore_case_accents_punctuation_and_album_markers()
    {
        Assert.Equal("ac dc", Names.Key("AC/DC"));
        Assert.Equal(Names.Key("Björk"), Names.Key("BJORK"));
        Assert.Equal("guns n roses", Names.Key("Guns N' Roses"));
        Assert.Equal("", Names.Key(" ?? "));
        Assert.Equal("Homogenic", Names.WithoutMarkers("Homogenic (CD 1)"));
        Assert.Equal("Homogenic", Names.WithoutMarkers("Homogenic [Remastered 2004] (Disc 2)"));
        Assert.Equal("Homogenic", Names.WithoutMarkers("Homogenic - CD2"));
        Assert.Equal("Homogenic", Names.WithoutMarkers("Homogenic (2011 Remaster)"));
        Assert.Equal("(What's the Story) Morning Glory?", Names.WithoutMarkers("(What's the Story) Morning Glory?"));
        Assert.Equal("CD1", Names.WithoutMarkers("CD1"));  // only a marker: kept
        Assert.Equal("Rhapsody of Fire", Names.Shown(["RHAPSODY OF FIRE", "Rhapsody of Fire", "Rhapsody of Fire"]));
    }

    [Fact]
    public void A_track_without_an_artist_has_no_discography()
    {
        var id = lib.Track(lib.Folder(lib.Top, "Loose"), "untitled.flac");
        Assert.Null(ArtistAlbums.For(lib.Conn, id));
    }
}
