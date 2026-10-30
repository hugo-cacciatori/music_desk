using System.Globalization;
using System.Security.Cryptography;
using System.Text.RegularExpressions;

namespace Refrain.Core.Cue;

/// <summary>What a trim would write, or why it can't (SPEC §23): everything the Trim window shows and checks. Nothing
/// on disk changes while planning.</summary>
public sealed class TrimPlan(string source, SourceInfo info, string subfolder)
{
    public string Source { get; } = source;
    public SourceInfo Info { get; } = info;
    public long Start { get; set; }  // the first kept sample
    public long End { get; set; }  // one past the last kept sample
    public string Subfolder { get; } = subfolder;
    public string Name { get; set; } = "";  // the copy's file name in the subfolder
    public string? Note { get; set; }  // why the name was numbered
    /// <summary>(field, why): field is "start", "end", "subfolder", or null for the whole edit ("Nothing to trim").</summary>
    public List<(string? Field, string Why)> Problems { get; } = [];

    public string Folder => Path.Combine(Path.GetDirectoryName(Source)!, Subfolder);
    public string CopyPath => Path.Combine(Folder, Name);
    /// <summary>The copy's path from the track's folder, as the window shows it: <c>lite\03 Song.flac</c>.</summary>
    public string ShownPath => $"{Subfolder}\\{Name}";
    public long Length => End - Start;
    public bool Ok => Problems.Count == 0;
    public string? Refusal => Problems.Count > 0 ? Problems[0].Why : null;
    /// <summary>Start and End are valid, which is all Preview in player needs (the subfolder doesn't matter).</summary>
    public bool TimesOk => !Problems.Any(p => p.Field is "start" or "end");
    /// <summary>The first problem of one field, for marking it.</summary>
    public string? Problem(string? field) => Problems.FirstOrDefault(p => p.Field == field).Why;

    /// <summary>"Keeps 3:41.200. Removes 0:02.350 at the start and 5:12.000 at the end."</summary>
    public string Summary
    {
        get
        {
            var rate = Info.Rate;
            var keeps = $"Keeps {Trim.FormatTime(Trim.ToMs(Length, rate))}.";
            var removed = new[] { (Start, "start"), (Info.Samples - End, "end") }.Where(p => p.Item1 > 0)
                .Select(p => $"{Trim.FormatTime(Trim.ToMs(p.Item1, rate))} at the {p.Item2}").ToList();
            return removed.Count > 0 ? $"{keeps} Removes {string.Join(" and ", removed)}." : keeps;
        }
    }
}

/// <summary>The cut's outcome: the proved .partial, or why nothing was written.</summary>
public sealed class TrimCut
{
    public bool Ok { get; init; }
    public bool Cancelled { get; set; }
    public string? Error { get; set; }  // flac's message, a failed proof, or why the trim wasn't started
    public string? Partial { get; init; }  // the proved copy, when ok
    public bool CreatedFolder { get; init; }  // this run made the subfolder (removed on a failure while empty)
    public long Samples { get; set; }  // in the copy, as decoded
    public string? Md5 { get; set; }  // of the copy's audio, equal to its range of the original when ok
}

/// <summary>The copy under its name, or why it wasn't given one.</summary>
public sealed class TrimFinished
{
    public string? Path { get; init; }
    public string? Error { get; init; }  // the name was taken meanwhile: nothing renamed, the .partial is gone
    public List<string> Warnings { get; } = [];
}

/// <summary>Trimming one track (SPEC §23), ported from the Python app's <c>core/trim.py</c>: a FLAC copy of the part
/// between a new start and a new end, written into a subfolder of the track's folder. The original is never touched.
/// A trim is a split of one track where only one part is kept, so it cuts and proves as the split does (flac's
/// <c>--skip</c>/<c>--until</c>, then the copy's length and the MD5 of its audio against the same range of the
/// original). "&lt;name&gt;.partial" first; on a failure or a Stop it is deleted, and the subfolder too if this run
/// created it and it is still empty (Ground rule 1).</summary>
public static partial class Trim
{
    public const string DefaultSubfolder = "lite";
    public const string Owner = "Trim";
    public const int NudgeMs = 10, PageMs = 1000;  // Up / Down, Page Up / Page Down in the time fields
    public const string CutStartTitle = "Cut from the start", CutEndTitle = "Cut from the end";  // the preview's parts

