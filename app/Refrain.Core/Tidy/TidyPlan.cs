using System.Globalization;
using Refrain.Core.Cue;
using Refrain.Core.Options;
using Refrain.Core.ReplayGain;

namespace Refrain.Core.Tidy;

public enum FolderKind { NotSplit, Split, HalfSplit, Skipped }

/// <summary>One folder Tidy renames: an album, or a disc subfolder of one.</summary>
public sealed class FolderPlan(string folder, FolderKind kind)
{
    public string Folder { get; } = folder;
    public FolderKind Kind { get; } = kind;
    public AlbumPlan? Split { get; init; }  // not split: the split to run
    public RenamePlan? Renames { get; init; }  // split and half-split: the renames to run (maybe none)
    public List<string> Offer { get; } = [];  // the image and its .cue(s), offered after the run
    public string? Skip { get; init; }  // why nothing is done here
    public string? Image { get; init; }  // an image waiting to be split: never measured as a track
    public List<string> Notes { get; } = [];  // what the plan shows besides (missing tags, a warning…)

    public int RenameCount => Renames?.With(RenameStatus.Rename).Count ?? 0;

    /// <summary>The images here (offered, or waiting to be split): not tracks, so ReplayGain leaves them out.</summary>
    public HashSet<string> Images
    {
        get
        {
            var names = Offer.Where(p => Formats.IsAudio(Path.GetExtension(p))).Select(p => Path.GetFileName(p)!).ToHashSet();
            if (Image is not null) names.Add(Image);
            return names;
        }
    }
}

/// <summary>A ReplayGain album (SPEC §28): its own folder's plan first, then its disc subfolders', and the ReplayGain
/// check.</summary>
public sealed class AlbumTidy(string folder)
{
    public string Folder { get; } = folder;
    public List<FolderPlan> Folders { get; } = [];
    public string? ReplayGain { get; set; }  // why ReplayGain runs; null when the tags are already right
    public HashSet<string> ReplayGainExclude { get; set; } = [];  // the album folder's images, never measured
    public List<OfferItem> Clutter { get; } = [];  // offered at the end, untouched by the run

    public FolderPlan Own => Folders[0];

    /// <summary>Nothing to do: no rename, the ReplayGain tags right, nothing skipped, no clutter to offer.</summary>
    public bool IsTidy => ReplayGain is null && Clutter.Count == 0 &&
                          Folders.All(f => f.Kind is FolderKind.Split or FolderKind.HalfSplit && f.RenameCount == 0);
}

public sealed class TidyPlan(string root)
{
    public string Root { get; } = root;  // several folders: the deepest folder holding them all
    public List<AlbumTidy> Albums { get; } = [];
    public List<OfferItem> Loose { get; } = [];  // clutter folders outside any album
    public List<string> Roots { get; init; } = [];  // several folders: the ones planned (their trees)

    /// <summary>The folder trees a run works in, so locks.</summary>
    public IReadOnlyList<string> Trees => Roots.Count > 0 ? Roots : [Root];

    /// <summary>Every clutter item, the albums' and the loose ones.</summary>
    public List<OfferItem> AllClutter => [.. Albums.SelectMany(a => a.Clutter), .. Loose];

    public IEnumerable<FolderPlan> AllFolders => Albums.SelectMany(a => a.Folders);

    /// <summary>"12 albums: 3 to split, 2 to rename, 5 to ReplayGain, 2 already tidy, 1 skipped".</summary>
    public string Summary()
    {
        var folders = AllFolders.ToList();
        (string Label, int Count)[] counts =
        [
            ("to split", folders.Count(f => f.Kind == FolderKind.NotSplit)),
            ("to rename", folders.Count(f => f.RenameCount > 0)),
            ("to ReplayGain", Albums.Count(a => a.ReplayGain is not null)),
            ("already tidy", Albums.Count(a => a.IsTidy)),
            ("skipped", folders.Count(f => f.Kind == FolderKind.Skipped)),
            ("with files to offer", Albums.Count(a => a.Clutter.Count > 0)),
        ];
        var head = $"{Albums.Count} album{(Albums.Count == 1 ? "" : "s")}";
        var parts = counts.Where(c => c.Count > 0).Select(c => $"{c.Count} {c.Label}").ToList();
        var text = parts.Count > 0 ? $"{head}: {string.Join(", ", parts)}" : head;
        if (Loose.Count > 0) text += $"; {Loose.Count} folder{(Loose.Count == 1 ? "" : "s")} to offer outside the albums";
        return text;
    }

