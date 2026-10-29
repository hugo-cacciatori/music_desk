using System.Globalization;
using System.Security.Cryptography;

namespace Refrain.Core.Cue;

/// <summary>The cut's outcome: the proved .partial files, or why the album wasn't written.</summary>
public sealed class CutResult
{
    public bool Ok { get; init; }
    public bool Cancelled { get; init; }
    public string? Error { get; set; }  // flac's message, a failed proof, or why the album wasn't started
    public List<string> Partials { get; init; } = [];  // the verified tracks, in order, when ok
    public List<long> Lengths { get; init; } = [];  // samples per written track, as decoded
    public List<string> TrackMd5s { get; init; } = [];  // per written track, equal to its range of the source
    public string? Md5 { get; init; }  // of the tracks end to end, when every track was written
    public string? ExpectedMd5 { get; init; }  // of the source
}

/// <summary>One album after its proof: the tracks under their real names.</summary>
public sealed class SplitFinished
{
    public List<string> Tracks { get; } = [];
    public string? Error { get; init; }  // a final name was taken meanwhile: nothing renamed, the .partial files gone
    public List<string> Warnings { get; } = [];
}

/// <summary>Cutting a planned album into tracks and proving nothing was lost (SPEC §22), ported from the Python app's
/// <c>core/split.py</c>. Each ticked track is written to "&lt;name&gt;.partial" first; the proof then decodes every
/// written track: its sample count must be its planned length, and its audio's MD5 that of the same range of the
/// source; with every track ticked, the tracks end to end must also have the source's MD5. On a failure or a Stop,
/// this run's .partial files are deleted, and only those (Ground rule 1).</summary>
public static class Split
{
    public const string Partial = ".partial";

    public static string PartialPath(PlannedTrack track) => track.Path! + Partial;

    public static string Megabytes(long size) =>
        (size / 1024.0 / 1024.0).ToString("N0", CultureInfo.InvariantCulture) + " MB";

    /// <summary>The plan's silence check: that range decoded, with no non-zero byte.</summary>
    public static Func<string, long, long, bool> IsSilent(Flac flac, CancellationToken cancel = default) =>
        (source, start, end) => flac.DecodeRange(source, start, end, cancel).All(chunk => !chunk.AsSpan().ContainsAnyExcept((byte)0));

    /// <summary>Writes and proves the album's ticked tracks as .partial files; nothing else in the folder is touched.
    /// <paramref name="onTrack"/>(done, total, track) is called before each track.</summary>
    public static CutResult Cut(AlbumPlan plan, Flac flac, CancellationToken cancel = default,
        Func<string, long>? freeSpace = null, Action<int, int, PlannedTrack>? onTrack = null)
    {
        if (plan.Refusal is not null || plan.Source is null || plan.Info is null)
            return new CutResult { Error = plan.Refusal ?? "The album wasn't planned." };
        var written = plan.Written;
        if (written.Count == 0) return new CutResult { Error = "No track is ticked." };
        freeSpace ??= folder => new DriveInfo(Path.GetPathRoot(Path.GetFullPath(folder))!).AvailableFreeSpace;
        var share = (double)written.Sum(t => t.Length) / Math.Max(plan.Info.Samples, 1);
        var needed = (long)Math.Ceiling(new FileInfo(plan.Source).Length * share);
        var free = freeSpace(plan.Folder);
        if (free < needed)
            return new CutResult { Error = $"Not enough free space: the tracks need about {Megabytes(needed)}, and the drive has {Megabytes(free)} free." };
        var taken = written.Select(PartialPath).Where(File.Exists).Select(p => Path.GetFileName(p)!).ToList();
        if (taken.Count > 0)
            return new CutResult
            {
                Error = $"{string.Join(", ", taken)} already exist{(taken.Count == 1 ? "s" : "")}, maybe left by an earlier split. " +
                        "Delete them in File Explorer, then split again.",
            };

        var partials = new List<string>();
        CutResult result;
        try
        {
            for (var done = 0; done < written.Count; done++)
            {
                var track = written[done];
                onTrack?.Invoke(done, written.Count, track);
                var output = PartialPath(track);
                partials.Add(output);  // recorded first: a flac killed mid-way may leave it behind
                flac.Encode(plan.Source, track.Start, track.End == plan.Info.Samples ? null : track.End, output, cancel);
            }
            result = Prove(plan, partials, flac, cancel);
        }
        catch (FlacCancelledException)
        {
            DeletePartials(partials);
            return new CutResult { Cancelled = true, Error = "Stopped." };
        }
        catch (FlacException e)
        {
            DeletePartials(partials);
            return new CutResult { Error = $"flac: {e.Message}" };
        }
        if (!result.Ok) DeletePartials(partials);
        return result;
    }

