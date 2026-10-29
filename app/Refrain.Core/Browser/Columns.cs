using System.Globalization;
using Refrain.Core.Index;

namespace Refrain.Core.Browser;

/// <summary>A column of the file list or the search results (SPEC §5, §6): its key, header, default width, whether it
/// shows by default, and how its cells read.</summary>
public sealed record Column(string Key, string Header, double Width, bool Visible, bool AlignRight = false);

/// <summary>The columns, and the text and sort order of each cell.</summary>
public static class Columns
{
    public static readonly IReadOnlyList<Column> FileList =
    [
        new("name", "Name", 300, true),
        new("track", "#", 44, true, AlignRight: true),
        new("title", "Title", 220, true),
        new("artist", "Artist", 170, true),
        new("album", "Album", 190, true),
        new("year", "Year", 90, true),
        new("duration", "Duration", 80, true, AlignRight: true),
        new("format", "Format", 70, true),
        new("bitrate", "Bitrate", 80, false, AlignRight: true),
        new("size", "Size", 90, true, AlignRight: true),
        new("modified", "Modified", 150, false),
        new("rg", "RG", 44, true),
        new("lrclib", "LRCLIB", 100, true),
    ];

    /// <summary>The results: Name, Title, Artist, Album, Folder, then the file list's other columns (SPEC §6).</summary>
    public static readonly IReadOnlyList<Column> Search =
    [
        FileList[0], FileList[2], FileList[3], FileList[4],
        new("folder", "Folder", 300, true),
        .. FileList.Where(c => c.Key is not ("name" or "title" or "artist" or "album")),
    ];

    /// <summary>The saved layout merged with the defaults: unknown keys dropped, new columns added at the end.</summary>
    public static HeaderState Layout(IReadOnlyList<Column> columns, HeaderState? saved)
    {
        var known = columns.ToDictionary(c => c.Key);
        var state = new HeaderState { SortKey = "name" };
        if (saved is not null)
        {
            state.SortKey = known.ContainsKey(saved.SortKey) ? saved.SortKey : "name";
            state.Descending = saved.Descending;
            foreach (var c in saved.Columns.Where(c => known.ContainsKey(c.Key)).DistinctBy(c => c.Key))
                state.Columns.Add(new ColumnState { Key = c.Key, Width = Math.Max(24, c.Width), Visible = c.Visible || c.Key == "name" });
        }
        foreach (var c in columns.Where(c => state.Columns.All(s => s.Key != c.Key)))
            state.Columns.Add(new ColumnState { Key = c.Key, Width = c.Width, Visible = c.Visible });
        return state;
    }

    public static HeaderState Defaults(IReadOnlyList<Column> columns) => Layout(columns, null);

    public static string Text(FileRow row, string key) => key switch
    {
        "name" => row.Name,
        "track" => row.Track?.ToString(CultureInfo.CurrentCulture) ?? "",
        "title" => row.IsDir ? Tracks(row.TrackCount) : row.Title ?? "",
        "artist" => row.Artist ?? "",
        "album" => row.Album ?? "",
        "year" => row.Year ?? "",
        "duration" => Duration(row.Duration),
        "format" => row.Format ?? "",
        "bitrate" => row.Bitrate is { } kbps ? $"{kbps} kbps" : "",
        "size" => row.IsDir ? "" : Size(row.Size),
        "modified" => row.Mtime is { } m && !row.IsDir
            ? DateTime.UnixEpoch.AddSeconds(m).ToLocalTime().ToString("g", CultureInfo.CurrentCulture) : "",
        "rg" => row.ReplayGain switch
        {
            ReplayGainState.Full => "✓",
            ReplayGainState.Partial => "◐",
            ReplayGainState.None => "–",
            _ => "",
        },
        "lrclib" => row.Lrclib ?? "",
        "folder" => row.Folder,
        _ => "",
    };

    static string Tracks(long? count) => count switch
    {
        null or 0 => "",
        1 => "1 track",
        _ => $"{count:N0} tracks",
    };

    public static string Duration(double? seconds)
    {
        if (seconds is not { } s || s <= 0) return "";
        var t = TimeSpan.FromSeconds(Math.Round(s));
        return t.TotalHours >= 1 ? $"{(int)t.TotalHours}:{t.Minutes:00}:{t.Seconds:00}" : $"{t.Minutes}:{t.Seconds:00}";
    }

    public static string Size(long? bytes) => bytes switch
    {
        null => "",
        < 1024 => $"{bytes} B",
        < 1024 * 1024 => $"{bytes / 1024.0:N0} KB",
        < 1024L * 1024 * 1024 => $"{bytes / (1024.0 * 1024):N1} MB",
        _ => $"{bytes / (1024.0 * 1024 * 1024):N2} GB",
    };

    /// <summary>Folders first in both directions, then the column's order, ties by name in Explorer's order
    /// (SPEC §5 "As built (M2 task 5)"). Empty cells sort last.</summary>
    public static Comparison<FileRow> Sort(string key, bool descending)
    {
        var direction = descending ? -1 : 1;
        return (a, b) =>
        {
            if (a.IsDir != b.IsDir) return a.IsDir ? -1 : 1;
            var c = key == "name" ? 0 : CompareValues(Value(a, key), Value(b, key));
            if (c == 0) c = NaturalOrder.Instance.Compare(a.Name, b.Name);
            if (c == 0) c = a.Id.CompareTo(b.Id);
            return c * direction;
        };
    }

    static object? Value(FileRow row, string key) => key switch
    {
        "track" => row.Track,
        "title" => row.IsDir ? null : row.Title,
        "artist" => row.Artist,
        "album" => row.Album,
        "year" => row.Year,
        "duration" => row.Duration,
        "format" => row.Format,
        "bitrate" => row.Bitrate,
        "size" => row.IsDir ? null : row.Size,
        "modified" => row.Mtime,
        "rg" => row.ReplayGain,
        "lrclib" => row.Lrclib is { Length: > 0 } s ? s : null,
        "folder" => row.Folder,
        _ => null,
    };

    static int CompareValues(object? a, object? b) => (a, b) switch
    {
        (null, null) => 0,
        (null, _) => 1,
        (_, null) => -1,
        (string x, string y) => NaturalOrder.Instance.Compare(x, y),
        (IComparable x, _) => x.CompareTo(b),
        _ => 0,
    };
}