    /// <summary>Something to rename, ReplayGain or offer (Run is disabled otherwise).</summary>
    public bool HasWork => AllClutter.Count > 0 || Albums.Any(a => a.ReplayGain is not null) ||
                           AllFolders.Any(f => f.Kind == FolderKind.NotSplit || f.RenameCount > 0);
}

/// <summary>Tidy's dry run (SPEC §28), ported from the Python app's <c>core/tidy.py</c>: what tidying a folder tree
/// would do to each album. Nothing on disk changes here. Until the cue split comes (rebuild task 9), an unsplit album
/// is skipped with "split it from its .cue", untouched (SPEC §36 "8.").</summary>
public static class Tidying
{
    public const string SplitByHand = "open its .cue to split it by hand";
    static readonly HashSet<string> SplitReads = new(StringComparer.OrdinalIgnoreCase) { ".flac", ".wav", ".aif", ".aiff" };

    /// <summary>A keyword subfolder holding a disc of its album (cd2, disc 2…): any album-data keyword but the
    /// track-data (lite) and excluded (bonus) ones. Tidy renames these; lite and bonus it leaves alone.</summary>
    public static bool IsDiscFolder(string name, ReplayGainOptions options)
    {
        var lower = name.ToLowerInvariant();
        return !options.TrackDataKeywords.Concat(options.ExcludedKeywords).Any(k => lower.Contains(k, StringComparison.Ordinal));
    }

    static List<string> AudioNames(string folder)
    {
        try
        {
            return Directory.EnumerateFiles(folder).Select(p => Path.GetFileName(p)!).Where(n => Formats.IsAudio(Path.GetExtension(n))).ToList();
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            return [];
        }
    }

    /// <summary>(album folder, its disc subfolders) under root, root included. An album is a folder holding any audio
    /// file; its keyword subfolders aren't albums.</summary>
    public static List<(string Folder, List<string> Discs)> FindAlbums(string root, ReplayGainOptions options)
    {
        var albums = new List<(string, List<string>)>();
        void Walk(string folder)
        {
            List<string> subfolders;
            try
            {
                subfolders = Directory.EnumerateDirectories(folder).Select(d => Path.GetFileName(d)!)
                    .OrderBy(LyricsKeyFold, StringComparer.Ordinal).ToList();
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException)
            {
                return;
            }
            if (AudioNames(folder).Count > 0)
            {
                var keyword = Album.KeywordSubfolders(folder, options).ToHashSet();
                var discs = subfolders.Where(n => keyword.Contains(n) && IsDiscFolder(n, options) && AudioNames(Path.Combine(folder, n)).Count > 0)
                    .Select(n => Path.Combine(folder, n)).ToList();
                albums.Add((folder, discs));
                subfolders.RemoveAll(keyword.Contains);
            }
            foreach (var name in subfolders) Walk(Path.Combine(folder, name));
        }
        Walk(root);
        return albums;
    }

    static string LyricsKeyFold(string name) => Tags.LyricsKey.CaseFold(name);

    /// <summary>The one image a single-file sheet plays that the split reads, as named in the folder.</summary>
    static string? SheetImage(CueSheet sheet, Dictionary<string, string> audio)
    {
        var names = sheet.Files.Select(LyricsKeyFold).Distinct().ToList();
        if (names.Count != 1 || !audio.TryGetValue(names[0], out var image)) return null;
        return SplitReads.Contains(Path.GetExtension(image)) ? image : null;
    }

