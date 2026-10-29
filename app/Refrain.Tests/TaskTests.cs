using Refrain.Core;
using Refrain.Core.Options;
using Refrain.Core.ReplayGain;
using Xunit;

namespace Refrain.Tests;

/// <summary>What the task windows run (SPEC §31, §35): the folder locks, ReplayGain's five kinds with the real rsgain,
/// and Remove ReplayGain tags. On temporary copies of the fixtures.</summary>
public sealed class TaskTests : IDisposable
{
    readonly TempFolder temp = new();
    static readonly ReplayGainOptions Options = ReplayGainOptions.Default;

    public void Dispose() => temp.Dispose();

    string Artist()
    {
        var artist = Path.Combine(temp.Path, "Fixture Artist");
        Setup.Copy(Path.Combine(Setup.Fixtures, "replaygain", "Fixture Artist"), artist);
        return artist;
    }

    static string? AlbumGain(string path)
    {
        using var tags = RgTags.Open(path);
        return tags.Get(RgTags.AlbumGain);
    }

    ReplayGainOutcome Run(ReplayGainKind kind, params string[] paths) =>
        ReplayGainTask.Run(kind, paths, Options, TestContext.Current.CancellationToken, exe: ReplayGainTests.Rsgain);

    [Fact]
    public void Locks_refuse_overlaps_with_the_owners_sentence()
    {
        var locks = new PathLocks();
        var token = locks.Acquire([@"D:\Music\Kid A"], "ReplayGain");
        var inside = Assert.Throws<LockedException>(() => locks.Acquire([@"d:\music\kid a\01.flac"], "a tag save"));
        Assert.Equal("01.flac is busy: ReplayGain is running on it.", inside.Message);
        var above = Assert.Throws<LockedException>(() => locks.Acquire([@"D:\Music"], "ReplayGain"));
        Assert.Equal("Music is busy: ReplayGain is running on it.", above.Message);
        locks.Release(locks.Acquire([@"D:\Music\Kid A2"], "ReplayGain"));  // a name that only starts the same
        Assert.Equal("ReplayGain", locks.Conflict(@"D:\Music"));
        Assert.Equal(["ReplayGain"], locks.Owners());
        locks.Release(token);
        Assert.Null(locks.Conflict(@"D:\Music\Kid A"));
        Assert.Empty(locks.Owners());
    }

    [Fact]
    public void Selected_tracks_are_an_album_and_the_folders_other_tracks_stay_untouched()
    {
        var album = Path.Combine(Artist(), "Album A");
        var outcome = Run(ReplayGainKind.Tracks, Path.Combine(album, "01 Tone.flac"), Path.Combine(album, "03 Spike.mp3"));

        Assert.Equal(["01 Tone.flac", "03 Spike.mp3"], outcome.AlbumLogs.Single().Files);
        Assert.Null(AlbumGain(Path.Combine(album, "02 Spike.flac")));
        Assert.NotNull(AlbumGain(Path.Combine(album, "01 Tone.flac")));
        Assert.Equal(Path.Combine(album, "replaygain.log"), outcome.LogPath);
        Assert.DoesNotContain("02 Spike.flac", File.ReadAllText(outcome.LogPath!));  // not even as skipped
        Assert.Equal("Done: 1 album OK.", outcome.Message());
    }

    [Fact]
    public void Several_folders_are_each_an_album_with_its_own_log()
    {
        var artist = Artist();
        var outcome = Run(ReplayGainKind.Albums, Path.Combine(artist, "Album A"), Path.Combine(artist, "Album B"));

        Assert.Equal(2, outcome.AlbumLogs.Count);
        Assert.Null(outcome.LogPath);  // two logs: no single one to open
        Assert.True(File.Exists(Path.Combine(artist, "Album B", "replaygain.log")));
        Assert.False(File.Exists(Path.Combine(artist, "replaygain.log")));  // no leveling, no summary
        Assert.Equal("Done: 2 albums OK.", outcome.Message());
        Assert.Contains("Folder: " + Path.Combine(artist, "Album B"), outcome.Text());
    }

    [Fact]
    public void A_discography_says_where_every_album_was_leveled()
    {
        var artist = Artist();
        var outcome = Run(ReplayGainKind.Discography, artist);

        Assert.Equal(Path.Combine(artist, "replaygain.log"), outcome.LogPath);
        Assert.StartsWith("Done: 2 albums OK. Every album was leveled to ", outcome.Message());
        Assert.StartsWith(new string('=', 78) + "\nDiscography run ", outcome.Text());  // the summary first, then the albums
        Assert.Contains("Folder: " + Path.Combine(artist, "Album B"), outcome.Text());
    }

    [Fact]
    public void Loose_tracks_write_no_log_and_a_cancel_stops_before_the_next()
    {
        var album = Path.Combine(Artist(), "Album B");
        var tracks = new[] { Path.Combine(album, "01 Loud.flac"), Path.Combine(album, "Scans", "back.jpg") };
        var outcome = Run(ReplayGainKind.Loose, tracks);

        Assert.Null(outcome.LogPath);
        Assert.False(File.Exists(Path.Combine(album, "replaygain.log")));
        Assert.Equal("Done: 1 track OK, each on its own. 1 file left out.", outcome.Message());

        using var cancelled = new CancellationTokenSource();
        cancelled.Cancel();
        var stopped = ReplayGainTask.Run(ReplayGainKind.Albums, [album], Options, cancelled.Token, exe: ReplayGainTests.Rsgain);
        Assert.True(stopped.Cancelled);
        Assert.StartsWith("Stopped.", stopped.Message());
    }

    [Fact]
    public void Nothing_to_scan_says_so()
    {
        var empty = temp.Sub("Empty");
        Assert.Equal("Nothing was done: no audio file rsgain can scan here.", Run(ReplayGainKind.Album, empty).Message());
    }

    [Fact]
    public void Remove_clears_tracks_counts_those_without_and_refuses_busy_files()
    {
        var album = Path.Combine(Artist(), "Album A");
        Run(ReplayGainKind.Album, album);
        var locks = new PathLocks();
        var busy = Path.Combine(album, "03 Spike.mp3");
        var token = locks.Acquire([busy], "ReplayGain");
        var cover = Path.Combine(album, "cover.jpg");
        var outcome = RemoveTask.Run([Path.Combine(album, "01 Tone.flac"), busy, cover], Options, locks);
        locks.Release(token);

        Assert.Equal(1, outcome.Cleared);
        Assert.Null(AlbumGain(Path.Combine(album, "01 Tone.flac")));
        Assert.Equal([(busy, "03 Spike.mp3 is busy: ReplayGain is running on it.")], outcome.Failures);
        Assert.Equal([cover], outcome.LeftOut);
        Assert.Equal("Removed the ReplayGain tags of 1 track. 1 file couldn't be changed: see below.", outcome.Message());

        var again = RemoveTask.Run([Path.Combine(album, "01 Tone.flac")], Options, locks);
        Assert.Equal("Removed the ReplayGain tags of 0 tracks. 1 track had none.", again.Message());
        Assert.Empty(locks.Owners());  // every short lock released
    }
}