    /// <summary>Feeds decoded PCM to the hashes; returns its size in bytes.</summary>
    static long Hash(IEnumerable<byte[]> chunks, params IncrementalHash[] hashes)
    {
        long size = 0;
        foreach (var chunk in chunks)
        {
            foreach (var hash in hashes) hash.AppendData(chunk);
            size += chunk.Length;
        }
        return size;
    }

    static string Hex(IncrementalHash hash) => Convert.ToHexStringLower(hash.GetHashAndReset());

    /// <summary>Decodes the written tracks: each must have its planned length and its range's MD5; with every track
    /// written, together they must also have the source's MD5.</summary>
    public static CutResult Prove(AlbumPlan plan, List<string> partials, Flac flac, CancellationToken cancel = default)
    {
        var (source, info) = (plan.Source!, plan.Info!);
        var frameBytes = info.Channels * ((info.Bits + 7) / 8);
        using var tracksDigest = IncrementalHash.CreateHash(HashAlgorithmName.MD5);
        using var sourceDigest = IncrementalHash.CreateHash(HashAlgorithmName.MD5);
        List<long> lengths = [];
        List<string> trackMd5s = [], problems = [];
        foreach (var (track, path) in plan.Written.Zip(partials))
        {
            using var own = IncrementalHash.CreateHash(HashAlgorithmName.MD5);
            using var part = IncrementalHash.CreateHash(HashAlgorithmName.MD5);
            var size = Hash(flac.Decode(path, cancel), own, tracksDigest);
            Hash(flac.DecodeRange(source, track.Start, track.End == info.Samples ? null : track.End, cancel), part, sourceDigest);
            lengths.Add(size / frameBytes);
            var (ownMd5, partMd5) = (Hex(own), Hex(part));
            trackMd5s.Add(ownMd5);
            if (size % frameBytes != 0 || size / frameBytes != track.Length)
                problems.Add($"track {track.Number} has {(size / frameBytes).ToString("N0", CultureInfo.InvariantCulture)} samples, but the cut planned {track.Length.ToString("N0", CultureInfo.InvariantCulture)}");
            else if (ownMd5 != partMd5)
                problems.Add($"track {track.Number}'s audio doesn't match its part of the original (MD5 {ownMd5[..8]}… instead of {partMd5[..8]}…)");
        }
        string? md5 = null, expected = null;
        if (plan.AllTicked)
        {
            md5 = Hex(tracksDigest);
            // A WAV or AIFF, or a FLAC written without an MD5: the source's ranges end to end are its whole audio.
            expected = info.Md5 ?? Hex(sourceDigest);
            if (md5 != expected) problems.Add($"the tracks' audio doesn't match the original's (MD5 {md5[..8]}… instead of {expected[..8]}…)");
        }
        return new CutResult
        {
            Ok = problems.Count == 0, Partials = [.. partials], Lengths = lengths, TrackMd5s = trackMd5s, Md5 = md5, ExpectedMd5 = expected,
            Error = problems.Count > 0 ? "The proof failed: " + string.Join("; ", problems) + "." : null,
        };
    }