    /// <summary>Lyrics are never copied, synced or not: the app's lyrics come from LRCLIB, and synced lyrics would no
    /// longer match once the start moves.</summary>
    static readonly HashSet<string> LyricsKeys = new(StringComparer.OrdinalIgnoreCase) { "lyrics", "unsyncedlyrics", "syncedlyrics" };

    static readonly HashSet<string> Reserved = new(
        new[] { "CON", "PRN", "AUX", "NUL" }.Concat(Enumerable.Range(1, 9).SelectMany(n => new[] { $"COM{n}", $"LPT{n}" })));

    [GeneratedRegex(@"^(\d+):([0-5]?\d)(?:\.(\d{1,3}))?$")]
    private static partial Regex TimePattern();

    [GeneratedRegex("[\\\\/:*?\"<>|\\x00-\\x1f]")]
    private static partial Regex FolderForbidden();

    // -- times --

    /// <summary><c>m:ss.mmm</c> (minutes may pass 59; <c>m:ss</c> and fewer decimals are fine) in milliseconds, or null.</summary>
    public static long? ParseTime(string text)
    {
        var match = TimePattern().Match(text.Trim());
        if (!match.Success) return null;
        var fraction = match.Groups[3].Success ? match.Groups[3].Value : "0";
        return (long.Parse(match.Groups[1].Value, CultureInfo.InvariantCulture) * 60 + int.Parse(match.Groups[2].Value, CultureInfo.InvariantCulture)) * 1000
               + int.Parse(fraction.PadRight(3, '0'), CultureInfo.InvariantCulture);
    }

    public static string FormatTime(long ms)
    {
        var (seconds, millis) = (ms / 1000, ms % 1000);
        return $"{seconds / 60}:{seconds % 60:00}.{millis:000}";
    }

    /// <summary>sample = round(seconds · rate), halves rounded up, in integers so it is exact.</summary>
    public static long ToSamples(long ms, int rate) => (ms * rate * 2 + 1000) / 2000;

    public static long ToMs(long samples, int rate) => (samples * 2000 + rate) / (2L * rate);

    /// <summary>The track's length as the window shows it, and the End field's starting value.</summary>
    public static long LengthMs(SourceInfo info) => ToMs(info.Samples, info.Rate);

    /// <summary>"FLAC 16-bit/44.1 kHz · 9:47.213".</summary>
    public static string FormatLine(SourceInfo info) =>
        $"{info.Format} {info.Bits}-bit/{Options.Python.G(info.Rate / 1000.0)} kHz · {FormatTime(LengthMs(info))}";

    // -- the source --

    /// <summary>Why trimming can't read this file, from its extension alone (the button's hover text), or null.</summary>
    public static string? UnsupportedReason(string path)
    {
        var ext = Path.GetExtension(path);
        if (SplitPlanner.Supported.ContainsKey(ext)) return null;
        var name = ext.TrimStart('.').ToUpperInvariant();
        return $"{(name.Length > 0 ? name : "This file type")} isn't supported: trimming reads FLAC, WAV and AIFF.";
    }

    /// <summary>What the trim needs of the track. Throws CueException with the reason (another format, 32-bit float
    /// WAV), IOException.</summary>
    public static SourceInfo ReadSource(string path) =>
        UnsupportedReason(path) is { } reason ? throw new CueException(reason) : SplitPlanner.ReadSource(path);

    // -- the plan --