    /// <summary>Why a folder's only audio file must not be taken for a track (SPEC §28 "The sheet names the image
    /// directly"): a single-file sheet beside it names another file, or names it in a format the split can't read.</summary>
    static string? MisnamedImage(string only, List<(string Path, CueSheet? Sheet)> sheets)
    {
        foreach (var (path, sheet) in sheets)
        {
            if (sheet is null || sheet.Files.Select(LyricsKeyFold).Distinct().Count() != 1) continue;
            var named = sheet.Files[0];
            if (LyricsKeyFold(named) != LyricsKeyFold(only)) return $"{Path.GetFileName(path)} names {named}, not {only}";
            var kind = Path.GetExtension(only).TrimStart('.').ToUpperInvariant();
            return $"{(kind.Length > 0 ? kind : "This file type")} isn't supported: the split reads FLAC, WAV and AIFF";
        }
        return null;
    }

    /// <summary>What Tidy does in one folder (its subfolders aside).</summary>
    public static FolderPlan PlanFolder(string folder, FileNamesOptions names, TidyOptions tidy, CancellationToken cancel = default,
        Func<string, long, long, bool>? isSilent = null)
    {
        var audio = new Dictionary<string, string>();
        foreach (var name in AudioNames(folder)) audio[LyricsKeyFold(name)] = name;
        List<(string Path, CueSheet? Sheet)> sheets;
        try
        {
            sheets = CueSheet.ReadSheets(folder);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            sheets = [];
        }
        var images = new Dictionary<string, List<string>>();  // image name → the sheets playing it
        foreach (var (path, sheet) in sheets)
            if (sheet is not null && SheetImage(sheet, audio) is { } image)
            {
                if (!images.TryGetValue(image, out var cues)) images[image] = cues = [];
                cues.Add(path);
            }

        if (audio.Count == 1 && images.Count == 0 && MisnamedImage(audio.Values.First(), sheets) is { } misnamed)
            return new FolderPlan(folder, FolderKind.Skipped) { Skip = $"{misnamed}: {SplitByHand}", Image = audio.Values.First() };

        if (audio.Count == 1 && images.Count > 0)
        {
            var (image, cues) = images.First();
            if (cues.Count > 1)
                return new FolderPlan(folder, FolderKind.Skipped) { Skip = $"{cues.Count} sheets name {image}: {SplitByHand}", Image = image };
            var sheet = sheets.First(s => s.Path == cues[0]).Sheet!;
            AlbumPlan split;
            try
            {
                split = SplitPlanner.Plan(sheet, cues[0], names.Masks, names.Replacement, isSilent: isSilent);
            }
            catch (Exception e) when (e is not OperationCanceledException)
            {
                // an album that can't be planned is skipped, not the whole tree
                return new FolderPlan(folder, FolderKind.Skipped)
                    { Skip = $"{Path.GetFileName(cues[0])} can't be planned ({e.GetType().Name}: {e.Message}): {SplitByHand}", Image = image };
            }
            if (SplitProblem(split) is { } problem)
                return new FolderPlan(folder, FolderKind.Skipped) { Split = split, Skip = $"{problem.TrimEnd('.')}: {SplitByHand}", Image = image };
            var notSplit = new FolderPlan(folder, FolderKind.NotSplit) { Split = split, Image = image };
            notSplit.Offer.AddRange([Path.Combine(folder, image), cues[0]]);
            return notSplit;
        }

        var musicTypes = tidy.MusicTypes.Select(e => e.ToLowerInvariant()).ToHashSet();
        var renames = Rename.Plan(folder, names.Masks, names.Replacement, musicTypes, cancel);
        var plan = new FolderPlan(folder, images.Count > 0 ? FolderKind.HalfSplit : FolderKind.Split) { Renames = renames };
        foreach (var (image, cues) in images)
        {
            plan.Offer.Add(Path.Combine(folder, image));
            plan.Offer.AddRange(cues);
        }
        foreach (var status in new[] { RenameStatus.MissingTags, RenameStatus.NameTaken, RenameStatus.Unreadable })
        {
            var count = renames.With(status).Count;
            if (count > 0) plan.Notes.Add($"{count} file{(count == 1 ? "" : "s")} not renamed ({RenameItem.Label(status)})");
        }
        plan.Notes.AddRange(renames.Warnings);
        return plan;
    }

