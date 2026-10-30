namespace Refrain.Core.Cue;

/// <summary>The cue editor's rules apart from its window (SPEC §22), ported from the Python app's
/// <c>ui/controllers/split.py</c>: what the source picker lists, why a source can't be used, what Preview in player
/// opens, and what the Fill dialog starts from.</summary>
public static class CueEditing
{
    /// <summary>The FLAC, WAV and AIFF files in a folder, in Explorer's order: what a sheet there can point to.</summary>
    public static List<string> AudioFiles(string folder)
    {
        try
        {
            return Directory.EnumerateFiles(folder).Select(p => Path.GetFileName(p)!)
                .Where(n => SplitPlanner.Supported.ContainsKey(Path.GetExtension(n))).Order(NaturalOrder.Instance).ToList();
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            return [];
        }
    }

    /// <summary>"not supported" or "not found" for the sheet's current source, as the picker marks it; null when fine.</summary>
    public static string? SourceMark(string current, IReadOnlyList<string> audio) =>
        current.Length == 0 ? null
        : !SplitPlanner.Supported.ContainsKey(Path.GetExtension(current)) ? "not supported"
        : !audio.Any(n => Tags.LyricsKey.CaseFold(n) == Tags.LyricsKey.CaseFold(current)) ? "not found"
        : null;

    /// <summary>Why the sheet's source can't be used, for the Source control's mark (SPEC §22 M13 "1."): several files,
    /// none, a missing or unsupported one, or one that can't be read. Null when it is usable (or not known yet).</summary>
    public static string? SourceWarning(CueDocument doc, IReadOnlyList<string> audio, AlbumPlan? plan)
    {
        var current = doc.File;
        if (doc.FileCount > 1) return "This sheet names several files: the split needs one audio file for the whole album.";
        if (current.Length == 0) return "This sheet names no audio file. Choose its audio file.";
        if (!SplitPlanner.Supported.ContainsKey(Path.GetExtension(current)))
            return $"This sheet points to {current}, which the split can't read (it reads FLAC, WAV and AIFF). Choose its audio file.";
        if (SourceMark(current, audio) == "not found") return $"This sheet points to {current}, which isn't in this folder. Choose its audio file.";
        if (plan is { Source: not null, Info: null, Refusal: { } refusal }) return $"{refusal} Choose its audio file.";
        return null;
    }

    /// <summary>The source line under the picker: "image.flac: FLAC, 44.1 kHz, 0:12", or the plan's refusal (bad).</summary>
    public static (string Text, bool Bad) SourceLine(CueDocument doc, IReadOnlyList<string> audio, AlbumPlan? plan)
    {
        if (SourceWarning(doc, audio, plan) is { } warning) return (warning, true);
        if (plan is { Info: { } info, Source: { } source })
            return ($"{Path.GetFileName(source)}: {info.Format}, {Options.Python.G(info.Rate / 1000.0)} kHz, {SplitRecap.Clock(info.Samples, info.Rate)}", false);
        return plan?.Refusal is { } refusal ? (refusal, true) : ("", false);
    }

    /// <summary>Preview in player (SPEC §22 M13 "3."): every track the split would write, ticked or not, each starting
    /// where the split cuts it, with its title and performer; a hidden track becomes track 1 ("Hidden track").</summary>
    public static string PreviewText(AlbumPlan plan)
    {
        var (source, info) = (plan.Source!, plan.Info!);
        string Field(PlannedTrack t, string key) => t.Fields.TryGetValue(key, out var v) ? v.ToString() ?? "" : "";
        var tracks = plan.Tracks.Select(t => (t.Start, Field(t, "title"), Field(t, "artist"))).ToList();
        string Tag(string key) => info.Tags.TryGetValue(key, out var v) ? v.ToString() ?? "" : "";
        var title = plan.Sheet?.Title is { Length: > 0 } t1 ? t1 : Tag("album");
        var performer = plan.Sheet?.Performer is { Length: > 0 } p1 ? p1 : Tag("albumartist");
        return Playlists.RenderPreview(source, info.Rate, info.Samples, tracks, title, performer);
    }

    /// <summary>The audio's length in CD frames.</summary>
    public static long AudioFrames(SourceInfo info) => info.Samples * CueSheet.FramesPerSecond / info.Rate;

    /// <summary>What the Fill dialog starts from: track 1's start, and each track's length but the last's (null where a
    /// start isn't valid). Null when track 1 has no valid start.</summary>
    public static (long FirstStart, List<long?> Lengths)? FillStart(CueDocument doc)
    {
        var views = doc.Tracks();
        var starts = views.Select(v =>
        {
            try
            {
                return (long?)CueSheet.FramesFromTime(v.Values["start"]);
            }
            catch (CueException)
            {
                return null;
            }
        }).ToList();
        if (starts.Count == 0 || starts[0] is not { } first) return null;
        var lengths = starts.Zip(starts.Skip(1)).Select(p => p is ({ } a, { } b) && b > a ? (long?)(b - a) : null).ToList();
        return (first, lengths);
    }
}
