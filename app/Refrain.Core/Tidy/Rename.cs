using System.Text;
using System.Text.RegularExpressions;
using Refrain.Core.Options;
using Refrain.Core.Tags;

namespace Refrain.Core.Tidy;

public enum RenameStatus { Rename, Unchanged, MissingTags, NameTaken, Unreadable, Kept }

/// <summary>One file of a rename plan; <see cref="Sidecar"/> is the .lrc that follows an audio rename.</summary>
public sealed class RenameItem(string source, RenameStatus status, string? target = null, string detail = "")
{
    public string Source { get; } = source;
    public RenameStatus Status { get; } = status;
    public string? Target { get; } = target;
    public string Detail { get; } = detail;  // missing fields, reason kept, or error
    public string? FallbackMissing { get; set; }  // set when a fallback mask was used
    public RenameItem? Sidecar { get; set; }
    public bool IsAudio { get; set; }
    public long Size { get; private set; }
    public DateTime Mtime { get; private set; }

    /// <summary>The file's size and time now, so the run renames only what the plan saw.</summary>
    public RenameItem Snapshot()
    {
        try
        {
            var info = new FileInfo(Source);
            if (info.Exists) (Size, Mtime) = (info.Length, info.LastWriteTimeUtc);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
        }
        return this;
    }

    public bool UnchangedSincePlan()
    {
        var info = new FileInfo(Source);
        return info.Exists && info.Length == Size && info.LastWriteTimeUtc == Mtime;
    }

    public static string Label(RenameStatus status) => status switch
    {
        RenameStatus.MissingTags => "missing tags",
        RenameStatus.NameTaken => "name taken",
        _ => status.ToString().ToLowerInvariant(),
    };
}

public sealed class RenamePlan(string folder)
{
    public string Folder { get; } = folder;
    public List<RenameItem> Items { get; } = [];
    public List<string> Warnings { get; } = [];

    public List<RenameItem> With(RenameStatus status) => Items.Where(i => i.Status == status).ToList();
}

/// <summary>What a rename run did (the script's sections, the same entry texts).</summary>
public sealed class RenameReport
{
    public int Scanned { get; set; }
    public List<string> Renamed { get; } = [];
    public List<string> Fallback { get; } = [];
    public List<string> MissingTags { get; } = [];
    public List<string> NameTaken { get; } = [];
    public List<string> Errors { get; } = [];
    public List<string> Kept { get; } = [];
    public List<string> SidecarsRenamed { get; } = [];
    public List<string> Skipped { get; } = [];  // changed since the plan
    public List<string> Warnings { get; } = [];
    public bool Cancelled { get; set; }
}

/// <summary>Renaming music files from their tags (SPEC §10, §21), Tidy's rename step: ported from the Python app's
/// <c>core/rename.py</c>. <see cref="Plan"/> is the dry run, simulating the renames on a virtual copy of the folder
/// so "name taken" comes out as the script reported it; <see cref="Apply"/> runs exactly that plan, re-checking each
/// file. Renaming never deletes anything.</summary>
public static partial class Rename
{
    [GeneratedRegex(@"^\s*FILE\s+(?:""([^""]*)""|(\S+))", RegexOptions.IgnoreCase)]
    private static partial Regex CueFile();

    [GeneratedRegex(@"^\s*TRACK\s+\d+", RegexOptions.IgnoreCase)]
    private static partial Regex CueTrack();

    /// <summary>(FILE names, TRACK count) of a cue sheet: UTF-8, else Latin-1 (enough for FILE and TRACK lines).</summary>
    public static (List<string> Files, int Tracks) ReadCue(string path)
    {
        var raw = File.ReadAllBytes(path);
        string text;
        try
        {
            var start = raw.AsSpan().StartsWith((byte[])[0xEF, 0xBB, 0xBF]) ? 3 : 0;
            text = new UTF8Encoding(false, true).GetString(raw, start, raw.Length - start);
        }
        catch (DecoderFallbackException)
        {
            text = Encoding.Latin1.GetString(raw);
        }
        var files = new List<string>();
        var tracks = 0;
        foreach (var line in ReplayGain.Report.SplitLines(text))
        {
            var match = CueFile().Match(line);
            if (match.Success) files.Add(match.Groups[1].Success ? match.Groups[1].Value : match.Groups[2].Value);
            else if (CueTrack().IsMatch(line)) tracks++;
        }
        return (files, tracks);
    }

    /// <summary>What the plan needs to know about a folder before walking it.</summary>
    sealed class FolderFacts
    {
        public Dictionary<string, string> SingleFileCues { get; } = [];  // lowercased audio name → cue name
        public Dictionary<string, List<string>> CueFiles { get; } = [];  // cue name → the file names it lists
        public Dictionary<string, string> Sidecars { get; } = [];  // lowercased audio name → its .lrc
    }