    /// <summary>Why a sheet needs a human (SPEC §28): anything the split would warn about or refuse.</summary>
    static string? SplitProblem(AlbumPlan plan)
    {
        if (plan.Refusal is not null) return plan.Refusal;
        if (plan.Warnings.Count > 0) return plan.Warnings[0];
        if (plan.Tracks.Any(t => t.Number == 0)) return "it has a hidden track before track 1";
        if (plan.Problems.Count > 0) return plan.Problems[0].Why;
        if (plan.Notes.Count > 0) return $"track {plan.Notes[0].Track}: {plan.Notes[0].Why}";
        return null;
    }

    /// <summary>The target in the ReplayGain step's comment line: a number; NaN when the template has no {target}
    /// (the line's presence is all that counts) or the number can't be read; null with no such line.</summary>
    static double? CommentTarget(string? comment, ReplayGainOptions options)
    {
        var pattern = Remove.LufsCommentPattern(options.Comment);
        foreach (var line in ReplayGain.Report.SplitLines(comment ?? ""))
        {
            if (!pattern.IsMatch(line)) continue;
            if (!options.Comment.Contains("{target}", StringComparison.Ordinal)) return double.NaN;
            var before = options.Comment.Split("{target}")[0].Trim();
            var rest = line.Trim()[before.Length..].Trim();
            var number = rest.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries).FirstOrDefault() ?? "";
            return double.TryParse(number.Replace(',', '.'), NumberStyles.Float, CultureInfo.InvariantCulture, out var target)
                ? target : double.NaN;
        }
        return null;
    }

    /// <summary>Why the album needs a ReplayGain run, or null when every track rsgain scans (the images aside) has the
    /// four values and the comment for the current target, and its disc subfolders' tracks have the album values.</summary>
    public static string? ReplayGainNeeded(string album, IEnumerable<string> discs, ReplayGainOptions options,
        IEnumerable<string> exclude)
    {
        var skip = exclude.Select(LyricsKeyFold).ToHashSet();
        var files = Album.ListAudio(album).Supported.Where(n => !skip.Contains(LyricsKeyFold(n))).ToList();
        if (files.Count == 0) return null;
        var targets = new List<double>();
        foreach (var name in files)
        {
            string?[] values;
            string? comment;
            try
            {
                using var tags = RgTags.Open(Path.Combine(album, name));
                values = [tags.Get(RgTags.TrackGain), tags.Get(RgTags.TrackPeak), tags.Get(RgTags.AlbumGain), tags.Get(RgTags.AlbumPeak)];
                comment = tags.Comment;
            }
            catch (Exception)
            {
                return $"{name} can't be read";
            }
            if (values.Any(v => v is null)) return "missing tags";
            if (CommentTarget(comment, options) is not { } target) return "no target in the comment";
            targets.Add(target);
        }
        var others = targets.Where(t => !double.IsNaN(t) && Math.Abs(t - options.Target) > 1e-6).Distinct().Order().ToList();
        if (others.Count > 0) return $"target {string.Join(", ", others.Select(Python.G))} LUFS, now {options.TargetText}";
        foreach (var disc in discs)
            foreach (var name in Album.ListAudio(disc).Supported)
            {
                try
                {
                    using var tags = RgTags.Open(Path.Combine(disc, name));
                    if (tags.Get(RgTags.AlbumGain) is null || tags.Get(RgTags.AlbumPeak) is null)
                        return $"missing tags in {Path.GetFileName(disc)}";
                }
                catch (Exception)
                {
                    return $"{Path.GetFileName(disc)}\\{name} can't be read";
                }
            }
        return null;
    }

    /// <summary>Tidy's dry run over root and every subfolder (SPEC §28). Throws OperationCanceledException on a
    /// cancel.</summary>
    public static TidyPlan Plan(string root, FileNamesOptions names, ReplayGainOptions replayGain, TidyOptions tidy,
        CancellationToken cancel = default, Action<int, int, string>? progress = null, Func<string, long, long, bool>? isSilent = null)
    {
        var plan = new TidyPlan(root);
        var found = FindAlbums(root, replayGain);
        for (var n = 0; n < found.Count; n++)
        {
            cancel.ThrowIfCancellationRequested();
            var (folder, discs) = found[n];
            progress?.Invoke(n, found.Count, folder);
            var album = new AlbumTidy(folder);
            foreach (var each in new[] { folder }.Concat(discs)) album.Folders.Add(PlanFolder(each, names, tidy, cancel, isSilent));
            var own = album.Own;
            album.ReplayGainExclude = own.Images;
            var scanned = Album.ListAudio(folder).Supported;
            var tracks = scanned.Where(n2 => !own.Images.Contains(n2)).ToList();
            if (own.Kind == FolderKind.Skipped && tracks.Count == 0)
            {
                // an image waiting to be split: nothing to measure yet
            }
            else if (scanned.Count == 0)
                own.Notes.Add("no file here is a format ReplayGain reads");  // renamed all the same, never measured
            else if (album.Folders.Any(f => f.Kind == FolderKind.NotSplit))
                album.ReplayGain = "after the split";
            else
                album.ReplayGain = ReplayGainNeeded(folder, album.Folders.Skip(1).Select(f => f.Folder), replayGain, own.Images);
            plan.Albums.Add(album);
        }
        PlaceClutter(plan, Clutter.Find(root, ClutterLists.Of(tidy), cancel), replayGain);
        cancel.ThrowIfCancellationRequested();
        progress?.Invoke(found.Count, found.Count, "");
        return plan;
    }

    /// <summary>Several folders as one plan (the right-click menu, SPEC §31): each planned as Tidy on it.</summary>
    public static TidyPlan Plan(IReadOnlyList<string> roots, FileNamesOptions names, ReplayGainOptions replayGain,
        TidyOptions tidy, CancellationToken cancel = default, Action<int, int, string>? progress = null,
        Func<string, long, long, bool>? isSilent = null)
    {
        var folders = roots.Distinct(StringComparer.OrdinalIgnoreCase).ToList();
        if (folders.Count == 1) return Plan(folders[0], names, replayGain, tidy, cancel, progress, isSilent);
        var merged = new TidyPlan(CommonFolder(folders)) { Roots = folders };
        foreach (var root in folders)
        {
            var part = Plan(root, names, replayGain, tidy, cancel, progress, isSilent);
            merged.Albums.AddRange(part.Albums);
            merged.Loose.AddRange(part.Loose);
        }
        return merged;
    }

    /// <summary>The deepest folder holding them all; "" on several drives.</summary>
    static string CommonFolder(List<string> folders)
    {
        var parts = folders.Select(f => f.TrimEnd('\\').Split('\\')).ToList();
        var common = new List<string>();
        for (var i = 0; parts.All(p => p.Length > i) && parts.All(p => p[i].Equals(parts[0][i], StringComparison.OrdinalIgnoreCase)); i++)
            common.Add(parts[0][i]);
        return common.Count == 0 ? "" : common.Count == 1 ? common[0] + "\\" : string.Join("\\", common);
    }

    /// <summary>Each item under its album (its folder, or one of the album's keyword subfolders and what they hold),
    /// else with the loose ones.</summary>
    static void PlaceClutter(TidyPlan plan, List<OfferItem> items, ReplayGainOptions options)
    {
        var owners = new List<(string Folder, AlbumTidy Album)>();
        foreach (var album in plan.Albums)
        {
            owners.Add((album.Folder.ToUpperInvariant(), album));
            try
            {
                owners.AddRange(Album.KeywordSubfolders(album.Folder, options).Select(n => (Path.Combine(album.Folder, n).ToUpperInvariant(), album)));
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException)
            {
            }
        }
        foreach (var item in items)
        {
            var parent = Path.GetDirectoryName(item.Path)!.ToUpperInvariant();
            var owner = owners.FirstOrDefault(o => parent == o.Folder ||
                parent.StartsWith(o.Folder.TrimEnd('\\') + "\\", StringComparison.Ordinal) && o.Folder != o.Album.Folder.ToUpperInvariant()).Album;
            if (owner is not null) owner.Clutter.Add(item);
            else plan.Loose.Add(item);
        }
    }

    /// <summary>A split or half-split folder's image and sheets, as rows of the redundant files list.</summary>
    public static List<OfferItem> OfferItems(FolderPlan folder)
    {
        var image = folder.Offer.Select(p => Path.GetFileName(p)!).FirstOrDefault(n => !n.EndsWith(".cue", StringComparison.OrdinalIgnoreCase)) ?? "the image";
        var imageWhy = folder.Kind == FolderKind.NotSplit && folder.Split is { } split
            ? $"image split into {split.Written.Count} tracks"
            : "image whose tracks are already split";
        return folder.Offer.Select(p => new OfferItem(p, Path.GetExtension(p).Equals(".cue", StringComparison.OrdinalIgnoreCase)
            ? $"cue sheet of {image}" : imageWhy)).ToList();
    }
}

