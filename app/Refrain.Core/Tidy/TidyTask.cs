using Refrain.Core.Cue;
using Refrain.Core.Options;
using Refrain.Core.ReplayGain;

namespace Refrain.Core.Tidy;

/// <summary>What the run did to one album, for the report.</summary>
public sealed class AlbumDone(string folder)
{
    public string Folder { get; } = folder;
    public List<string> Lines { get; } = [];
    public bool Failed { get; set; }
}

public sealed class TidyOutcome(string root)
{
    public string Root { get; } = root;
    public List<AlbumDone> Albums { get; } = [];
    /// <summary>For the redundant files list: half-split images and their sheets, and the plan's clutter
    /// (re-checked).</summary>
    public List<OfferItem> Offers { get; } = [];
    public bool Cancelled { get; set; }
    public string? Error { get; set; }  // the task itself failed

    public int FailedAlbums => Albums.Count(a => a.Failed);

    /// <summary>The run's report: a headline, then each album with what was done.</summary>
    public string Text()
    {
        var head = Error is not null ? $"Failed: {Error}"
            : Cancelled ? $"Stopped after {Report.Plural(Albums.Count, "album")}."
            : $"Done: {Report.Plural(Albums.Count, "album")} tidied" + (FailedAlbums > 0 ? $", {FailedAlbums} with a problem." : ".");
        var lines = new List<string> { head, "" };
        foreach (var album in Albums)
        {
            lines.Add(album.Folder + (album.Failed ? "  (problem)" : ""));
            lines.AddRange(album.Lines.Select(l => "    " + l));
        }
        if (Offers.Count > 0)
        {
            lines.Add("");
            lines.Add($"{Report.Plural(Offers.Count, "item")} to offer for the Recycle Bin: you choose what to keep in " +
                      "the list below.");
        }
        return string.Join("\n", lines);
    }
}

/// <summary>Tidy's run (SPEC §28), ported from the Python app's <c>services/tidy_job.py</c>: album by album, the split
/// of an unsplit image, the renames, then ReplayGain when the plan says so. The caller holds a lock on the plan's
/// trees (owner "Tidy") for the whole run. A failed or stopped split ends its album (no ReplayGain, nothing offered); a
/// failed rename or ReplayGain doesn't stop the run; Stop ends the current step and no further album starts.</summary>
public static class TidyTask
{
    public const string Owner = "Tidy";

    public static TidyOutcome Run(TidyPlan plan, ReplayGainOptions options, CancellationToken cancel,
        IReporter? reporter = null, Scanner? scanner = null, string? exe = null, Flac? flac = null)
    {
        flac ??= new Flac(Flac.Exe);
        reporter ??= NullReporter.Instance;
        var outcome = new TidyOutcome(plan.Root);
        var work = plan.Albums.Where(a => !a.IsTidy).ToList();
        try
        {
            for (var n = 0; n < work.Count; n++)
            {
                if (cancel.IsCancellationRequested)
                {
                    outcome.Cancelled = true;
                    break;
                }
                reporter.Progress(n, work.Count, work[n].Folder);
                reporter.Info($"== {work[n].Folder}");
                outcome.Albums.Add(RunAlbum(work[n], options, cancel, reporter, outcome, scanner, exe, flac));
                if (cancel.IsCancellationRequested)
                {
                    outcome.Cancelled = true;
                    break;
                }
            }
            reporter.Progress(work.Count, work.Count, "done");
            // The clutter goes on the offer list whatever happened to its album (SPEC §28), checked again.
            outcome.Offers.AddRange(plan.AllClutter.Where(item => Clutter.Recheck(item) is null));
        }
        catch (Exception e)
        {
            Log.Error($"Tidy in {plan.Root} failed", e);
            outcome.Error = $"{e.GetType().Name}: {e.Message}";
        }
        Log.Info($"Tidy in {plan.Root}: {outcome.Albums.Count} albums, {outcome.FailedAlbums} failed{(outcome.Cancelled ? ", stopped" : "")}");
        return outcome;
    }

