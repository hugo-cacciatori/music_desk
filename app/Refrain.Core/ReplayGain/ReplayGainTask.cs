using Refrain.Core.Options;

namespace Refrain.Core.ReplayGain;

/// <summary>What a ReplayGain entry runs on (SPEC §31): one folder as an album or as a discography, several folders
/// each as an album, one folder's selected tracks as an album, or tracks each on its own.</summary>
public enum ReplayGainKind { Album, Discography, Albums, Tracks, Loose }

/// <summary>How a ReplayGain task ended: the reports to show, and what the window says.</summary>
public sealed class ReplayGainOutcome
{
    public required ReplayGainKind Kind { get; init; }
    public required string Folder { get; init; }  // the folder shown: the tracks' folder, or the first of several
    public required ReplayGainOptions Options { get; init; }
    public List<RunLog> AlbumLogs { get; } = [];
    public DiscographyResult? Discography { get; set; }
    public LooseResult? Loose { get; set; }
    public bool Cancelled { get; set; }
    public bool Nothing { get; set; }  // no audio file rsgain can scan
    public string? Error { get; set; }  // the task itself failed (an album's failures are in its log)

    /// <summary>The log holding this run's report: the folder's (the artist folder's for a discography); null when
    /// there is no single one (several folders each have theirs, loose tracks have none).</summary>
    public string? LogPath =>
        Nothing || Error is not null || Kind == ReplayGainKind.Loose || (Kind == ReplayGainKind.Albums && AlbumLogs.Count != 1) ? null
        : Kind == ReplayGainKind.Albums ? Path.Combine(AlbumLogs[0].Folder, Options.LogFile)
        : Path.Combine(Folder, Options.LogFile);

    /// <summary>The reports: the loose tracks' report; or a discography's summary first, then each album's report as
    /// written to its log.</summary>
    public string Text()
    {
        if (Loose is not null) return Loose.Text();
        var albums = string.Join("\n", AlbumLogs.Select(l => l.Text()));
        return Discography is { } discography ? discography.Summary + "\n" + albums : albums;
    }

    /// <summary>The line above the report.</summary>
    public string Message()
    {
        if (Error is not null) return $"The task failed: {Error}";
        if (Nothing) return "Nothing was done: no audio file rsgain can scan here.";
        if (Loose is { } loose)
        {
            var trackProblems = loose.Logs.Count(l => l.Result() != "OK");
            var line = Cancelled ? $"Stopped: {Report.Plural(loose.NotRun.Count, "track")} after that point were not processed."
                : trackProblems > 0 ? $"Done, but {Report.Plural(trackProblems, "track")} need{(trackProblems == 1 ? "s" : "")} checking: see below."
                : $"Done: {Report.Plural(loose.Logs.Count, "track")} OK, each on its own.";
            if (loose.Skipped.Count > 0) line += $" {Report.Plural(loose.Skipped.Count, "file")} left out.";
            return line;
        }
        var problems = AlbumLogs.Count(l => l.Result() != "OK");
        var message = Cancelled ? "Stopped. The albums after that point were not processed, and nothing was leveled."
            : problems > 0 ? $"Done, but {Report.Plural(problems, "album")} need{(problems == 1 ? "s" : "")} checking: see below."
            : $"Done: {Report.Plural(AlbumLogs.Count, "album")} OK.";
        if (Discography is { Target: { } target } && !Cancelled) message += $" Every album was leveled to {Report.Gain(target)}.";
        return message;
    }
}

/// <summary>A ReplayGain entry's run (SPEC §10 B, §31), ported from the Python app's <c>services/replaygain_job.py</c>:
/// one album at a time, one rsgain process at below-normal priority. The caller holds the locks
/// (<see cref="LockPaths"/>, owner "ReplayGain") for the whole run.</summary>
public static class ReplayGainTask
{
    public const string Owner = "ReplayGain";

    static bool OnTracks(ReplayGainKind kind) => kind is ReplayGainKind.Tracks or ReplayGainKind.Loose;

    /// <summary>What the run locks: the folder trees, or a track's folder. They are also what the index re-reads
    /// afterwards.</summary>
    public static List<string> LockPaths(ReplayGainKind kind, IReadOnlyList<string> paths) => OnTracks(kind)
        ? paths.Select(p => Path.GetDirectoryName(p)!).Distinct(StringComparer.OrdinalIgnoreCase).ToList()
        : [.. paths];