    /// <summary>Checks the window's values and works out the copy. <paramref name="start"/> and <paramref name="end"/>
    /// are the fields' text; <paramref name="existing"/> the subfolder's listing (null: read it, nothing when missing).</summary>
    public static TrimPlan Plan(string source, SourceInfo info, string start, string end, string subfolder, IEnumerable<string>? existing = null)
    {
        var plan = new TrimPlan(source, info, subfolder);
        var total = LengthMs(info);
        var (startMs, endMs) = (ParseTime(start), ParseTime(end));
        if (startMs is null) plan.Problems.Add(("start", "Type the start as m:ss.mmm, for example 0:02.350."));
        else if (startMs >= total) plan.Problems.Add(("start", $"The start is past the track's length ({FormatTime(total)})."));
        if (endMs is null) plan.Problems.Add(("end", "Type the end as m:ss.mmm, for example 3:43.550."));
        else if (endMs > total) plan.Problems.Add(("end", $"The end is past the track's length ({FormatTime(total)})."));
        if (plan.Problems.Count == 0)
        {
            plan.Start = ToSamples(startMs!.Value, info.Rate);
            // The length as shown is rounded to the millisecond: an End left at it means the very last sample.
            plan.End = endMs == total ? info.Samples : Math.Min(ToSamples(endMs!.Value, info.Rate), info.Samples);
            if (plan.Start >= plan.End) plan.Problems.Add(("end", "The start must be before the end."));
            else if (plan.Start == 0 && plan.End == info.Samples) plan.Problems.Add((null, "Nothing to trim: move a handle."));
        }

        if (CheckSubfolder(Path.GetDirectoryName(source)!, subfolder) is { } why)
        {
            plan.Problems.Add(("subfolder", why));
            return plan;
        }
        if (existing is null)
        {
            try
            {
                existing = Directory.Exists(plan.Folder) ? Directory.EnumerateFileSystemEntries(plan.Folder).Select(p => Path.GetFileName(p)!).ToList() : [];
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException)
            {
                existing = [];
            }
        }
        var wanted = Path.GetFileNameWithoutExtension(source) + ".flac";
        plan.Name = SplitPlanner.FreeName(wanted, existing.Select(Tags.LyricsKey.CaseFold).ToHashSet());
        if (plan.Name != wanted) plan.Note = $"{wanted} is already in {subfolder}, so the copy is named {plan.Name}.";
        foreach (var path in new[] { plan.CopyPath, PartialPath(plan) })
            if (path.Length > SplitPlanner.MaxPath)
            {
                plan.Problems.Add(("subfolder", $"The copy's path would have {path.Length} characters, longer than Windows tools can open " +
                                                $"({SplitPlanner.MaxPath}). Shorten the subfolder's name."));
                break;
            }
        return plan;
    }

    /// <summary>Preview in player (SPEC §23): the track as up to three parts, Cut from the start, "&lt;title&gt; (kept)"
    /// and Cut from the end; a part of no length is left out. The plan's times must be valid.</summary>
    public static string PreviewText(TrimPlan plan, string title = "", string artist = "", string album = "")
    {
        var kept = $"{(title.Trim().Length > 0 ? title.Trim() : Path.GetFileNameWithoutExtension(plan.Source))} (kept)";
        var parts = new[] { (0L, plan.Start, CutStartTitle), (plan.Start, plan.End, kept), (plan.End, plan.Info.Samples, CutEndTitle) };
        var tracks = parts.Where(p => p.Item2 > p.Item1).Select(p => (p.Item1, p.Item3, artist)).ToList();
        return Playlists.RenderPreview(plan.Source, plan.Info.Rate, plan.Info.Samples, tracks, album, artist);
    }

    /// <summary>Why <paramref name="name"/> can't be the subfolder of <paramref name="folder"/> (null if it can). An
    /// existing folder of that name is fine.</summary>
    public static string? CheckSubfolder(string folder, string name)
    {
        if (CheckSubfolderName(name) is { } why) return why;
        return File.Exists(Path.Combine(folder, name)) ? $"{name} is a file in this folder, not a folder." : null;
    }

