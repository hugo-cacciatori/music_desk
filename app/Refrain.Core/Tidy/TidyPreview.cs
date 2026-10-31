namespace Refrain.Core.Tidy;

/// <summary>A file of the preview's Tidy section: its name (relative to the previewed folder), what Tidy would do to
/// it, and whether that is something (highlighted).</summary>
public sealed record TidyFile(string Name, string Action, bool Changes);

/// <summary>Tidy in the search preview (SPEC §37 "Tidy in the preview"): Tidy's own dry run (<see cref="Tidying.Plan"/>)
/// on the previewed folder, said in plain words ("the album isn't split, no ReplayGain tags, 3 file names don't follow
/// your mask"), one line per album that has something to do, and the folder's files with the planned action beside each.</summary>
public sealed record TidyPreview(bool HasWork, IReadOnlyList<string> Lines, IReadOnlyList<TidyFile> Files)
{
    /// <summary>At most this many files are listed: an artist's whole tree would be a wall.</summary>
    public const int MaxFiles = 300;

    public static TidyPreview From(TidyPlan plan, string folder)
    {
        var lines = new List<string>();
        var several = plan.Albums.Count > 1;
        foreach (var album in plan.Albums.Where(a => !a.IsTidy))
        {
            var reasons = Reasons(album);
            if (reasons.Count == 0) continue;
            var text = string.Join(", ", reasons);
            text = char.ToUpperInvariant(text[0]) + text[1..];
            lines.Add(several ? $"{Relative(folder, album.Folder)}: {text}" : text);
        }
        foreach (var loose in plan.Loose) lines.Add(TidyText.LooseText(loose));

        // What happens to each file: renamed, split, measured, offered to the Recycle Bin at the end.
        var actions = new Dictionary<string, List<string>>(StringComparer.OrdinalIgnoreCase);
        void Act(string path, string action)
        {
            if (!actions.TryGetValue(path, out var list)) actions[path] = list = [];
            if (!list.Contains(action)) list.Add(action);
        }
        foreach (var album in plan.Albums)
        {
            foreach (var each in album.Folders)
            {
                if (each.Kind == FolderKind.NotSplit && each.Split is { } split)
                {
                    Act(split.Source ?? split.Cue, $"split into {ReplayGain.Report.Plural(split.Written.Count, "track")}");
                }
                if (each.Kind == FolderKind.Skipped && each.Image is { } image) Act(Path.Combine(each.Folder, image), $"left alone: {each.Skip}");
                foreach (var item in each.Renames?.Items ?? [])
                    if (item.Status == RenameStatus.Rename && item.Target is { } target)
                        Act(Path.Combine(each.Folder, item.Source), $"rename to {Path.GetFileName(target)}");
                foreach (var offered in each.Offer) Act(offered, "offered to the Recycle Bin");
            }
            if (album.ReplayGain is not null)
                foreach (var each in album.Folders.Where(f => f.Kind != FolderKind.Skipped))
                    foreach (var track in Tracks(each.Folder).Where(t => !album.ReplayGainExclude.Contains(Path.GetFileName(t))))
                        Act(track, "ReplayGain");
            foreach (var item in album.Clutter) Act(item.Path, $"offered to the Recycle Bin ({item.Why})");
        }
        foreach (var item in plan.Loose) Act(item.Path, $"offered to the Recycle Bin ({item.Why})");

        var files = new List<TidyFile>();
        foreach (var path in AllFiles(folder).Concat(actions.Keys).Distinct(StringComparer.OrdinalIgnoreCase)
                     .OrderBy(p => Relative(folder, p), NaturalOrder.Instance).Take(MaxFiles))
        {
            var has = actions.TryGetValue(path, out var list);
            // A file inside an offered folder goes with it.
            var inOffered = !has && actions.Any(a => a.Value.Any(v => v.StartsWith("offered", StringComparison.Ordinal)) &&
                                                     RealPath.IsInside(path, a.Key) && !path.Equals(a.Key, StringComparison.OrdinalIgnoreCase));
            files.Add(new TidyFile(Relative(folder, path),
                has ? string.Join(", then ", list!) : inOffered ? "offered to the Recycle Bin with its folder" : "kept as it is", has || inOffered));
        }
        return new TidyPreview(plan.HasWork, lines, files);
    }

    /// <summary>Why an album isn't tidy, in plain words.</summary>
    static List<string> Reasons(AlbumTidy album)
    {
        var reasons = new List<string>();
        foreach (var each in album.Folders)
        {
            var where = each == album.Own ? "" : $" in {Path.GetFileName(each.Folder)}";
            if (each.Kind == FolderKind.NotSplit) reasons.Add($"the album isn't split{where}");
            if (each.Kind == FolderKind.Skipped) reasons.Add($"skipped{where}: {each.Skip}");
            if (each.RenameCount > 0)
                reasons.Add(each.RenameCount == 1 ? $"1 file name doesn't follow your mask{where}" : $"{each.RenameCount} file names don't follow your mask{where}");
        }
        if (album.ReplayGain is { } why)
            reasons.Add(why switch
            {
                "missing tags" => "no ReplayGain tags",
                "after the split" => "ReplayGain after the split",
                _ => $"ReplayGain: {why}",
            });
        if (album.Clutter.Count > 0)
        {
            var offer = TidyText.ClutterText(album.Clutter);
            reasons.Add(char.ToLowerInvariant(offer[0]) + offer[1..] + " to the Recycle Bin");
        }
        return reasons;
    }

    static IEnumerable<string> Tracks(string folder)
    {
        try
        {
            return Directory.EnumerateFiles(folder).Where(f => Formats.ReplayGain.Contains(Path.GetExtension(f).ToLowerInvariant())).ToList();
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            return [];
        }
    }

    static IEnumerable<string> AllFiles(string folder)
    {
        try
        {
            return Directory.EnumerateFiles(folder, "*", SearchOption.AllDirectories).Take(MaxFiles * 2).ToList();
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            return [];
        }
    }

    static string Relative(string folder, string path)
    {
        var relative = Path.GetRelativePath(folder, path);
        return relative == "." ? Path.GetFileName(path.TrimEnd('\\')) : relative;
    }
}