    static FolderFacts Facts(List<string> files, IReadOnlySet<string> musicTypes)
    {
        var facts = new FolderFacts();
        var byLower = new Dictionary<string, string>();
        foreach (var file in files) byLower[Path.GetFileName(file).ToLowerInvariant()] = file;
        var audio = files.Where(f => musicTypes.Contains(Path.GetExtension(f).ToLowerInvariant())).ToList();
        foreach (var cue in files.Where(f => Path.GetExtension(f).Equals(".cue", StringComparison.OrdinalIgnoreCase)))
        {
            List<string> names;
            int tracks;
            try
            {
                (names, tracks) = ReadCue(cue);
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException)
            {
                continue;
            }
            facts.CueFiles[Path.GetFileName(cue)] = names;
            var distinct = names.Select(n => n.ToLowerInvariant()).Distinct().ToList();
            if (distinct.Count == 1 && tracks >= 2 && byLower.TryGetValue(distinct[0], out var target) &&
                musicTypes.Contains(Path.GetExtension(target).ToLowerInvariant()))
                facts.SingleFileCues[Path.GetFileName(target).ToLowerInvariant()] = Path.GetFileName(cue);
        }
        foreach (var lrc in files.Where(f => Path.GetExtension(f).Equals(".lrc", StringComparison.OrdinalIgnoreCase)))
        {
            var stem = Path.GetFileNameWithoutExtension(lrc).ToLowerInvariant();
            var owners = audio.Where(a => Path.GetFileNameWithoutExtension(a).ToLowerInvariant() == stem).ToList();
            if (owners.Count == 1) facts.Sidecars[Path.GetFileName(owners[0]).ToLowerInvariant()] = lrc;
        }
        return facts;
    }

    /// <summary>The names in a folder as they will be after the planned renames so far (ignoring case, as Windows).</summary>
    sealed class FolderState(IEnumerable<string> names)
    {
        readonly HashSet<string> names = names.Select(n => n.ToLowerInvariant()).ToHashSet();

        // A case-only rename points at the same file: not a clash.
        public bool Taken(string old, string @new) =>
            names.Contains(@new.ToLowerInvariant()) && @new.ToLowerInvariant() != old.ToLowerInvariant();

        public void Move(string old, string @new)
        {
            names.Remove(old.ToLowerInvariant());
            names.Add(@new.ToLowerInvariant());
        }
    }

    /// <summary>The dry run on one folder (its subfolders aside, as Tidy plans each folder): nothing on disk changes.
    /// <paramref name="musicTypes"/>: the extensions renamed (Tidy's "music file types").</summary>
    public static RenamePlan Plan(string folder, IReadOnlyList<string> masks, string replacement,
        IReadOnlySet<string> musicTypes, CancellationToken cancel = default)
    {
        var plan = new RenamePlan(folder);
        var entries = Directory.EnumerateFileSystemEntries(folder).ToList();  // in the order Windows lists them
        var files = entries.Where(File.Exists).ToList();
        var facts = Facts(files, musicTypes);
        var state = new FolderState(entries.Select(e => Path.GetFileName(e)!));
        var renamed = new HashSet<string>();
        foreach (var path in files)
        {
            cancel.ThrowIfCancellationRequested();
            if (!musicTypes.Contains(Path.GetExtension(path).ToLowerInvariant())) continue;
            var item = PlanAudio(path, masks, replacement, facts, state, plan);
            item.IsAudio = true;
            plan.Items.Add(item);
            if (item.Status == RenameStatus.Rename) renamed.Add(Path.GetFileName(path).ToLowerInvariant());
        }
        foreach (var (cue, names) in facts.CueFiles)
        {
            if (facts.SingleFileCues.ContainsValue(cue)) continue;
            if (names.Any(n => renamed.Contains(n.ToLowerInvariant())))
                plan.Warnings.Add($"{cue} lists files that will be renamed: it won't match them anymore");
        }
        return plan;
    }

