using System.Diagnostics;
using System.Text;
using Refrain.Core.Options;

namespace Refrain.Core.ReplayGain;

/// <summary>Where a run's progress goes as it happens (the task window); the log file is the RunLog's.</summary>
public interface IReporter
{
    void Info(string message) { }
    void Warn(string message) { }
    void Error(string message) { }
    void Progress(int done, int total, string label) { }
}

public sealed class NullReporter : IReporter
{
    public static readonly NullReporter Instance = new();
}

/// <summary>A track's line in the report.</summary>
public sealed class TrackValues(double? gain, double? peak, string note)
{
    public double? Gain { get; } = gain;
    public double? Peak { get; } = peak;
    public double? Final { get; set; } = gain;
    public string Note { get; set; } = note;
}

/// <summary>Collects what happens during an album run and writes it to the album's log file as one block. Ported from
/// the Python app's <c>core/replaygain/run_log.py</c>, the same text.</summary>
public sealed class RunLog
{
    readonly Func<DateTime> now;
    readonly Stopwatch clock = Stopwatch.StartNew();

    public RunLog(string folder, IReadOnlyList<string> files, ReplayGainOptions options, string exe,
        IReporter? reporter = null, Func<DateTime>? now = null, bool loose = false)
    {
        this.now = now ?? (() => DateTime.Now);
        Start = this.now();
        Folder = folder;
        Files = files;
        Options = options;
        Exe = exe;
        Reporter = reporter ?? NullReporter.Instance;
        Loose = loose;
    }

    /// <summary>A track on its own (SPEC §31): its own album, no subfolders, no log file.</summary>
    public bool Loose { get; }
    public DateTime Start { get; }
    public string Folder { get; }
    public IReadOnlyList<string> Files { get; }
    public ReplayGainOptions Options { get; }
    public string Exe { get; }
    public IReporter Reporter { get; }
    public string Scan { get; set; } = "not run";
    public string ScanOutput { get; set; } = "";
    public bool ScanOk { get; set; }
    public Dictionary<string, TrackValues> Tracks { get; } = [];
    public (double Gain, double Peak)? Album { get; set; }
    public List<string>? Subfolders { get; set; }  // null until that step runs
    public List<(DateTime Moment, string Level, string Message)> Issues { get; } = [];
    public bool Crashed { get; private set; }

    public void Warn(string message)
    {
        Reporter.Warn(message);
        Issues.Add((now(), "WARNING", message));
    }

    public void Error(string message)
    {
        Reporter.Error(message);
        Issues.Add((now(), "ERROR", message));
    }

    public void Crash(Exception error)
    {
        Crashed = true;
        Issues.Add((now(), "ERROR", "Unexpected crash:\n" + error.ToString().Trim()));
    }

    public void Track(string file, double? gain = null, double? peak = null, string note = "") =>
        Tracks[file] = new TrackValues(gain, peak, note);

    public int Count(string level) => Issues.Count(i => i.Level == level);

    public string Result() =>
        Crashed ? "CRASHED" : !ScanOk ? "STOPPED" : Count("ERROR") == 0 ? "OK" : "DONE WITH ERRORS";

    List<string> TrackTable()
    {
        string[] header = ["Track", "Loudness", "Peak", "rsgain gain", "Final gain", "Note"];
        var rows = new List<string[]> { header };
        foreach (var file in Files)
        {
            var t = Tracks.GetValueOrDefault(file) ?? new TrackValues(null, null, "not processed") { Final = null };
            var loudness = t.Gain is { } gain ? $"{Python.Fixed(Options.Target - gain, 2)} LUFS" : "-";
            rows.Add([file, loudness, Report.PeakDb(t.Peak), Report.Gain(t.Gain), Report.Gain(t.Final), t.Note]);
        }
        return Report.Table(rows);
    }

