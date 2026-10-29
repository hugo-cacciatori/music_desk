using System.Diagnostics;
using Refrain.Core.Options;

namespace Refrain.Core.ReplayGain;

/// <summary>One album of a discography run.</summary>
public sealed class AlbumOutcome(string name, string folder)
{
    public string Name { get; } = name;
    public string Folder { get; } = folder;
    public string Result { get; set; } = "CRASHED";
    public double? Gain { get; set; }
    public int? Changed { get; set; }  // null: not leveled
    public int Skipped { get; set; }
    public List<string> Errors { get; set; } = [];
}

public sealed record DiscographyResult(IReadOnlyList<AlbumOutcome> Albums, double? Target, bool Cancelled);

/// <summary>Discography mode: every album under a folder, one at a time, then all leveled to the lowest album gain.
/// Ported from the Python app's <c>core/replaygain/discography.py</c>.</summary>
public static class Discography
{
    /// <summary>The folders under root (root included) that directly hold audio files rsgain can scan. An album's
    /// keyword subfolders are not albums of their own (its copy step handles them); a folder whose only such files
    /// are unsplit images isn't an album (SPEC §10 B).</summary>
    public static List<string> FindAlbums(string root, ReplayGainOptions options)
    {
        var albums = new List<string>();
        void Walk(string folder)
        {
            var (supported, unsupported) = Album.ListAudio(folder);
            var images = supported.Count > 0 ? Album.ImageNames(folder, supported.Concat(unsupported)) : [];
            var subfolders = Directory.EnumerateDirectories(folder).Select(d => Path.GetFileName(d)!).ToList();
            if (supported.Any(n => !images.Contains(Album.Fold(n))))
            {
                albums.Add(folder);
                var keyword = Album.KeywordSubfolders(folder, options);
                subfolders.RemoveAll(keyword.Contains);
            }
            foreach (var name in subfolders.Order(StringComparer.Ordinal)) Walk(Path.Combine(folder, name));
        }
        Walk(root);
        return albums;
    }

    /// <summary>Sets the album gain of an album folder and its keyword subfolders to the target. Only files whose
    /// album gain differs are written; files without one are skipped.</summary>
    public static (int Changed, int Skipped, List<string> Errors) Level(string folder, double target, ReplayGainOptions options)
    {
        var paths = new List<string>();
        foreach (var each in new[] { folder }.Concat(Album.KeywordSubfolders(folder, options).Select(s => Path.Combine(folder, s))))
        {
            var images = Album.ImageNames(each);
            paths.AddRange(Album.ListAudio(each).Supported.Where(f => !images.Contains(Album.Fold(f))).Select(f => Path.Combine(each, f)));
        }
        int changed = 0, skipped = 0;
        var errors = new List<string>();
        foreach (var path in paths)
        {
            try
            {
                using var tags = RgTags.Open(path);
                var current = tags.Get(RgTags.AlbumGain);
                if (current is null)
                {
                    skipped++;
                    continue;
                }
                if (Album.Parse(current) == target) continue;
                tags.Set(RgTags.AlbumGain, $"{Python.Repr(target)} dB");
                tags.Save();
                changed++;
            }
            catch (Exception e)
            {
                errors.Add($"Could not level {Path.GetRelativePath(folder, path)}: {e.Message}");
            }
        }
        return (changed, skipped, errors);
    }