/// <summary>The plan in words, as the Python app's Tidy page says it (SPEC §28 "The plan page").</summary>
public static class TidyText
{
    /// <summary>What Tidy does in one folder.</summary>
    public static List<string> FolderActions(FolderPlan plan)
    {
        if (plan.Kind == FolderKind.Skipped) return [$"Skipped: {plan.Skip}"];
        var actions = new List<string>();
        if (plan.Kind == FolderKind.NotSplit && plan.Split is { } split)
            actions.Add($"Split {Path.GetFileName(split.Cue)} into {ReplayGain.Report.Plural(split.Written.Count, "track")}");
        if (plan.RenameCount > 0) actions.Add($"Rename {ReplayGain.Report.Plural(plan.RenameCount, "file")}");
        return actions;
    }

    /// <summary>"Offer 3 pictures, 1 video, 1 folder".</summary>
    public static string ClutterText(IReadOnlyList<OfferItem> items)
    {
        (int Count, string Noun)[] counts =
        [
            (items.Count(i => i.Why == Clutter.PictureBesideMusic), "picture"), (items.Count(i => i.Why == Clutter.VideoBesideMusic), "video"),
            (items.Count(i => i.IsFolder), "folder"),
        ];
        return "Offer " + string.Join(", ", counts.Where(c => c.Count > 0).Select(c => ReplayGain.Report.Plural(c.Count, c.Noun)));
    }

