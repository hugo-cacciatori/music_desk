using Refrain.Core;
using Refrain.Core.Playback;
using Refrain.Core.Search;
using Xunit;

namespace Refrain.Tests;

/// <summary>The preview (SPEC §37): what kind of folder was clicked and what it shows, the excerpt's window and fades,
/// and ratings tied to tags rather than files.</summary>
public sealed class PreviewTests : IDisposable
{
    readonly HandIndex lib = new();

    public void Dispose() => lib.Dispose();

    long Song(long folder, string title, string album, string? date, int track, string artist = "Björk")
    {
        var id = lib.Track(folder, $"{track:00} {title}.flac", title: title, artist: artist, album: album, date: date);
        lib.Scalar("UPDATE tracks SET track = $n, duration = 200 WHERE entry_id = $id", ("$n", track), ("$id", id));
        return id;
    }

    [Fact]
    public void A_folder_previews_as_an_album_an_artist_or_what_it_holds()
    {
        var artist = lib.Folder(lib.Top, "Björk");
        var homogenic = lib.Folder(artist, "Homogenic");
        Song(homogenic, "Hunter", "Homogenic (CD1)", "1997", 1);
        Song(lib.Folder(homogenic, "CD2"), "Jóga", "Homogenic (CD2)", "1997-09-22", 2);
        Song(lib.Folder(homogenic, "lite"), "Hunter", "Homogenic", "1997", 1);  // a copy counts once
        Song(lib.Folder(artist, "Debut"), "Human Behaviour", "Debut", "1993", 1);
        var mixed = lib.Folder(lib.Top, "Mixed");
        Song(mixed, "Glove", "Amnesiac", "2001", 1, artist: "Radiohead");
        Song(mixed, "Venus", "Debut", "1993", 2);

        var album = FolderPreview.Read(lib.Conn, homogenic)!;
        Assert.Equal((FolderKind.Album, "Homogenic", "Björk", "1997"), (album.Kind, album.Name, album.Artist, album.Year));
        Assert.Equal(["Hunter", "Jóga"], album.Songs.Select(s => s.Title));
        var discography = FolderPreview.Read(lib.Conn, artist)!;
        Assert.Equal((FolderKind.Artist, "Björk"), (discography.Kind, discography.Name));
        Assert.Equal(["Debut 1993 1", "Homogenic 1997 2"], discography.Albums.Select(a => $"{a.Name} {a.Year} {a.Songs}"));
        var other = FolderPreview.Read(lib.Conn, mixed)!;
        Assert.Equal((FolderKind.Other, "Mixed", 2), (other.Kind, other.Name, other.Songs.Count));
        Assert.Equal(["Björk", "Radiohead"], other.Artists);
        Assert.Null(FolderPreview.Read(lib.Conn, lib.Folder(lib.Top, "Scans")));
    }

    [Fact]
    public void Search_leaves_out_folders_without_songs()
    {
        var album = lib.Folder(lib.Top, "Scans of Homogenic");
        lib.Folder(album, "Scans");
        Song(album, "Hunter", "Homogenic", "1997", 1);
        lib.Scalar("INSERT INTO folder_stats (entry_id, n_tracks, duration) VALUES ($id, 1, 200)", ("$id", album));
        var found = LibrarySearch.Find(lib.Conn, SearchQuery.Parse("scans"));
        Assert.Equal([album], found.Ids);  // the Scans folder, with no song below it, is left out
    }

    [Fact]
    public void The_excerpt_is_thirty_seconds_from_a_third_in_with_fades()
    {
        var long_ = Excerpt.For(TimeSpan.FromSeconds(300));
        Assert.Equal((100, 30, 2), (long_.Start.TotalSeconds, long_.Length.TotalSeconds, long_.Fade.TotalSeconds));
        Assert.Equal(0, long_.Level(TimeSpan.FromSeconds(100)));
        Assert.Equal(0.5, long_.Level(TimeSpan.FromSeconds(101)), 3);
        Assert.Equal(1, long_.Level(TimeSpan.FromSeconds(115)));
        Assert.Equal(0.5, long_.Level(TimeSpan.FromSeconds(129)), 3);
        Assert.True(long_.Over(TimeSpan.FromSeconds(130)));
        Assert.Equal(15, Excerpt.For(TimeSpan.FromSeconds(45)).Start.TotalSeconds);  // never past the end
        var short_ = Excerpt.For(TimeSpan.FromSeconds(12));  // played whole, with shorter fades
        Assert.Equal((0, 12, 2), (short_.Start.TotalSeconds, short_.Length.TotalSeconds, short_.Fade.TotalSeconds));
        Assert.Equal(1, Excerpt.For(TimeSpan.FromSeconds(6)).Fade.TotalSeconds);
    }

    [Fact]
    public void Ratings_follow_the_tags_and_survive_a_restart()
    {
        using var temp = new TempFolder();
        var file = System.IO.Path.Combine(temp.Path, "ratings.json");
        var ratings = new Ratings(file);
        var key = Ratings.Key("Björk", "Homogenic (CD1)", "Jóga")!;
        Assert.Equal(key, Ratings.Key("BJORK", "Homogenic", "joga"));  // grouped as the discography view groups names
        Assert.Null(Ratings.Key(null, "Homogenic", "Jóga"));
        ratings.Set(key, new Rating(4.3, "the strings"));
        Assert.Equal(new Rating(4.5, "the strings"), new Ratings(file).Get(key));  // halves only, kept in the file
        ratings.Set(key, Rating.None);
        Assert.Equal(Rating.None, new Ratings(file).Get(key));
    }
}
