using Refrain.Core;
using Refrain.Core.Browser;
using Refrain.Core.Index;
using Xunit;

namespace Refrain.Tests;

/// <summary>The browser's plain pieces (SPEC §5): history, columns and their sort, and the remembered settings.</summary>
public sealed class BrowserTests
{
    [Fact]
    public void History_goes_back_and_forward_and_skips_folders_gone()
    {
        var history = new History();
        foreach (var id in new long[] { 1, 2, 3, 4 }) history.Open(id);
        Assert.Equal(3, history.Back(_ => true));
        Assert.Equal(1, history.Back(id => id != 2));  // 2 left the index
        Assert.False(history.CanGoBack);
        Assert.Equal(3, history.Forward(_ => true));
        history.Open(9);  // a new folder clears the forward stack
        Assert.False(history.CanGoForward);
        Assert.Equal(3, history.Back(_ => true));
        Assert.Null(new History().Back(_ => true));
    }

    static FileRow Row(long id, string name, bool dir = false, string? title = null, long? track = null) =>
        new() { Id = id, Name = name, IsDir = dir, Ext = dir ? null : ".flac", Title = title, Track = track };

    [Fact]
    public void Folders_stay_first_in_both_directions_and_ties_go_by_name()
    {
        List<FileRow> rows =
        [
            Row(1, "b.flac", title: "Zed", track: 2), Row(2, "Folder 10", dir: true), Row(3, "a.flac", title: "Zed"),
            Row(4, "Folder 2", dir: true), Row(5, "c.flac", title: "Alpha", track: 1),
        ];
        rows.Sort(Columns.Sort("name", descending: false));
        Assert.Equal(["Folder 2", "Folder 10", "a.flac", "b.flac", "c.flac"], rows.Select(r => r.Name));
        rows.Sort(Columns.Sort("title", descending: false));
        Assert.Equal(["Folder 2", "Folder 10", "c.flac", "a.flac", "b.flac"], rows.Select(r => r.Name));
        rows.Sort(Columns.Sort("track", descending: true));
        Assert.Equal(["Folder 10", "Folder 2", "a.flac", "b.flac", "c.flac"], rows.Select(r => r.Name));
    }

    [Fact]
    public void A_saved_layout_is_merged_with_the_defaults()
    {
        var saved = new HeaderState
        {
            SortKey = "gone",
            Columns = [new ColumnState { Key = "artist", Width = 5, Visible = false }, new ColumnState { Key = "gone" },
                new ColumnState { Key = "name", Width = 400, Visible = false }],
        };
        var layout = Columns.Layout(Columns.FileList, saved);
        Assert.Equal("name", layout.SortKey);
        Assert.Equal(["artist", "name"], layout.Columns.Take(2).Select(c => c.Key));
        Assert.Equal(24, layout.Columns[0].Width);  // never narrower than a grip
        Assert.True(layout.Columns[1].Visible);  // Name always shows
        Assert.Equal(Columns.FileList.Count, layout.Columns.Count);
        var defaults = Columns.Defaults(Columns.FileList);
        Assert.False(defaults.Columns.Single(c => c.Key == "bitrate").Visible);
        Assert.False(defaults.Columns.Single(c => c.Key == "modified").Visible);
    }

    [Fact]
    public void Cells_read_as_the_python_app_showed_them()
    {
        var folder = Row(1, "Album", dir: true) with { TrackCount = 12, Duration = 3725 };
        Assert.Equal("12 tracks", Columns.Text(folder, "title"));
        Assert.Equal("1:02:05", Columns.Text(folder, "duration"));
        var track = Row(2, "a.flac") with { Duration = 284.4, ReplayGain = ReplayGainState.Partial, Size = 31_457_280 };
        Assert.Equal("4:44", Columns.Text(track, "duration"));
        Assert.Equal("◐", Columns.Text(track, "rg"));
        Assert.Equal(30.0.ToString("N1") + " MB", Columns.Text(track, "size"));
        Assert.Equal(["name", "title", "artist", "album", "folder"], Columns.Search.Take(5).Select(c => c.Key));
    }

    [Fact]
    public void Settings_survive_a_round_trip_and_a_broken_file()
    {
        using var temp = new TempFolder();
        var path = Path.Combine(temp.Path, "settings.json");
        var settings = new Settings { TreeVisible = true, LastFolder = @"D:\Music", ExpandedFolders = [@"D:\Music"] };
        settings.FileHeader = Columns.Defaults(Columns.FileList);
        settings.Save(path);
        var back = Settings.Load(path);
        Assert.True(back.TreeVisible);
        Assert.Equal(@"D:\Music", back.LastFolder);
        Assert.Equal(Columns.FileList.Count, back.FileHeader!.Columns.Count);
        File.WriteAllText(path, "{ not json");
        Assert.False(Settings.Load(path).TreeVisible);
    }
}
