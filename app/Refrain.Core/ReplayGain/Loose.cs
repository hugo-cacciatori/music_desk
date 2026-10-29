using Refrain.Core.Options;

namespace Refrain.Core.ReplayGain;

/// <summary>A run on loose tracks: each one's RunLog, in order, and the paths left out with why.</summary>
public sealed class LooseResult(ReplayGainOptions options)
{
    public List<RunLog> Logs { get; } = [];
    public List<(string Path, string Reason)> Skipped { get; } = [];
    public List<string> NotRun { get; set; } = [];  // after a cancel

    public string Result()
    {
        if (NotRun.Count > 0) return "CANCELLED";
        if (Logs.Count == 0) return "NOTHING TO DO";
        return Logs.All(l => l.Result() == "OK") ? "OK" : "DONE WITH ERRORS";
    }

    /// <summary>The report the task window shows: one block per track, then what was left out. Never written to a
    /// file: an album's replaygain.log keeps its last report.</summary>
    public string Text()
    {
        var text = $"ReplayGain, each track on its own (target {options.TargetText} LUFS): " +
                   $"{Report.Plural(Logs.Count, "track")} processed\n\n" + string.Concat(Logs.Select(l => l.Text()));
        var tail = Skipped.Select(s => $"  {s.Path}: {s.Reason}").Concat(NotRun.Select(p => $"  {p}: not processed, cancelled")).ToList();
        if (tail.Count > 0) text += "\nLeft out:\n" + string.Join("\n", tail) + "\n";
        return text + $"\nResult: {Result()}\n";
    }
}

/// <summary>Loose tracks (the right-click menu only, SPEC §31): each track alone is its own album. Ported from the
/// Python app's <c>core/replaygain/loose.py</c>.</summary>
public static class Loose
{
    /// <summary>rsgain measures each track at the target, the clipping fix lowers its track gain if needed, its album
    /// gain and peak become its own and the LUFS comment is written. Nothing is copied into subfolders and no log
    /// file is written. Files rsgain can't scan and missing ones are skipped; a cancel stops after the current
    /// track.</summary>
    public static LooseResult Process(IEnumerable<string> tracks, ReplayGainOptions options, Scanner? scanner = null,
        string? exe = null, CancellationToken cancel = default, IReporter? reporter = null, Func<DateTime>? now = null)
    {
        scanner ??= Rsgain.Scan;
        exe ??= Rsgain.Exe;
        reporter ??= NullReporter.Instance;
        var result = new LooseResult(options);
        var todo = new List<string>();
        foreach (var track in tracks)
        {
            var ext = Path.GetExtension(track);
            if (!Formats.ReplayGain.Contains(ext)) result.Skipped.Add((track, "skipped, format not supported by rsgain"));
            else if (!RgTags.Writable(ext)) result.Skipped.Add((track, "skipped, Refrain can't write this format's tags"));
            else if (!File.Exists(track)) result.Skipped.Add((track, "skipped, not found"));
            else todo.Add(track);
        }
        for (var i = 0; i < todo.Count; i++)
        {
            if (cancel.IsCancellationRequested)
            {
                result.NotRun = todo[i..];
                break;
            }
            reporter.Progress(i, todo.Count, Path.GetFileName(todo[i]));
            result.Logs.Add(ProcessTrack(todo[i], options, scanner, exe, reporter, now));
        }
        reporter.Progress(todo.Count, todo.Count, "done");
        return result;
    }

    static RunLog ProcessTrack(string track, ReplayGainOptions options, Scanner scanner, string exe, IReporter reporter,
        Func<DateTime>? now)
    {
        var folder = Path.GetDirectoryName(track)!;
        string[] files = [Path.GetFileName(track)];
        var runLog = new RunLog(folder, files, options, exe, reporter, now, loose: true);
        try
        {
            if (Album.ScanAlbum(folder, files, runLog, scanner, exe, CancellationToken.None))
            {
                Album.PreventClipping(folder, files, runLog);
                Album.WriteAlbumValues(folder, files, runLog);
            }
        }
        catch (Exception e)
        {
            Log.Error($"ReplayGain crashed on {track}", e);
            runLog.Crash(e);
        }
        reporter.Info($"{files[0]}: {runLog.Result()}");
        return runLog;
    }
}
