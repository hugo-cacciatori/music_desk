using Refrain.Core.ReplayGain;

namespace Refrain.Core.Cue;

/// <summary>How a split ended.</summary>
public sealed class SplitOutcome(AlbumPlan plan)
{
    public AlbumPlan Plan { get; } = plan;
    public CutResult? Cut { get; set; }
    public SplitFinished? Finished { get; set; }
    public bool Cancelled { get; set; }
    public string? Error { get; set; }

    public bool Split => Cut is { Ok: true } && Finished is { Error: null };

    /// <summary>The report, as the Python app's Split page wrote it.</summary>
    public string Text()
    {
        if (Error is not null) return $"The split failed: {Error}\nThe album is as it was.";
        if (Cancelled) return "Stopped: nothing left half-done. The tracks being written were deleted; the album is as it was.";
        if (!Split || Finished is null)
        {
            var reason = Finished?.Error ?? Cut?.Error ?? "not started";
            return $"Not split: {reason}\nNothing was written; the album is as it was.";
        }
        var (written, total) = (Finished.Tracks.Count, Plan.Tracks.Count);
        var lines = new List<string>
        {
            Plan.AllTicked
                ? $"Done: {Report.Plural(written, "track")} split, all identical to the original."
                : $"Done: {written} of {total} tracks split, each identical to its part of the original.",
            "",
            $"Written to {Plan.Folder}:",
        };
        lines.AddRange(Finished.Tracks.Select(p => "    " + Path.GetFileName(p)));
        lines.Add("");
        lines.Add("The proof: each track has its planned number of samples, and its audio has the same MD5 as its part of the original.");
        if (Cut?.Md5 is { } md5) lines.Add($"Together, the tracks have the original's MD5 ({md5[..8]}…).");
        lines.Add("");
        lines.Add("The image and its cue sheet are as they were.");
        lines.AddRange(Finished.Warnings.Select(w => $"Warning: {w}"));
        return string.Join("\n", lines);
    }
}

/// <summary>A split on its own (SPEC §22, §31 "Split this cue sheet…"), ported from the Python app's
/// <c>services/split_job.py</c>. The caller holds a lock (owner "Split") on <see cref="LockPaths"/>.</summary>
public static class SplitTask
{
    public const string Owner = "Split";

    /// <summary>The image's folder and the destination.</summary>
    public static List<string> LockPaths(AlbumPlan plan) =>
        new[] { Path.GetDirectoryName(plan.Cue)!, plan.Folder }.Distinct(StringComparer.OrdinalIgnoreCase).ToList();

    public static SplitOutcome Run(AlbumPlan plan, Flac flac, CancellationToken cancel, Action<int, int, string>? progress = null)
    {
        var outcome = new SplitOutcome(plan);
        try
        {
            outcome.Cut = Split.Cut(plan, flac, cancel, onTrack: (done, total, track) => progress?.Invoke(done, total, track.Name));
            if (outcome.Cut.Cancelled) outcome.Cancelled = true;
            else if (outcome.Cut.Ok) outcome.Finished = Split.Finish(plan, outcome.Cut);
            progress?.Invoke(plan.Written.Count, plan.Written.Count, "done");
        }
        catch (CueException e)
        {
            outcome.Error = e.Message;
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or FlacException)
        {
            Log.Error($"The split of {plan.Cue} failed", e);
            outcome.Error = $"{e.GetType().Name}: {e.Message}";
        }
        Log.Info($"Split of {plan.Cue} into {plan.Folder}: " + (outcome.Cancelled ? "stopped"
            : outcome.Split ? $"{outcome.Finished!.Tracks.Count} tracks" : outcome.Error ?? outcome.Cut?.Error ?? "not started"));
        return outcome;
    }
}

/// <summary>The recap of "Split this cue sheet…" (SPEC §31), as the Python app's <c>split_recap.py</c> says it.</summary>
public static class SplitRecap
{
    public const string FixIt = "To fix it, use \"Edit this cue sheet…\" in the right-click menu, or Edit… on its preview in Refrain.";

    /// <summary>"m:ss", whole seconds.</summary>
    public static string Clock(long samples, int rate)
    {
        var seconds = rate > 0 ? samples / rate : 0;
        return $"{seconds / 60}:{seconds % 60:00}";
    }

    /// <summary>Why the sheet can't be split as it is: the masks to fix, the plan's refusal, its warnings (the
    /// preview's Split doesn't split a sheet that needs a look either). Empty: Split is enabled.</summary>
    public static List<string> Reasons(AlbumPlan plan, bool masksValid, string optionsWhere)
    {
        var reasons = new List<string>();
        if (!masksValid) reasons.Add($"The File names masks need fixing: {optionsWhere}.");
        if (plan.Refusal is not null) reasons.Add(plan.Refusal);
        reasons.AddRange(plan.Warnings);
        return reasons;
    }

    /// <summary>The source, the destination and the encoding, one per line.</summary>
    public static List<string> Details(AlbumPlan plan)
    {
        var source = plan is { Info: { } info, Source: { } path }
            ? $"{Path.GetFileName(path)}: {info.Format}, {Options.Python.G(info.Rate / 1000.0)} kHz, {Clock(info.Samples, info.Rate)}"
            : plan.Source is { } named ? Path.GetFileName(named) : "none found";
        return [$"Source: {source}", $"Into: {plan.Folder}", $"Cue sheet read as {plan.Sheet?.Encoding ?? "?"}"];
    }

    /// <summary>(Track, File, Length) per track: "hidden" for a hidden track.</summary>
    public static List<(string Track, string File, string Length)> Rows(AlbumPlan plan) =>
        plan.Tracks.Select(t => (t.Row is null ? "hidden" : t.Number.ToString("00"), t.Name, Clock(t.Length, plan.Info?.Rate ?? 0))).ToList();

    public static string Message(AlbumPlan plan, List<string> reasons) => reasons.Count > 0
        ? "This cue sheet can't be split as it is: " + string.Join(" ", reasons) + " " + FixIt
        : $"Split writes {Report.Plural(plan.Written.Count, "track")} as FLAC files, each proven identical to its part of the original. " +
          "The image and its cue sheet stay as they are.";
}