    /// <summary>Why <paramref name="name"/> can't be a folder's name on Windows, wherever it goes (the Options
    /// window's Trim page).</summary>
    public static string? CheckSubfolderName(string name)
    {
        if (name.Trim().Length == 0) return "Type the subfolder's name.";
        if (FolderForbidden().IsMatch(name)) return "A folder name can't contain any of \\ / : * ? \" < > |.";
        if (name[^1] is ' ' or '.') return "A folder name can't end with a dot or a space.";
        if (Reserved.Contains(name.Split('.')[0].Trim().ToUpperInvariant())) return $"{name} is a name Windows reserves.";
        return null;
    }

    // -- the cut and its proof --

    public static string PartialPath(TrimPlan plan) => plan.CopyPath + Split.Partial;

    /// <summary>Writes and proves the kept range as "&lt;name&gt;.partial" in the subfolder, creating it if missing.
    /// Nothing else is touched; on a failure or a Stop, the folder is left as it was.</summary>
    public static TrimCut Cut(TrimPlan plan, Flac flac, CancellationToken cancel = default, Func<string, long>? freeSpace = null)
    {
        if (!plan.Ok) return new TrimCut { Error = plan.Refusal };
        freeSpace ??= folder => new DriveInfo(Path.GetPathRoot(Path.GetFullPath(folder))!).AvailableFreeSpace;
        var needed = (long)Math.Ceiling((double)new FileInfo(plan.Source).Length * plan.Length / Math.Max(plan.Info.Samples, 1));
        var free = freeSpace(Path.GetDirectoryName(plan.Source)!);
        if (free < needed)
            return new TrimCut { Error = $"Not enough free space: the copy needs about {Split.Megabytes(needed)}, and the drive has {Split.Megabytes(free)} free." };
        var output = PartialPath(plan);
        if (File.Exists(output))
            return new TrimCut { Error = $"{plan.Subfolder}\\{Path.GetFileName(output)} already exists, maybe left by an earlier trim. Delete it in File Explorer, then trim again." };
        var created = !Directory.Exists(plan.Folder);
        try
        {
            Directory.CreateDirectory(plan.Folder);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            return new TrimCut { Error = $"The subfolder {plan.Subfolder} can't be created ({e.Message})." };
        }
        var result = new TrimCut { CreatedFolder = created, Partial = output };
        string? problem;
        try
        {
            flac.Encode(plan.Source, plan.Start, plan.End == plan.Info.Samples ? null : plan.End, output, cancel);
            problem = Prove(plan, output, flac, result, cancel);
        }
        catch (FlacCancelledException)
        {
            (problem, result.Cancelled) = ("Stopped.", true);
        }
        catch (FlacException e)
        {
            problem = $"flac: {e.Message}";
        }
        if (problem is not null)
        {
            Undo(plan, output, created);
            return new TrimCut { Error = problem, Cancelled = result.Cancelled, CreatedFolder = created, Samples = result.Samples, Md5 = result.Md5 };
        }
        return new TrimCut { Ok = true, Partial = output, CreatedFolder = created, Samples = result.Samples, Md5 = result.Md5 };
    }

    /// <summary>The copy's sample count must be the kept range's length, and its audio's MD5 that of the same range of
    /// the original, decoded by flac (the split's per-track proof). Returns why it failed, or null.</summary>
    public static string? Prove(TrimPlan plan, string copy, Flac flac, TrimCut result, CancellationToken cancel = default)
    {
        var info = plan.Info;
        var frameBytes = info.Channels * ((info.Bits + 7) / 8);
        using var own = IncrementalHash.CreateHash(HashAlgorithmName.MD5);
        using var part = IncrementalHash.CreateHash(HashAlgorithmName.MD5);
        long size = 0;
        foreach (var chunk in flac.Decode(copy, cancel))
        {
            own.AppendData(chunk);
            size += chunk.Length;
        }
        foreach (var chunk in flac.DecodeRange(plan.Source, plan.Start, plan.End == info.Samples ? null : plan.End, cancel)) part.AppendData(chunk);
        var (ownMd5, partMd5) = (Convert.ToHexStringLower(own.GetHashAndReset()), Convert.ToHexStringLower(part.GetHashAndReset()));
        (result.Samples, result.Md5) = (size / frameBytes, ownMd5);
        if (size % frameBytes != 0 || size / frameBytes != plan.Length)
            return $"The proof failed: the copy has {(size / frameBytes).ToString("N0", CultureInfo.InvariantCulture)} samples, but the trim planned " +
                   $"{plan.Length.ToString("N0", CultureInfo.InvariantCulture)}.";
        if (ownMd5 != partMd5)
            return $"The proof failed: the copy's audio doesn't match its part of the original (MD5 {ownMd5[..8]}… instead of {partMd5[..8]}…).";
        return null;
    }

