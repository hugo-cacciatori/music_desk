using System.Diagnostics;
using Microsoft.Data.Sqlite;
using Refrain.Core;
using Refrain.Core.Index;
using Refrain.Core.Search;
using Xunit;

namespace Refrain.Tests;

/// <summary>Roots (SPEC §3) and the scanner (SPEC §4), on temporary copies of the fixtures (Ground rule 6).</summary>
public sealed class IndexTests : IDisposable
{
    readonly TempFolder temp = new();
    readonly Database db;
    readonly Indexer indexer;

    public IndexTests()
    {
        db = new Database(Path.Combine(temp.Path, "library.db"));
        indexer = new Indexer(db);
    }

    public void Dispose()
    {
        indexer.Dispose();
        db.Dispose();
        temp.Dispose();
    }

    string Library(string fixture = "album")
    {
        var path = temp.Sub("Music");
        Setup.Copy(Path.Combine(Setup.Fixtures, fixture), path);
        return path;
    }

    AddResult Add(string path) => db.Write((conn, tx) => Roots.Add(conn, tx, path)).Result;

    void Scan(long rootId)
    {
        indexer.ScanRoot(rootId);
        indexer.WaitIdle();
    }

    T Read<T>(Func<SqliteConnection, T> query)
    {
        using var conn = db.Read();
        return query(conn);
    }

    List<string> Names(long folderId) =>
        Read(conn => Queries.Listing(conn, folderId)).Select(r => r.Name).Order(StringComparer.Ordinal).ToList();

    long FolderOf(string path) => Read(conn => Queries.Find(conn, path))!.Value.Id;

    [Fact]
    public void A_root_is_walked_and_its_tags_read()
    {
        var music = Library();
        var root = Add(music);
        Assert.Equal(AddOutcome.Added, root.Outcome);
        Scan(root.RootId!.Value);
        var album = FolderOf(Path.Combine(music, "Fixture Artist - Fixture Album"));
        var rows = Read(conn => Queries.Listing(conn, album));
        var first = rows.Single(r => r.Name == "01 First.flac");
        Assert.True(first.IsRead);
        Assert.NotNull(first.Title);
        Assert.Equal("FLAC", first.Format);
        Assert.True(first.Duration > 0);
        Assert.Contains(rows, r => r is { Name: "CD2", IsDir: true });
    }

    [Fact]
    public void Every_fixture_format_is_listed_and_the_tag_library_ones_read()
    {
        var music = Library("formats");
        Scan(Add(music).RootId!.Value);
        var rows = Read(conn => Queries.Listing(conn, FolderOf(music))).ToDictionary(r => r.Name);
        Assert.False(rows["tone.oga"].IsRead);  // names only (SPEC §36 "4.")
        Assert.False(rows["tone.tta"].IsRead);
        Assert.True(rows["tone.oga"].IsAudio);
        foreach (var name in new[] { "tone.flac", "tone.mp3", "tone.ogg", "tone.opus", "tone.m4b", "tone_aac.m4a",
                     "tone_alac.m4a", "tone.wav", "tone.aiff", "tone.wma", "tone.wv", "tone.mp2" })
        {
            Assert.True(rows[name].IsRead, name);
            Assert.True(rows[name].ReadError is null, $"{name}: {rows[name].ReadError}");
            Assert.True(rows[name].Duration > 0, name);
        }
    }

    [Fact]
    public void A_rescan_follows_the_disk()
    {
        var music = Library();
        var root = Add(music).RootId!.Value;
        Scan(root);
        var albumPath = Path.Combine(music, "Fixture Artist - Fixture Album");
        var album = FolderOf(albumPath);
        File.Delete(Path.Combine(albumPath, "track2.flac"));
        Directory.Delete(Path.Combine(albumPath, "CD2"), recursive: true);
        File.Copy(Path.Combine(albumPath, "01 First.flac"), Path.Combine(albumPath, "Copy.flac"));
        var changed = new List<long>();
        indexer.FolderChanged += changed.Add;
        Scan(root);
        var names = Names(album);
        Assert.DoesNotContain("track2.flac", names);
        Assert.DoesNotContain("CD2", names);
        Assert.Contains("Copy.flac", names);
        Assert.True(Read(conn => Queries.Listing(conn, album)).Single(r => r.Name == "Copy.flac").IsRead);
        Assert.Contains(album, changed);
        // CD2's entries went with it, search rows included.
        Assert.Empty(Read(conn => LibrarySearch.Find(conn, SearchQuery.Parse("CD2")).Ids));
    }