    public string Text()
    {
        var clippingFixes = Tracks.Values.Count(t => t.Final != t.Gain);
        var lines = new List<string>
        {
            new('=', 78),
            $"Run {Start:yyyy-MM-dd HH:mm:ss}   target {Options.TargetText} LUFS   rsgain: {Exe}",
            $"Folder: {Folder}",
            $"Files: {Report.FilesSummary(Files)}",
            $"Scan: {Scan}",
        };
        if (ScanOutput.Length > 0) lines.Add(Report.Indent(ScanOutput));

        if (ScanOk)
        {
            lines.Add("");
            lines.AddRange(TrackTable());
            lines.Add("");
            if (Loose)
                lines.Add(Album is { } a
                    ? $"Album: the track's own values, gain {Python.Repr(a.Gain)} dB, peak {Python.Repr(a.Peak)}"
                    : "Album: not computed");
            else if (Album is { } album)
                lines.Add($"Album: gain {Python.Repr(album.Gain)} dB (lowest track gain), peak {Python.Repr(album.Peak)} (highest track peak)");
            else
                lines.Add("Album: not computed");
            if (!Loose)
            {
                lines.Add("Subfolders:");
                if (Subfolders is null) lines.Add("  not processed");
                else if (Subfolders.Count == 0) lines.Add("  none matching " + string.Join(", ", Options.SubfolderKeywords));
                else lines.AddRange(Subfolders.Select(s => "  " + s));
            }
        }

        lines.Add("");
        lines.Add("Warnings / errors:");
        if (Issues.Count == 0) lines.Add("  none");
        foreach (var (moment, level, message) in Issues)
        {
            var messageLines = Report.SplitLines(message);
            lines.Add($"  {moment:HH:mm:ss} {level,-7}  {(messageLines.Count > 0 ? messageLines[0] : "")}");
            lines.AddRange(messageLines.Skip(1).Select(l => "                    " + l));
        }

        lines.Add("");
        lines.Add($"Result: {Result()}. {Report.Plural(Files.Count, "track")}, " +
                  $"{Report.Plural(clippingFixes, "clipping fix", "clipping fixes")}, " +
                  $"{Report.Plural(Count("WARNING"), "warning")}, " +
                  $"{Report.Plural(Count("ERROR"), "error")}, {Python.Fixed(clock.Elapsed.TotalSeconds, 1)} s");
        lines.Add("");
        return string.Join("\n", lines) + "\n";
    }

    /// <summary>Writes the report as the album's log file, replacing the previous run's (Hugo, 2026-10-16). Within one
    /// discography run, the leveling line is added after it.</summary>
    public void Write() => Report.WriteText(Path.Combine(Folder, Options.LogFile), Text(), append: false);
}

/// <summary>The formatting the reports share.</summary>
public static class Report
{
    public static string Plural(int count, string word, string? pluralWord = null) =>
        $"{count} {(count == 1 ? word : pluralWord ?? word + "s")}";

    public static string Gain(double? gain) =>
        gain is not { } g ? "-" : (double.IsNegative(g) ? "" : "+") + Python.Fixed(g, 2) + " dB";

    public static string PeakDb(double? peak) =>
        peak is not { } p ? "-" : p > 0 ? $"{Python.Fixed(20 * Math.Log10(p), 2)} dB" : "-inf dB";

    /// <summary>Python's str.splitlines (for the texts the reports hold).</summary>
    public static List<string> SplitLines(string text)
    {
        var lines = text.Replace("\r\n", "\n").Split('\n', '\r').ToList();
        if (lines.Count > 0 && lines[^1].Length == 0) lines.RemoveAt(lines.Count - 1);
        return lines;
    }

    public static string Indent(string text, string prefix = "    ") =>
        string.Join("\n", SplitLines(text).Select(l => (prefix + l).TrimEnd()));

    /// <summary>"N (x FLAC, y MP3)", with other formats appended only when present.</summary>
    public static string FilesSummary(IReadOnlyList<string> files)
    {
        var counts = new Dictionary<string, int> { ["FLAC"] = 0, ["MP3"] = 0 };
        foreach (var name in files)
        {
            var label = Path.GetExtension(name).TrimStart('.').ToUpperInvariant();
            counts[label] = counts.GetValueOrDefault(label) + 1;
        }
        var others = counts.Keys.Where(l => l is not ("FLAC" or "MP3")).Order(StringComparer.Ordinal);
        var parts = new[] { "FLAC", "MP3" }.Concat(others).Select(l => $"{counts[l]} {l}");
        return $"{files.Count} ({string.Join(", ", parts)})";
    }

    /// <summary>Columns padded to their widest cell, two spaces apart, trailing spaces trimmed.</summary>
    public static List<string> Table(List<string[]> rows)
    {
        var widths = Enumerable.Range(0, rows[0].Length).Select(i => rows.Max(r => r[i].Length)).ToArray();
        return rows.Select(r => string.Join("  ", r.Select((cell, i) => cell.PadRight(widths[i]))).TrimEnd()).ToList();
    }

    /// <summary>As Python writes a text file on Windows: UTF-8 without a BOM, each "\n" as CRLF.</summary>
    public static void WriteText(string path, string text, bool append)
    {
        var data = new UTF8Encoding(false).GetBytes(text.Replace("\n", "\r\n"));
        using var stream = new FileStream(path, append ? FileMode.Append : FileMode.Create, FileAccess.Write);
        stream.Write(data);
    }
}
