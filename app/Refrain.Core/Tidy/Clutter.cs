namespace Refrain.Core.Tidy;

/// <summary>What a picture, a video and an other offered file (a cue sheet, a playlist…) are (Tidy's options, SPEC §28
/// "Clutter", §36 "For later"), extensions in lowercase.</summary>
public sealed record ClutterLists(IReadOnlySet<string> Pictures, IReadOnlySet<string> Videos, IReadOnlySet<string> Others)
{
    public static ClutterLists Of(Options.TidyOptions options) => new(
        options.Pictures.Select(e => e.ToLowerInvariant()).ToHashSet(),
        options.Videos.Select(e => e.ToLowerInvariant()).ToHashSet(),
        options.Others.Select(e => e.ToLowerInvariant()).ToHashSet());
}

/// <summary>A row of Tidy's redundant files list: a file, or a whole folder (<see cref="Lists"/> set: a clutter folder,
/// checked again against them before it goes).</summary>
public sealed record OfferItem(string Path, string Why, string Label = "", ClutterLists? Lists = null)
{
    public bool IsFolder => Lists is not null;
    public string Name => Label.Length > 0 ? Label : System.IO.Path.GetFileName(Path);
}

/// <summary>Tidy's clutter (SPEC §28): pictures, videos and other offered files beside music, and the outermost
/// folders holding only those or nothing. Ported from the Python app's <c>core/cleanup.py</c>; it lists, never deletes.</summary>
public static class Clutter
{
    public const string PictureBesideMusic = "picture beside music", VideoBesideMusic = "video beside music",
        OtherBesideMusic = "other file beside music";
    static readonly HashSet<string> Ignored = ["thumbs.db", "desktop.ini"];  // Windows' own: they count as nothing

    sealed class Contents
    {
        public int Pictures;
        public int Videos;
        public int Extras;  // the other offered files
        public bool Other;  // anything else: not clutter
    }

    static string Kind(string name, ClutterLists lists)
    {
        if (Ignored.Contains(name.ToLowerInvariant())) return "ignored";
        var ext = System.IO.Path.GetExtension(name).ToLowerInvariant();
        if (lists.Pictures.Contains(ext)) return "picture";
        if (lists.Videos.Contains(ext)) return "video";
        if (lists.Others.Contains(ext)) return "extra";
        return Formats.IsAudio(ext) ? "audio" : "other";
    }

    /// <summary>(the File column, the Why column) of a clutter folder.</summary>
    public static (string Label, string Why) FolderLabel(string path, int pictures, int videos, int others)
    {
        var name = System.IO.Path.GetFileName(path);
        if (pictures == 0 && videos == 0 && others == 0) return ($"{name} (empty folder)", "empty folder");
        var parts = new[] { (pictures, "picture"), (videos, "video"), (others, "other file") }.Where(p => p.Item1 > 0)
            .Select(p => $"{p.Item1} {p.Item2}{(p.Item1 != 1 ? "s" : "")}").ToList();
        return ($"{name} (folder, {string.Join(", ", parts)})", $"folder of {string.Join(" and ", parts)}");
    }

    /// <summary>Pictures, videos and other offered files in every folder holding audio under root (root included), and
    /// the outermost folders whose whole contents are those or nothing, in Explorer's order. Root itself is never
    /// offered; links and junctions are never followed and count as something else.</summary>
    public static List<OfferItem> Find(string root, ClutterLists lists, CancellationToken cancel = default)
    {
        var items = new List<OfferItem>();
        Visit(root, lists, items, cancel, isRoot: true);
        return items;
    }

    /// <summary>The folder's contents if it is clutter, else null after listing its own items: a clutter folder is
    /// listed by its parent, so only the outermost one is a row.</summary>
    static Contents? Visit(string folder, ClutterLists lists, List<OfferItem> items, CancellationToken cancel, bool isRoot = false)
    {
        if (cancel.IsCancellationRequested) return null;
        List<FileSystemInfo> entries;
        try
        {
            entries = new DirectoryInfo(folder).EnumerateFileSystemInfos().OrderBy(e => e.Name, NaturalOrder.Instance).ToList();
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            return null;  // unreadable: never offered
        }
        var contents = new Contents();
        var files = new List<(string Path, string Kind)>();
        var clutter = new List<(string Path, Contents Sub)>();
        foreach (var entry in entries)
        {
            if (entry.Attributes.HasFlag(FileAttributes.ReparsePoint)) contents.Other = true;
            else if (entry is DirectoryInfo)
            {
                var sub = Visit(entry.FullName, lists, items, cancel);
                if (sub is null) contents.Other = true;
                else
                {
                    clutter.Add((entry.FullName, sub));
                    contents.Pictures += sub.Pictures;
                    contents.Videos += sub.Videos;
                    contents.Extras += sub.Extras;
                }
            }
            else
            {
                var kind = Kind(entry.Name, lists);
                files.Add((entry.FullName, kind));
                if (kind == "picture") contents.Pictures++;
                else if (kind == "video") contents.Videos++;
                else if (kind == "extra") contents.Extras++;
                else if (kind != "ignored") contents.Other = true;
            }
        }
        if (!contents.Other && !isRoot) return contents;  // clutter: its parent lists it whole
        foreach (var (path, sub) in clutter)
        {
            var (label, why) = FolderLabel(path, sub.Pictures, sub.Videos, sub.Extras);
            items.Add(new OfferItem(path, why, label, lists));
        }
        if (files.Any(f => f.Kind == "audio"))
            foreach (var (path, kind) in files)
                if (kind is "picture" or "video" or "extra")
                    items.Add(new OfferItem(path, kind switch { "picture" => PictureBesideMusic, "video" => VideoBesideMusic, _ => OtherBesideMusic }));
        return null;
    }

    /// <summary>Why an item can't go any more ("gone", or a folder that now holds something else), or null.</summary>
    public static string? Recheck(OfferItem item)
    {
        if (item.Lists is { } lists)
        {
            if (!Directory.Exists(item.Path)) return "gone";
            return Visit(item.Path, lists, [], default) is not null ? null : "it now holds other files";
        }
        return File.Exists(item.Path) ? null : "gone";
    }
}