    static RenameItem PlanAudio(string path, IReadOnlyList<string> masks, string replacement, FolderFacts facts,
        FolderState state, RenamePlan plan)
    {
        var name = Path.GetFileName(path);
        if (facts.SingleFileCues.TryGetValue(name.ToLowerInvariant(), out var cue))
            return new RenameItem(path, RenameStatus.Kept, detail: $"used by {cue}").Snapshot();
        Dictionary<string, object> fields;
        try
        {
            fields = RenameFields.Of(TagFile.Read(path));
        }
        catch (Exception e)
        {
            return new RenameItem(path, RenameStatus.Unreadable, detail: $"could not read the tags ({e.Message})").Snapshot();
        }
        var values = fields.ToDictionary(kv => kv.Key, kv => kv.Value is string s ? (object)Masks.Sanitize(s, replacement) : kv.Value);
        if (Masks.NameFor(fields, masks, replacement, Path.GetExtension(path)) is not var (newName, maskIndex))
            return new RenameItem(path, RenameStatus.MissingTags, detail: Masks.MissingFrom(masks[0], values)).Snapshot();
        var target = Path.Combine(Path.GetDirectoryName(path)!, newName);
        if (name == newName) return new RenameItem(path, RenameStatus.Unchanged).Snapshot();
        if (state.Taken(name, newName)) return new RenameItem(path, RenameStatus.NameTaken, target).Snapshot();

        var item = new RenameItem(path, RenameStatus.Rename, target).Snapshot();
        if (maskIndex > 0) item.FallbackMissing = Masks.MissingFrom(masks[0], values);
        state.Move(name, newName);
        if (facts.Sidecars.TryGetValue(name.ToLowerInvariant(), out var lrc))
        {
            var lrcName = Path.GetFileNameWithoutExtension(newName) + Path.GetExtension(lrc);
            var lrcTarget = Path.Combine(Path.GetDirectoryName(lrc)!, lrcName);
            if (Path.GetFileName(lrc) == lrcName)
            {
            }
            else if (state.Taken(Path.GetFileName(lrc), lrcName))
            {
                item.Sidecar = new RenameItem(lrc, RenameStatus.NameTaken, lrcTarget).Snapshot();
                plan.Warnings.Add($"{Path.GetFileName(lrc)} stays: {lrcName} already exists");
            }
            else
            {
                item.Sidecar = new RenameItem(lrc, RenameStatus.Rename, lrcTarget).Snapshot();
                state.Move(Path.GetFileName(lrc), lrcName);
            }
        }
        return item;
    }

    static bool TargetFree(RenameItem item) =>
        !Path.Exists(item.Target) || RealPath.SameFile(item.Source, item.Target!);

    /// <summary>Runs exactly the planned renames, re-checking each one.</summary>
    public static RenameReport Apply(RenamePlan plan, CancellationToken cancel = default)
    {
        var report = new RenameReport();
        report.Warnings.AddRange(plan.Warnings);
        foreach (var item in plan.Items)
        {
            if (cancel.IsCancellationRequested)
            {
                report.Cancelled = true;
                break;
            }
            var name = Path.GetFileName(item.Source);
            if (item.IsAudio) report.Scanned++;
            switch (item.Status)
            {
                case RenameStatus.MissingTags:
                    report.MissingTags.Add($"{name} (missing: {item.Detail})");
                    break;
                case RenameStatus.NameTaken:
                    report.NameTaken.Add($"{name} -> {Path.GetFileName(item.Target)}");
                    break;
                case RenameStatus.Unreadable:
                    report.Errors.Add($"{name}: {item.Detail}");
                    break;
                case RenameStatus.Kept:
                    report.Kept.Add($"{name} (kept: {item.Detail})");
                    break;
                case RenameStatus.Rename:
                    ApplyOne(item, report);
                    break;
            }
        }
        return report;
    }

    static void ApplyOne(RenameItem item, RenameReport report)
    {
        var (name, newName) = (Path.GetFileName(item.Source), Path.GetFileName(item.Target!));
        if (!item.UnchangedSincePlan())
        {
            report.Skipped.Add($"{name}: changed since the preview, not renamed");
            return;
        }
        if (!TargetFree(item))
        {
            report.NameTaken.Add($"{name} -> {newName}");
            return;
        }
        try
        {
            File.Move(item.Source, item.Target!);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            report.Errors.Add($"{name}: could not be renamed to {newName} ({e.Message})");
            return;
        }
        report.Renamed.Add(newName);
        if (item.FallbackMissing is not null) report.Fallback.Add($"{newName} (missing: {item.FallbackMissing})");

        if (item.Sidecar is not { Status: RenameStatus.Rename } sidecar) return;
        var (lrcName, lrcNew) = (Path.GetFileName(sidecar.Source), Path.GetFileName(sidecar.Target!));
        if (!sidecar.UnchangedSincePlan() || !TargetFree(sidecar))
        {
            report.Warnings.Add($"{lrcName} was not renamed: it changed or {lrcNew} exists now");
            return;
        }
        try
        {
            File.Move(sidecar.Source, sidecar.Target!);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            report.Warnings.Add($"{lrcName} could not be renamed ({e.Message})");
            return;
        }
        report.SidecarsRenamed.Add($"{lrcName} -> {lrcNew}");
    }
}
