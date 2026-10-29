using Refrain.Core.Tags;

namespace Refrain.Core.Options;

/// <summary>The fields a file gives the masks, as the Python app's <c>TaggedFile.rename_fields</c>: numbers when
/// present, text when not blank, kept as stored (not stripped).</summary>
public static class RenameFields
{
    public static Dictionary<string, object> Of(FileTags tags)
    {
        var fields = new Dictionary<string, object>();
        foreach (var (name, field) in new[] { ("track", Field.Track), ("track_total", Field.TrackTotal), ("disc", Field.Disc), ("disc_total", Field.DiscTotal) })
            if (long.TryParse(tags.Values[field], out var n) && n > 0)
                fields[name] = n;
        foreach (var (name, field) in new[]
                 {
                     ("title", Field.Title), ("artist", Field.Artist), ("albumartist", Field.AlbumArtist), ("album", Field.Album),
                     ("year", Field.Year), ("genre", Field.Genre), ("composer", Field.Composer),
                 })
            if (tags.Values[field].Trim().Length > 0)
                fields[name] = tags.Values[field];
        return fields;
    }

    /// <summary>The first track of a file or folder (the folder's own, else level by level in Explorer's order), for
    /// the File names page's example; null when there is none. Reads the disk: never on the UI thread.</summary>
    public static string? FirstTrack(string path)
    {
        if (File.Exists(path)) return Formats.IsRead(Path.GetExtension(path)) ? path : null;
        var level = new List<string> { path };
        while (level.Count > 0)
        {
            var next = new List<string>();
            foreach (var folder in level)
            {
                if (Index.DiskListing.List(folder) is not { } items) continue;
                var names = items.Select(i => i.Name).Order(NaturalOrder.Instance).ToList();
                var track = names.FirstOrDefault(n => Formats.IsRead(Path.GetExtension(n)) && items.First(i => i.Name == n) is { IsDir: false });
                if (track is not null) return Path.Combine(folder, track);
                next.AddRange(names.Where(n => items.First(i => i.Name == n).IsDir).Select(n => Path.Combine(folder, n)));
            }
            level = next;
        }
        return null;
    }
}
