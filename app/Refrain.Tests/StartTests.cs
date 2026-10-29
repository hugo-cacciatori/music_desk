using System.Runtime.InteropServices;
using Refrain.Core;
using Refrain.Core.Index;
using RefrainMenu;
using Xunit;

namespace Refrain.Tests;

/// <summary>Play (SPEC §14), starts from outside (SPEC §29) and the right-click handler (SPEC §31, §36 "7.").</summary>
public sealed class StartTests : IDisposable
{
    readonly TempFolder temp = new();

    public void Dispose() => temp.Dispose();

    [Fact]
    public void A_playlist_is_extended_m3u_of_audio_files_only()
    {
        var text = Playlists.Render([
            new PlayEntry(@"D:\Music\01 Airbag.flac", 284.4, "Radiohead", "Airbag"),
            new PlayEntry(@"D:\Music\02 Untitled.mp3", null, "Radiohead", " "),
            new PlayEntry(@"D:\Music\cover.jpg", null, null, null),
        ]);
        Assert.Equal("#EXTM3U\r\n#EXTINF:284,Radiohead - Airbag\r\nD:\\Music\\01 Airbag.flac\r\n" +
                     "#EXTINF:-1,02 Untitled\r\nD:\\Music\\02 Untitled.mp3\r\n", text);
    }

    [Fact]
    public void The_newest_twenty_playlists_are_kept_and_nothing_else_is_touched()
    {
        var folder = temp.Sub("playlists");
        File.WriteAllText(Path.Combine(folder, "mine.m3u8"), "");
        var start = new DateTime(2026, 10, 29, 12, 0, 0);
        string? last = null;
        for (var i = 0; i < 23; i++) last = Playlists.Write([new PlayEntry(@"D:\a.flac", 1, null, "a")], folder, start.AddSeconds(i / 2));
        var kept = Directory.GetFiles(folder, "play-*.m3u8");
        Assert.Equal(20, kept.Length);
        Assert.Contains(last, kept);
        Assert.Contains(kept, f => f.EndsWith("-2.m3u8"));  // two in one second
        Assert.True(File.Exists(Path.Combine(folder, "mine.m3u8")));
        Assert.Equal(new System.Text.UTF8Encoding(false).GetBytes("#"), File.ReadAllBytes(last!)[..1]);  // no BOM
    }

    [Fact]
    public async Task Play_folder_goes_folder_by_folder_and_play_selection_skips_folders()
    {
        var music = temp.Sub("Music");
        Setup.Copy(Path.Combine(Setup.Fixtures, "album"), music);
        using var db = new Database(Path.Combine(temp.Path, "library.db"));
        using (var indexer = new Indexer(db))
        {
            var root = await db.Write((c, t) => Roots.Add(c, t, music));
            indexer.ScanRoot(root.RootId!.Value);
            indexer.WaitIdle();
        }
        using var conn = db.Read();
        var album = Queries.Find(conn, Path.Combine(music, "Fixture Artist - Fixture Album"))!.Value.Id;
        var own = Playlists.ForFolder(conn, album, subfolders: false);
        var all = Playlists.ForFolder(conn, album, subfolders: true);
        Assert.Equal("01 First.flac", Path.GetFileName(own[0].Path));
        Assert.True(all.Count > own.Count);
        Assert.Equal(own, all[..own.Count]);  // the folder's own files first
        Assert.All(all, e => Assert.True(Formats.IsAudio(Path.GetExtension(e.Path))));
        var cd2 = Queries.Find(conn, Path.Combine(music, "Fixture Artist - Fixture Album", "CD2"))!.Value.Id;
        var first = Queries.Find(conn, Path.Combine(music, "Fixture Artist - Fixture Album", "01 First.flac"))!.Value.Id;
        Assert.Equal([own[0].Path], Playlists.ForIds(conn, [cd2, first]).Select(e => e.Path));
    }

    [Fact]
    public void A_start_reads_a_folder_or_an_entry_and_its_list()
    {
        Assert.Equal(new StartRequest(@"D:\Music", null, []) with { Paths = [] }, StartRequest.Parse([@"D:\Music"]) with { Paths = [] });
        Assert.True(StartRequest.Parse([]).IsEmpty);
        Directory.CreateDirectory(StartRequest.MenuFolder);
        var list = Path.Combine(StartRequest.MenuFolder, "test-list.txt");
        File.WriteAllText(list, "D:\\One\nD:\\Two\n\n");
        var request = StartRequest.Parse(["--open", "--list", list]);
        Assert.Equal("open", request.Entry);
        Assert.Equal([@"D:\One", @"D:\Two"], request.Paths);
        Assert.False(File.Exists(list));  // read, then deleted
        var elsewhere = Path.Combine(temp.Path, "list.txt");
        File.WriteAllText(elsewhere, "D:\\Three\n");
        Assert.Empty(StartRequest.Parse(["--open", "--list", elsewhere]).Paths);  // only the menu folder's lists
        Assert.True(File.Exists(elsewhere));
    }

