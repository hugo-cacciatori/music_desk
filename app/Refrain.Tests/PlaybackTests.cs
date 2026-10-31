using Refrain.Core;
using Refrain.Core.Playback;
using Xunit;

namespace Refrain.Tests;

/// <summary>The player's plain rules (SPEC §37 "The player"): the album's order, looping or once, busy songs skipped;
/// the sung line from LRCLIB's synced lyrics; the Player page's settings.</summary>
public sealed class PlaybackTests
{
    [Fact]
    public void The_album_goes_on_loops_or_stops_and_never_plays_a_busy_song()
    {
        static bool All(int _) => true;
        Assert.Equal(1, PlayQueue.Next(3, 0, AlbumEnd.Loop, All));
        Assert.Equal(0, PlayQueue.Next(3, 2, AlbumEnd.Loop, All));  // after the last, the first again
        Assert.Null(PlayQueue.Next(3, 2, AlbumEnd.Once, All));  // played once: it stops
        Assert.Equal(2, PlayQueue.Next(3, 0, AlbumEnd.Loop, i => i != 1));  // a song a task holds is skipped
        Assert.Equal(0, PlayQueue.Next(1, 0, AlbumEnd.Loop, All));  // a one-song album loops on itself
        Assert.Null(PlayQueue.Next(3, 0, AlbumEnd.Loop, _ => false));  // nothing may play
        Assert.Equal(2, PlayQueue.Previous(3, 0, AlbumEnd.Loop, All));
        Assert.Null(PlayQueue.Previous(3, 0, AlbumEnd.Once, All));
        Assert.Equal(0, PlayQueue.Previous(3, 2, AlbumEnd.Once, i => i != 1));
    }

    [Fact]
    public void Synced_lyrics_give_the_line_sung_at_each_moment()
    {
        var lines = SyncedLyrics.Parse("[ar: Björk]\n[00:12.50] Emotional landscapes\r\n[00:20.00][01:05.10]They puzzle me\n[00:30.1]\nuntimed");
        Assert.Equal(["0:12.5 Emotional landscapes", "0:20 They puzzle me", "0:30.1 ", "1:05.1 They puzzle me"],
            lines.Select(l => $"{l.At:m\\:ss\\.FFF} {l.Text}".Replace(". ", " ")));
        Assert.Equal(-1, SyncedLyrics.IndexAt(lines, TimeSpan.FromSeconds(3)));
        Assert.Equal(0, SyncedLyrics.IndexAt(lines, TimeSpan.FromSeconds(12.5)));
        Assert.Equal(1, SyncedLyrics.IndexAt(lines, TimeSpan.FromSeconds(29)));
        Assert.Equal(3, SyncedLyrics.IndexAt(lines, TimeSpan.FromMinutes(4)));
        Assert.Empty(SyncedLyrics.Parse("Just plain words"));
    }

    [Fact]
    public void The_player_page_reads_unknown_values_as_its_defaults()
    {
        var settings = new Settings();
        Assert.Equal((AlbumEnd.Loop, BusyFolder.Stop, true), (settings.GetAlbumEnd(), settings.GetBusyFolder(), settings.PlayerKeep));
        settings.PlayerAtEnd = "once";
        settings.PlayerOnBusy = "skip";
        Assert.Equal((AlbumEnd.Once, BusyFolder.Skip), (settings.GetAlbumEnd(), settings.GetBusyFolder()));
        settings.PlayerAtEnd = "sometimes";
        Assert.Equal(AlbumEnd.Loop, settings.GetAlbumEnd());
    }
}

/// <summary>ReplayGain around the player (SPEC §37): the albums a scan's end asks about, from the index alone.</summary>
public sealed class MissingGainTests : IDisposable
{
    readonly HandIndex lib = new();

    public void Dispose() => lib.Dispose();

    [Fact]
    public void A_scan_asks_about_the_albums_without_their_tags_as_replaygain_sees_albums()
    {
        var options = Refrain.Core.Options.ReplayGainOptions.Default;
        var tagged = lib.Folder(lib.Top, "Tagged");
        lib.Track(tagged, "01.flac", rg: 4);
        var half = lib.Folder(lib.Top, "Half");
        lib.Track(half, "01.flac", rg: 4);
        lib.Track(half, "02.flac");
        var discs = lib.Folder(lib.Top, "Discs");
        lib.Track(discs, "01.flac", rg: 4);
        lib.Track(lib.Folder(discs, "CD2"), "01.flac");  // a disc measured with its album
        var copies = lib.Folder(lib.Top, "Copies");
        lib.Track(copies, "01.flac", rg: 4);
        lib.Track(lib.Folder(copies, "lite"), "01.flac");  // a copy: never counted
        var image = lib.Folder(lib.Top, "Image");
        lib.Track(image, "album.flac");
        lib.Unread(image, "album.cue");  // an image waiting to be split
        lib.Unread(lib.Folder(lib.Top, "Unread"), "01.flac");  // tags not read yet: nothing known

        Assert.Equal([@"C:\Music\Discs", @"C:\Music\Half"], Refrain.Core.ReplayGain.Missing.AlbumsWithoutTags(lib.Conn, options));
    }

    [Fact]
    public void A_gain_tag_lowers_the_volume_and_never_raises_it()
    {
        Assert.Equal(-6.52, Refrain.Core.ReplayGain.Missing.Gain("-6.52 dB"));
        Assert.Equal(2.1, Refrain.Core.ReplayGain.Missing.Gain("+2.10 dB"));
        Assert.Null(Refrain.Core.ReplayGain.Missing.Gain("loud"));
        Assert.Equal(0.5, Refrain.Core.ReplayGain.Missing.Factor(-6.0206), 3);
        Assert.Equal(1, Refrain.Core.ReplayGain.Missing.Factor(3));
        Assert.Equal(1, Refrain.Core.ReplayGain.Missing.Factor(null));
    }
}