    /// <summary>This run's .partial, and the subfolder if this run made it and nothing else is in it.</summary>
    public static void Undo(TrimPlan plan, string partial, bool createdFolder)
    {
        Split.DeletePartials([partial]);
        if (!createdFolder) return;
        try
        {
            if (!Directory.EnumerateFileSystemEntries(plan.Folder).Any()) Directory.Delete(plan.Folder);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
        }
    }

    // -- finishing: the name and the tags --

    /// <summary>Gives the proved copy its name (checked free again) and the original's tags. Nothing is ever
    /// overwritten, and nothing runs after it (a trim is just a trim).</summary>
    public static TrimFinished Finish(TrimPlan plan, TrimCut cut)
    {
        if (File.Exists(plan.CopyPath) || Directory.Exists(plan.CopyPath))
        {
            Undo(plan, cut.Partial!, cut.CreatedFolder);
            return new TrimFinished { Error = $"Nothing is ever overwritten, and {plan.ShownPath} was created meanwhile." };
        }
        File.Move(cut.Partial!, plan.CopyPath);
        var finished = new TrimFinished { Path = plan.CopyPath };
        try
        {
            CopyTags(plan.Source, plan.CopyPath);
        }
        catch (Exception e)
        {
            finished.Warnings.Add($"{plan.Name}: the tags couldn't be copied ({e.GetType().Name}: {e.Message})");
        }
        return finished;
    }

    /// <summary>The original's tags on the copy, without any lyrics (SPEC §23). A FLAC gives every Vorbis comment and
    /// every picture as they are, ReplayGain included; a WAV or AIFF gives the fields of §1 with the Vorbis keys the
    /// Python app writes, its ReplayGain values and its front cover. Whatever flac carried over itself is replaced, so
    /// the result never depends on flac's version.</summary>
    public static void CopyTags(string source, string copy)
    {
        using var target = TagLib.File.Create(copy);
        var xiph = (TagLib.Ogg.XiphComment)target.GetTag(TagLib.TagTypes.Xiph, true);
        foreach (var name in xiph.ToList()) xiph.RemoveField(name);
        target.Tag.Pictures = [];
        if (Path.GetExtension(source).Equals(".flac", StringComparison.OrdinalIgnoreCase))
        {
            using var original = TagLib.File.Create(new Tags.SharedRead(source), TagLib.ReadStyle.None);
            if (original.GetTag(TagLib.TagTypes.Xiph, false) is TagLib.Ogg.XiphComment comments)
                foreach (var name in comments.Where(n => !LyricsKeys.Contains(n)))
                    xiph.SetField(name, comments.GetField(name));
            target.Tag.Pictures = original.Tag.Pictures;
            target.Save();
            return;
        }
        CopyId3Tags(source, xiph, target);
        target.Save();
    }