    public static string AlbumText(AlbumTidy album)
    {
        if (album.IsTidy) return "Already tidy";
        var actions = FolderActions(album.Own);
        if (album.Folders.Skip(1).Any(d => FolderActions(d).Count > 0)) actions.Add("work in its disc folders");
        if (album.ReplayGain is not null) actions.Add($"ReplayGain ({album.ReplayGain})");
        var text = string.Join(", then ", actions);
        if (album.Clutter.Count > 0)
        {
            var offer = ClutterText(album.Clutter);
            text = text.Length > 0 ? $"{text}; {char.ToLowerInvariant(offer[0])}{offer[1..]}" : offer;
        }
        return text.Length > 0 ? text : "Nothing to do";
    }

    /// <summary>A loose clutter folder's line under its parent: "Offer empty folder Scans", "Offer folder Artwork (12
    /// pictures)".</summary>
    public static string LooseText(OfferItem item) => item.Why == "empty folder"
        ? $"Offer empty folder {Path.GetFileName(item.Path)}"
        : $"Offer folder {Path.GetFileName(item.Path)} ({(item.Why.StartsWith("folder of ", StringComparison.Ordinal) ? item.Why["folder of ".Length..] : item.Why)})";

    /// <summary>The line under the summary, once the plan is read.</summary>
    public const string RunNote = "Run Tidy does all of this, album by album. Files made redundant by a split (the " +
                                  "image and its cue sheet) and the files offered are kept; you choose which to recycle at the end.";

    /// <summary>What the redundant files list says first, under Tidy's report.</summary>
    public const string OfferIntro = "Tidy made these files redundant (split images and their cue sheets), or found them beside the music (pictures, videos, folders holding nothing else). Every box is " +
        "checked: uncheck what to keep, then click Delete selected to send the rest to the Recycle Bin. Closing this " +
        "window keeps them all.";
}