    /// <summary>Gives the proved tracks their names (checked free again) and writes their tags and cover. A split is
    /// just a split: nothing else is changed or deleted, and nothing is ever overwritten (a name taken since the plan
    /// stops every rename).</summary>
    public static SplitFinished Finish(AlbumPlan plan, CutResult cut)
    {
        var written = plan.Written;
        var taken = written.Where(t => File.Exists(t.Path)).Select(t => t.Name).ToList();
        if (taken.Count > 0)
        {
            DeletePartials(cut.Partials);
            return new SplitFinished { Error = "Nothing is ever overwritten, and these names were taken meanwhile: " + string.Join(", ", taken) + "." };
        }
        var finished = new SplitFinished();
        foreach (var (track, partial) in written.Zip(cut.Partials))
        {
            File.Move(partial, track.Path!);
            finished.Tracks.Add(track.Path!);
        }
        foreach (var track in written)
        {
            try
            {
                WriteTags(track, plan.Info!.Cover);
            }
            catch (Exception e)
            {
                finished.Warnings.Add($"{track.Name}: the tags couldn't be written ({e.GetType().Name}: {e.Message})");
            }
        }
        return finished;
    }

    /// <summary>The sheet's fields, the ISRC and the source's front cover, and nothing else (SPEC §22), with the Vorbis
    /// keys the Python app writes. flac carries a FLAC image's tags and pictures into each cut; they described the
    /// whole file, so they go first.</summary>
    public static void WriteTags(PlannedTrack track, byte[]? cover)
    {
        using var file = TagLib.File.Create(track.Path!);
        var xiph = (TagLib.Ogg.XiphComment)file.GetTag(TagLib.TagTypes.Xiph, true);
        foreach (var name in xiph.ToList()) xiph.RemoveField(name);
        file.Tag.Pictures = [];
        string Text(string key) => track.Fields.TryGetValue(key, out var v) ? Convert.ToString(v, CultureInfo.InvariantCulture)! : "";
        foreach (var (field, key) in new[]
                 {
                     ("title", "TITLE"), ("artist", "ARTIST"), ("albumartist", "ALBUMARTIST"), ("album", "ALBUM"), ("year", "DATE"),
                     ("genre", "GENRE"), ("composer", "COMPOSER"),
                 })
            if (track.Fields.ContainsKey(field)) xiph.SetField(key, Text(field));
        foreach (var (number, total, numberKey, totalKey) in new[] { ("track", "track_total", "TRACKNUMBER", "TRACKTOTAL"), ("disc", "disc_total", "DISCNUMBER", "DISCTOTAL") })
        {
            if (track.Fields.ContainsKey(number)) xiph.SetField(numberKey, Text(number));
            if (track.Fields.TryGetValue(total, out var t) && Convert.ToInt64(t, CultureInfo.InvariantCulture) != 0) xiph.SetField(totalKey, Text(total));
        }
        if (cover is { Length: > 0 })
            file.Tag.Pictures = [new TagLib.Picture(new TagLib.ByteVector(cover))
            {
                Type = TagLib.PictureType.FrontCover,
                MimeType = Tags.TagFile.ImageType(cover) ?? "image/jpeg",
                Description = "",
            }];
        if (track.Isrc.Length > 0) xiph.SetField("ISRC", track.Isrc);
        file.Save();
    }

    /// <summary>This run's own .partial files (Ground rule 1's one permanent delete), the split's or the trim's.</summary>
    public static void DeletePartials(IEnumerable<string> paths)
    {
        foreach (var path in paths)
            if (path.EndsWith(Partial, StringComparison.Ordinal))
            {
                try
                {
                    File.Delete(path);
                }
                catch (Exception e) when (e is IOException or UnauthorizedAccessException)
                {
                    Log.Error($"{path} couldn't be deleted", e);
                }
            }
    }
}