    [Fact]
    public async Task A_second_start_hands_its_request_over()
    {
        var pipe = $"RefrainTest-{Guid.NewGuid():N}";
        using var first = Instance.TryStart(pipe);
        Assert.NotNull(first);
        Assert.Null(Instance.TryStart(pipe));  // a second Refrain doesn't get the pipe
        var received = new TaskCompletionSource<StartRequest>();
        first.Received += r => received.TrySetResult(r);
        Assert.True(Instance.HandOver(new StartRequest(@"D:\Music\Björk", null, []), pipe));
        var got = await received.Task.WaitAsync(TimeSpan.FromSeconds(5), TestContext.Current.CancellationToken);
        Assert.Equal(@"D:\Music\Björk", got.Folder);
        var again = new TaskCompletionSource<StartRequest>();
        first.Received += r => again.TrySetResult(r);
        Assert.True(Instance.HandOver(new StartRequest(null, "open", [@"D:\X"]), pipe));  // and again
        Assert.Equal("open", (await again.Task.WaitAsync(TimeSpan.FromSeconds(5), TestContext.Current.CancellationToken)).Entry);
        Assert.False(Instance.HandOver(StartRequest.None, $"RefrainTest-{Guid.NewGuid():N}"));  // nobody listens
    }

    [Fact]
    public void The_menu_offers_open_on_one_folder_or_empty_space()
    {
        (string, bool)[] folder = [(@"D:\Music", true)];
        Assert.Equal([MenuLogic.Open], MenuLogic.Entries(MenuLogic.Classify(false, folder), folder));
        Assert.Equal([MenuLogic.Open], MenuLogic.Entries(MenuLogic.Classify(true, folder), folder));
        (string, bool)[] file = [(@"D:\Music\a.flac", false)];
        Assert.Empty(MenuLogic.Entries(MenuLogic.Classify(false, file), file));
        (string, bool)[] two = [(@"D:\A", true), (@"D:\B", true)];
        Assert.Empty(MenuLogic.Entries(MenuLogic.Classify(false, two), two));
        Assert.Equal((@"C:\App\Refrain.exe", "\"D:\\\\\"", null), MenuLogic.Command(MenuLogic.Open, [@"D:\"], @"C:\App"));
        var (_, args, list) = MenuLogic.Command(new Entry("tidy", "Tidy", ""), [@"D:\A", @"D:\B"], @"C:\App");
        Assert.Equal("--tidy --list ", args);
        Assert.Equal("D:\\A\nD:\\B\n", list);
    }

    /// <summary>The published native DLL, driven as Explorer does it (class object, instance, Initialize,
    /// QueryContextMenu, GetCommandString, InvokeCommand). Needs <c>dotnet publish app/RefrainMenu -c Release</c>.</summary>
    [Fact]
    public unsafe void The_native_handler_adds_its_submenu_below_7zip_once()
    {
        var dll = Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "..", "RefrainMenu", "bin", "Release",
            "net10.0-windows", "win-x64", "publish", "RefrainMenu.dll");
        Assert.SkipUnless(File.Exists(dll), "The native handler isn't published (dotnet publish app/RefrainMenu -c Release).");
        var library = NativeLibrary.Load(Path.GetFullPath(dll));
        var test = (delegate* unmanaged<char*, int, char*, int, int>)NativeLibrary.GetExport(library, "RefrainMenu_Test");
        string Run(string paths, bool background, out int added)
        {
            var output = new char[4096];
            fixed (char* p = paths + "\0")
            fixed (char* o = output)
            {
                added = test(p, background ? 1 : 0, o, output.Length);
                return new string(o);
            }
        }

        var music = temp.Sub("Music", "Album");
        var report = Run(music, background: false, out var added);
        Assert.Equal(1, added);
        Assert.Contains("position=2\n", report);  // just below 7-Zip
        Assert.Contains("again=0\n", report);  // asked twice for one menu: one submenu
        Assert.Contains("Open in Refrain|refrain.open\n", report);
        Assert.Contains($"command={Path.GetDirectoryName(Path.GetFullPath(dll))}\\Refrain.exe \"{music}\"\n", report);
        Run(music, background: true, out added);
        Assert.Equal(1, added);
        var file = Path.Combine(music, "a.flac");
        File.WriteAllText(file, "");
        Run(file, background: false, out added);
        Assert.Equal(0, added);
        var other = temp.Sub("Music", "Other");
        Run(music + "\n" + other, background: false, out added);
        Assert.Equal(0, added);
    }
}