    /// <summary>A WAV's or AIFF's ID3 chunk as the app reads it (§27's legacy text included), as Vorbis comments.</summary>
    static void CopyId3Tags(string source, TagLib.Ogg.XiphComment xiph, TagLib.File target)
    {
        var read = Tags.TagFile.Read(source);
        foreach (var (field, key) in new[]
                 {
                     (Tags.Field.Title, "TITLE"), (Tags.Field.Artist, "ARTIST"), (Tags.Field.AlbumArtist, "ALBUMARTIST"), (Tags.Field.Album, "ALBUM"),
                     (Tags.Field.Year, "DATE"), (Tags.Field.Genre, "GENRE"), (Tags.Field.Composer, "COMPOSER"), (Tags.Field.Comment, "COMMENT"),
                     (Tags.Field.Track, "TRACKNUMBER"), (Tags.Field.TrackTotal, "TRACKTOTAL"), (Tags.Field.Disc, "DISCNUMBER"), (Tags.Field.DiscTotal, "DISCTOTAL"),
                 })
            if (read.Values[field] is { Length: > 0 } value) xiph.SetField(key, value);
        using (var original = TagLib.File.Create(new Tags.SharedRead(source), TagLib.ReadStyle.None))
            if (original.GetTag(TagLib.TagTypes.Id3v2, false) is TagLib.Id3v2.Tag id3)
                foreach (var frame in id3.GetFrames<TagLib.Id3v2.UserTextInformationFrame>())
                    if (frame.Description?.StartsWith("replaygain_", StringComparison.OrdinalIgnoreCase) == true && frame.Text is [var text, ..])
                        xiph.SetField(frame.Description.ToUpperInvariant(), text);
        if (read.Cover is { } cover)
            target.Tag.Pictures = [new TagLib.Picture(new TagLib.ByteVector(cover.Data))
            {
                Type = TagLib.PictureType.FrontCover, MimeType = cover.Mime, Description = "",
            }];
    }
}

/// <summary>How a trim ended, and its report (the Python app's <c>controllers/trim.py</c> <c>report_text</c>).</summary>
public sealed class TrimOutcome(TrimPlan plan)
{
    public TrimPlan Plan { get; } = plan;
    public TrimCut? Cut { get; set; }
    public TrimFinished? Finished { get; set; }
    public bool Cancelled { get; set; }
    public string? Error { get; set; }  // the trim failed (flac, the proof, the space) or the job itself did

    public bool Trimmed => Finished is { Error: null, Path: not null };

    public string Text()
    {
        if (Cancelled) return "Stopped: nothing left half-done. The copy being written was deleted; the original is unchanged.";
        if (Error is not null || !Trimmed)
            return $"The trim failed: {Error ?? "not started"}\nNothing was written; the original is unchanged.";
        var length = Trim.FormatTime(Trim.ToMs(Plan.Length, Plan.Info.Rate));
        var lines = new List<string>
        {
            $"Done: {Plan.ShownPath}, {length}, identical to its part of the original. The original is unchanged.",
            "",
            "The proof: the copy has its planned number of samples, and its audio has the same MD5 as its part of the original.",
        };
        lines.AddRange(Finished!.Warnings.Select(w => $"Warning: {w}"));
        return string.Join("\n", lines);
    }
}

/// <summary>The trim's job (the Python app's <c>services/trim_job.py</c>): cut, prove, name and tag one copy. The
/// caller holds a lock (owner "Trim") on the track's folder, which holds the subfolder.</summary>
public static class TrimTask
{
    public static TrimOutcome Run(TrimPlan plan, Flac flac, CancellationToken cancel)
    {
        var outcome = new TrimOutcome(plan);
        try
        {
            outcome.Cut = Trim.Cut(plan, flac, cancel);
            if (outcome.Cut.Cancelled) outcome.Cancelled = true;
            else if (outcome.Cut.Ok)
            {
                outcome.Finished = Trim.Finish(plan, outcome.Cut);
                outcome.Error = outcome.Finished.Error;
            }
            else outcome.Error = outcome.Cut.Error;
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or FlacException)
        {
            Log.Error($"The trim of {plan.Source} failed", e);
            outcome.Error = $"{e.GetType().Name}: {e.Message}";
        }
        Log.Info($"Trim of {plan.Source} to {plan.ShownPath}: {(outcome.Cancelled ? "stopped" : outcome.Error ?? "done")}");
        return outcome;
    }
}