    /// <summary>Processes every album under root, one at a time, then gives them all the lowest album gain. After a
    /// cancel, the remaining albums are not processed and nothing is leveled. <paramref name="onAlbum"/> gets each
    /// album's RunLog as it is written.</summary>
    public static DiscographyResult? Process(string root, ReplayGainOptions options, Scanner? scanner = null,
        string? exe = null, CancellationToken cancel = default, IReporter? reporter = null, Func<DateTime>? now = null,
        Action<RunLog>? onAlbum = null)
    {
        reporter ??= NullReporter.Instance;
        now ??= () => DateTime.Now;
        var (start, clock) = (now(), Stopwatch.StartNew());
        var folders = FindAlbums(root, options);
        if (folders.Count == 0)
        {
            reporter.Info($"No folder with audio files rsgain can scan under {root}");
            return null;
        }

        // 1. The album workflow in every album folder
        var albums = new List<AlbumOutcome>();
        var cancelled = false;
        for (var i = 0; i < folders.Count; i++)
        {
            var folder = folders[i];
            var name = folder != root ? Path.GetRelativePath(root, folder) : $"{Path.GetFileName(root)} (this folder)";
            var album = new AlbumOutcome(name, folder);
            albums.Add(album);
            if (cancelled || cancel.IsCancellationRequested)
            {
                cancelled = true;
                album.Result = "CANCELLED";
                continue;
            }
            reporter.Progress(i, folders.Count, name);
            var runLog = Album.Process(folder, options, scanner, exe, cancel, reporter, now);
            if (runLog is null) continue;
            onAlbum?.Invoke(runLog);
            album.Result = runLog.Result();
            if (runLog.Album is { } values && album.Result is "OK" or "DONE WITH ERRORS") album.Gain = values.Gain;
            if (runLog.Scan == "CANCELLED") cancelled = true;
        }

        // 2. Level the albums processed successfully (not after a cancel)
        var leveled = cancelled ? [] : albums.Where(a => a.Gain is not null).ToList();
        double? target = leveled.Count > 0 ? leveled.Min(a => a.Gain!.Value) : null;
        foreach (var album in leveled)
        {
            var (changed, skipped, errors) = Level(album.Folder, target!.Value, options);
            (album.Changed, album.Skipped, album.Errors) = (changed, skipped, errors);
            var line = album.Gain == target
                ? $"album gain already at the discography target ({Python.Repr(target.Value)} dB)"
                : $"album gain {Python.Repr(album.Gain!.Value)} dB -> {Python.Repr(target.Value)} dB";
            line += $", {Report.Plural(changed, "file")} changed";
            var text = $"Discography leveling {now():yyyy-MM-dd HH:mm:ss}: {line}\n" +
                       string.Concat(errors.Select(e => $"  ERROR  {e}\n"));
            Report.WriteText(Path.Combine(album.Folder, options.LogFile), text, append: true);
        }

        // 3. The summary in the artist folder
        WriteSummary(root, options, start, clock.Elapsed.TotalSeconds, albums, target, cancelled);
        reporter.Progress(folders.Count, folders.Count, "done");
        return new DiscographyResult(albums, target, cancelled);
    }

    public static string SummaryText(string root, ReplayGainOptions options, DateTime start, double duration,
        IReadOnlyList<AlbumOutcome> albums, double? target, bool cancelled = false)
    {
        var rows = new List<string[]> { new[] { "Album", "Result", "Album gain", "Leveled to", "Files changed" } };
        foreach (var album in albums)
        {
            var isLeveled = album.Changed is not null;
            var result = album.Result == "OK" && album.Gain is null ? "OK (no album gain)" : album.Result;
            rows.Add([album.Name, result, Report.Gain(album.Gain), isLeveled ? Report.Gain(target) : "not leveled",
                isLeveled ? album.Changed!.Value.ToString() : "-"]);
        }
        var table = Report.Table(rows);
        var nLeveled = albums.Count(a => a.Changed is not null);
        var issues = albums.Where(a => a.Changed is null)
            .Select(a => $"  {a.Name}: {a.Result}, not leveled. See {Path.Combine(a.Name, options.LogFile)}").ToList();
        issues.AddRange(albums.SelectMany(a => a.Errors.Select(e => $"  {a.Name}: {e}")));
        issues.AddRange(albums.Where(a => a.Skipped > 0).Select(a => $"  {a.Name}: {a.Skipped} file(s) without album gain, not leveled"));
        var gainLine = cancelled ? "Discography album gain: none, the run was cancelled before leveling"
            : target is { } t ? $"Discography album gain: {Python.Repr(t)} dB (lowest album gain)"
            : "Discography album gain: none, no album was processed successfully";
        var lines = new List<string>
        {
            new('=', 78),
            $"Discography run {start:yyyy-MM-dd HH:mm:ss}   target {options.TargetText} LUFS",
            $"Folder: {root}",
            $"Albums: {albums.Count} ({nLeveled} leveled, {albums.Count - nLeveled} not leveled)",
            "",
        };
        lines.AddRange(table);
        lines.AddRange(["", gainLine, "", "Errors / notes:"]);
        lines.AddRange(issues.Count > 0 ? issues : ["  none"]);
        lines.Add("");
        lines.Add($"Result: {(nLeveled == albums.Count && issues.Count == 0 ? "OK" : "CHECK ALBUMS")}. " +
                  $"{Report.Plural(albums.Count, "album")}, {nLeveled} leveled, {Python.Fixed(duration, 1)} s");
        lines.Add("");
        return string.Join("\n", lines) + "\n";
    }

    /// <summary>The summary replaces the artist folder's log, unless that folder was also one of this run's albums:
    /// then it follows that album's report, written minutes earlier in the same run.</summary>
    static void WriteSummary(string root, ReplayGainOptions options, DateTime start, double duration,
        IReadOnlyList<AlbumOutcome> albums, double? target, bool cancelled)
    {
        var append = albums.Any(a => a.Folder == root);
        Report.WriteText(Path.Combine(root, options.LogFile),
            SummaryText(root, options, start, duration, albums, target, cancelled), append);
    }
}