    [Fact]
    public void Scanning_a_path_reads_only_that_folder()
    {
        var music = Library();
        var root = Add(music).RootId!.Value;
        Scan(root);
        var albumPath = Path.Combine(music, "Fixture Artist - Fixture Album");
        File.Copy(Path.Combine(albumPath, "01 First.flac"), Path.Combine(albumPath, "New.flac"));
        indexer.ScanPath(albumPath, recursive: false);
        indexer.WaitIdle();
        Assert.Contains("New.flac", Names(FolderOf(albumPath)));
    }

    [Fact]
    public void A_folder_not_indexed_yet_scans_its_nearest_indexed_ancestor()
    {
        var music = Library();
        Scan(Add(music).RootId!.Value);
        var fresh = Path.Combine(music, "Fresh", "Deeper");
        Directory.CreateDirectory(fresh);
        File.Copy(Path.Combine(music, "Fixture Artist - Fixture Album", "01 First.flac"), Path.Combine(fresh, "a.flac"));
        indexer.ScanPath(fresh, recursive: false);
        indexer.WaitIdle();
        Assert.Equal(["a.flac"], Names(FolderOf(fresh)));
    }

    [Fact]
    public void The_same_folder_is_never_added_twice()
    {
        var music = Library();
        Assert.Equal(AddOutcome.Added, Add(music).Outcome);
        Assert.Equal(AddOutcome.AlreadyRoot, Add(music.ToUpperInvariant() + "\\").Outcome);
        var inside = Add(Path.Combine(music, "Fixture Artist - Fixture Album"));
        Assert.Equal(AddOutcome.InsideRoot, inside.Outcome);
        Assert.Equal(Path.Combine(music, "Fixture Artist - Fixture Album"), inside.Path);
        Assert.Equal(AddOutcome.IsAFile, Add(Path.Combine(music, "Fixture Artist - Fixture Album", "01 First.flac")).Outcome);
        Assert.Equal(AddOutcome.NotAFolder, Add(Path.Combine(music, "missing")).Outcome);
    }

    [Fact]
    public void A_junction_into_a_root_counts_as_the_root()
    {
        var music = Library();
        Add(music);
        var link = Path.Combine(temp.Path, "Link");
        using (var mklink = Process.Start(new ProcessStartInfo("cmd.exe", $"/c mklink /J \"{link}\" \"{music}\"")
                   { CreateNoWindow = true, UseShellExecute = false, RedirectStandardOutput = true }))
            mklink!.WaitForExit();
        Assert.True(Directory.Exists(link));
        Assert.Equal(AddOutcome.AlreadyRoot, Add(link).Outcome);
        Assert.Equal(AddOutcome.InsideRoot, Add(Path.Combine(link, "Fixture Artist - Fixture Album")).Outcome);
        Directory.Delete(link);  // the junction only, not its target
    }

    [Fact]
    public void A_parent_merges_the_roots_inside_it_and_keeps_their_entries()
    {
        var music = Library();
        var albumPath = Path.Combine(music, "Fixture Artist - Fixture Album");
        var cd2 = Path.Combine(albumPath, "CD2");
        var inner = Add(cd2).RootId!.Value;
        Scan(inner);
        var before = Names(FolderOf(cd2));
        var merged = Add(music);
        Assert.Equal(AddOutcome.Merged, merged.Outcome);
        Assert.Single(Read(Roots.All));
        Assert.Equal(before, Names(FolderOf(cd2)));
        Assert.Equal(cd2, Read(conn => Queries.PathOf(conn, FolderOf(cd2))));
        Scan(merged.RootId!.Value);
        Assert.Contains("01 First.flac", Names(FolderOf(albumPath)));
        Assert.Equal(before, Names(FolderOf(cd2)));
    }

    [Fact]
    public async Task Removing_a_root_forgets_it_and_leaves_the_disk_alone()
    {
        var music = Library();
        var root = Add(music).RootId!.Value;
        Scan(root);
        await db.Write((conn, tx) => Roots.Remove(conn, tx, root));
        Assert.Empty(Read(Roots.All));
        Assert.Equal(0L, Read(conn => conn.Scalar("SELECT count(*) FROM search")));
        Assert.True(File.Exists(Path.Combine(music, "Fixture Artist - Fixture Album", "01 First.flac")));
    }

    [Fact]
    public void Natural_order_is_explorers()
    {
        var names = new List<string> { "Track 10", "track 2", "Track 1", "B", "a" };
        names.Sort(NaturalOrder.Instance);
        Assert.Equal(["a", "B", "Track 1", "track 2", "Track 10"], names);
    }
}