    public static ReplayGainOutcome Run(ReplayGainKind kind, IReadOnlyList<string> paths, ReplayGainOptions options,
        CancellationToken cancel, IReporter? reporter = null, Scanner? scanner = null, string? exe = null)
    {
        reporter ??= NullReporter.Instance;
        var outcome = new ReplayGainOutcome
        {
            Kind = kind,
            Folder = OnTracks(kind) ? Path.GetDirectoryName(paths[0])! : paths[0],
            Options = options,
        };
        try
        {
            switch (kind)
            {
                case ReplayGainKind.Discography:
                    var result = ReplayGain.Discography.Process(outcome.Folder, options, scanner, exe, cancel, reporter,
                        onAlbum: outcome.AlbumLogs.Add);
                    outcome.Discography = result;
                    outcome.Nothing = result is null;
                    outcome.Cancelled = result?.Cancelled == true;
                    break;
                case ReplayGainKind.Loose:
                    var loose = ReplayGain.Loose.Process(paths, options, scanner, exe, cancel, reporter);
                    outcome.Loose = loose;
                    outcome.Nothing = loose.Logs.Count == 0 && loose.NotRun.Count == 0;
                    outcome.Cancelled = loose.NotRun.Count > 0;
                    break;
                default:
                    // Album and Albums: each folder as an album, in turn; Tracks: their folder, its other files excluded.
                    List<(string Folder, List<string> Exclude)> albums;
                    if (kind == ReplayGainKind.Tracks)
                    {
                        var chosen = paths.Select(p => Album.Fold(Path.GetFileName(p))).ToHashSet();
                        albums = [(outcome.Folder, Directory.EnumerateFileSystemEntries(outcome.Folder).Select(p => Path.GetFileName(p)!)
                            .Where(n => !chosen.Contains(Album.Fold(n))).ToList())];
                    }
                    else albums = paths.Select(p => (p, new List<string>())).ToList();
                    for (var i = 0; i < albums.Count; i++)
                    {
                        if (cancel.IsCancellationRequested)
                        {
                            outcome.Cancelled = true;
                            break;
                        }
                        reporter.Progress(i, albums.Count, Path.GetFileName(albums[i].Folder));
                        var runLog = Album.Process(albums[i].Folder, options, scanner, exe, cancel, reporter, exclude: albums[i].Exclude);
                        if (runLog is null) continue;
                        outcome.AlbumLogs.Add(runLog);
                        outcome.Cancelled = runLog.Scan == "CANCELLED";
                    }
                    reporter.Progress(albums.Count, albums.Count, "done");
                    outcome.Nothing = outcome.AlbumLogs.Count == 0 && !outcome.Cancelled;
                    break;
            }
        }
        catch (Exception e)
        {
            Log.Error($"ReplayGain ({kind}) in {outcome.Folder} failed", e);
            outcome.Error = $"{e.GetType().Name}: {e.Message}";
        }
        Log.Info($"ReplayGain ({kind}) in {outcome.Folder}: {outcome.AlbumLogs.Count} albums{(outcome.Cancelled ? ", cancelled" : "")}");
        return outcome;
    }
}

/// <summary>What "Remove ReplayGain tags…" did (SPEC §31): tracks cleared, tracks that had none, files that couldn't
/// be changed and why, and the paths left out (not tracks).</summary>
public sealed class RemoveOutcome
{
    public int Cleared { get; set; }
    public int HadNone { get; set; }
    public List<(string Path, string Reason)> Failures { get; } = [];
    public List<string> LeftOut { get; } = [];

    public string Message()
    {
        if (Cleared + HadNone + Failures.Count == 0) return "There was no track to remove ReplayGain tags from.";
        var text = $"Removed the ReplayGain tags of {Report.Plural(Cleared, "track")}.";
        if (HadNone > 0) text += $" {Report.Plural(HadNone, "track")} had none.";
        if (Failures.Count > 0)
            text += $" {Report.Plural(Failures.Count, "file")} couldn't be changed: see below.";
        return text;
    }

    public string Details()
    {
        var lines = Failures.Select(f => $"{f.Path}: {f.Reason}").ToList();
        if (LeftOut.Count > 0)
        {
            if (lines.Count > 0) lines.Add("");
            lines.Add("Left out (not tracks rsgain scans):");
            lines.AddRange(LeftOut.Select(p => "  " + p));
        }
        return string.Join("\n", lines);
    }
}

/// <summary>"Remove ReplayGain tags…" on selected tracks (SPEC §10 B, §31): one file at a time, each under a short
/// lock, so a file a task holds is refused with the busy sentence.</summary>
public static class RemoveTask
{
    public const string Owner = "a tag save";

    /// <summary>The tracks (an extension rsgain scans) and the rest, left out.</summary>
    public static (List<string> Tracks, List<string> LeftOut) Split(IEnumerable<string> paths)
    {
        List<string> tracks = [], leftOut = [];
        foreach (var path in paths) (Formats.ReplayGain.Contains(Path.GetExtension(path)) ? tracks : leftOut).Add(path);
        return (tracks, leftOut);
    }

    public static RemoveOutcome Run(IEnumerable<string> paths, ReplayGainOptions options, PathLocks locks,
        Action<string>? changed = null)
    {
        var (tracks, leftOut) = Split(paths);
        var outcome = new RemoveOutcome();
        outcome.LeftOut.AddRange(leftOut);
        foreach (var track in tracks)
        {
            int token;
            try
            {
                token = locks.Acquire([track], Owner);
            }
            catch (LockedException e)
            {
                outcome.Failures.Add((track, e.Message));
                continue;
            }
            try
            {
                if (Remove.Run(track, options)) outcome.Cleared++;
                else outcome.HadNone++;
                changed?.Invoke(track);
            }
            catch (Exception e)
            {
                outcome.Failures.Add((track, Reason(e)));
            }
            finally
            {
                locks.Release(token);
            }
        }
        return outcome;
    }

    /// <summary>Plain words for the usual failures.</summary>
    static string Reason(Exception e) => e switch
    {
        UnauthorizedAccessException => "it is read-only, or Refrain may not change it",
        FileNotFoundException or DirectoryNotFoundException => "it isn't there any more",
        IOException => $"it is in use by another program ({e.Message.TrimEnd('.')})",
        _ => e.Message,
    };
}