    static AlbumDone RunAlbum(AlbumTidy album, ReplayGainOptions options, CancellationToken cancel, IReporter reporter,
        TidyOutcome outcome, Scanner? scanner, string? exe, Flac flac)
    {
        var done = new AlbumDone(album.Folder);
        var offers = new List<OfferItem>();
        foreach (var folder in album.Folders)
        {
            var where = folder.Folder == album.Folder ? "" : $"{Path.GetFileName(folder.Folder)}: ";
            if (folder.Kind == FolderKind.Skipped)
            {
                done.Lines.Add($"{where}skipped: {folder.Skip}");
                continue;
            }
            if (folder.Kind == FolderKind.NotSplit)
            {
                if (!SplitFolder(folder, flac, cancel, reporter, done, where))
                {
                    done.Failed = true;
                    return done;  // no ReplayGain, nothing offered
                }
                offers.AddRange(Tidying.OfferItems(folder));
            }
            else if (folder.Renames is not null && folder.RenameCount > 0)
            {
                var report = Rename.Apply(folder.Renames, cancel);
                var text = $"{where}renamed {Report.Plural(report.Renamed.Count, "file")}";
                done.Lines.Add(text);
                reporter.Info(text);
                foreach (var problem in report.Errors.Concat(report.NameTaken).Concat(report.Skipped))
                {
                    done.Lines.Add($"{where}not renamed: {problem}");
                    reporter.Info($"WARNING  {problem}");
                }
                if (report.Cancelled) return done;
            }
            if (folder.Kind == FolderKind.HalfSplit) offers.AddRange(Tidying.OfferItems(folder));
        }
        if (album.ReplayGain is not null && !cancel.IsCancellationRequested)
        {
            var exclude = album.Folders.SelectMany(f => f.Images).ToHashSet();
            reporter.Info($"ReplayGain ({album.ReplayGain})");
            var runLog = Album.Process(album.Folder, options, scanner, exe, cancel, reporter, exclude: exclude);
            if (runLog is null) done.Lines.Add("ReplayGain: no file it can measure");
            else
            {
                var result = runLog.Result();
                done.Lines.Add($"ReplayGain: {result} (see {options.LogFile})");
                if (result is not ("OK" or "DONE WITH ERRORS")) done.Failed = true;
            }
        }
        if (album.Clutter.Count > 0 && done.Lines.Count == 0)
            done.Lines.Add("nothing to change: its files to offer are listed at the end");
        outcome.Offers.AddRange(offers);
        return done;
    }

    static bool SplitFolder(FolderPlan folder, Flac flac, CancellationToken cancel, IReporter reporter, AlbumDone done, string where)
    {
        var plan = folder.Split!;
        reporter.Info($"{where}splitting {Path.GetFileName(plan.Cue)} into {Report.Plural(plan.Written.Count, "track")}");
        CutResult cut;
        SplitFinished finished;
        try
        {
            cut = Split.Cut(plan, flac, cancel, onTrack: (_, _, track) => reporter.Info($"  {track.Name}"));
            if (cut.Cancelled)
            {
                done.Lines.Add($"{where}split stopped: nothing left half-written");
                return false;
            }
            if (!cut.Ok)
            {
                done.Lines.Add($"{where}split failed: {cut.Error}");
                return false;
            }
            finished = Split.Finish(plan, cut);
        }
        catch (CueException e)
        {
            done.Lines.Add($"{where}split failed: {e.Message}");
            return false;
        }
        if (finished.Error is not null)
        {
            done.Lines.Add($"{where}split failed: {finished.Error}");
            return false;
        }
        done.Lines.Add($"{where}split into {Report.Plural(finished.Tracks.Count, "track")}, each identical to its part of the original");
        done.Lines.AddRange(finished.Warnings.Select(w => where + w));
        if (plan.Sheet is { FellBack: true })
            done.Lines.Add($"{where}{Path.GetFileName(plan.Cue)} read as Windows-1252 (no BOM, not UTF-8): if a title looks wrong, fix its tags");
        return true;
    }
}
