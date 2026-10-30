using System.Globalization;
using Refrain.Core.Cue;
using Refrain.Core.Options;
using Refrain.Core.Tags;

namespace Refrain.Core.ReplayGain;

/// <summary>The album workflow (SPEC §10 B): rsgain album scan, clipping fix, album values and LUFS comment, subfolder
/// copies. Ported from the Python app's <c>core/replaygain/album.py</c>: the same order, values and report text.</summary>
public static class Album
{
    /// <summary>(names rsgain can scan and Refrain can tag, other audio names) of a folder, in the order Windows lists
    /// them (as the script's os.listdir).</summary>
    public static (List<string> Supported, List<string> Unsupported) ListAudio(string folder)
    {
        List<string> supported = [], unsupported = [];
        foreach (var path in Directory.EnumerateFiles(folder))
        {
            var name = Path.GetFileName(path);
            var ext = Path.GetExtension(name);
            if (Formats.ReplayGain.Contains(ext) && RgTags.Writable(ext)) supported.Add(name);
            else if (Formats.IsAudio(ext)) unsupported.Add(name);
        }
        return (supported, unsupported);
    }

    /// <summary>Why a folder's audio file is left out of the scan.</summary>
    static string UnsupportedReason(string name) => Formats.ReplayGain.Contains(Path.GetExtension(name))
        ? "skipped, Refrain can't write this format's tags"
        : "skipped, format not supported by rsgain";

    public static string Fold(string name) => LyricsKey.CaseFold(name);

    /// <summary>The casefolded names of the folder's unsplit images (SPEC §10 B, Tidy's rule): never measured, never
    /// given album values.</summary>
    public static HashSet<string> ImageNames(string folder, IEnumerable<string>? audioNames = null)
    {
        if (audioNames is null)
        {
            var (supported, unsupported) = ListAudio(folder);
            audioNames = supported.Concat(unsupported);
        }
        return CueSheet.UnsplitImages(folder, audioNames).Select(Fold).ToHashSet();
    }

    /// <summary>The names of a folder's subfolders that match the keywords (lite, bonus, CD2...), in the order Windows
    /// lists them.</summary>
    public static List<string> KeywordSubfolders(string folder, ReplayGainOptions options) =>
        Directory.EnumerateDirectories(folder).Select(Path.GetFileName)
            .Where(name => options.SubfolderKeywords.Any(k => name!.ToLowerInvariant().Contains(k, StringComparison.Ordinal)))
            .Select(name => name!).ToList();

    /// <summary>A keyword subfolder holding a disc of its album (cd2, disc 2…): any album-data keyword but the
    /// track-data (lite) and excluded (bonus) ones. Its tracks are measured with the album (SPEC §10 B, Hugo
    /// 2026-10-30), and Tidy renames them; lite and bonus only get values copied, and Tidy leaves them alone.</summary>
    public static bool IsDisc(string name, ReplayGainOptions options)
    {
        var lower = name.ToLowerInvariant();
        return !options.TrackDataKeywords.Concat(options.ExcludedKeywords).Any(k => lower.Contains(k, StringComparison.Ordinal));
    }

    /// <summary>The disc subfolders' tracks, as paths relative to the album folder (<c>CD2\01 Song.flac</c>), in the
    /// order Windows lists them; their other audio and waiting images as (relative name, reason) warnings. A disc whose
    /// folder name is in <paramref name="skip"/> stays out (a selection of tracks measures only those).</summary>
    public static (List<string> Tracks, List<(string Name, string Reason)> Left) DiscTracks(string folder, ReplayGainOptions options,
        IReadOnlySet<string>? skip = null)
    {
        List<string> tracks = [];
        List<(string, string)> left = [];
        foreach (var disc in KeywordSubfolders(folder, options).Where(d => IsDisc(d, options) && skip?.Contains(Fold(d)) != true))
        {
            var (supported, unsupported) = ListAudio(Path.Combine(folder, disc));
            var images = ImageNames(Path.Combine(folder, disc), supported.Concat(unsupported));
            foreach (var name in supported)
                if (images.Contains(Fold(name))) left.Add((Path.Combine(disc, name), "skipped, image waiting to be split"));
                else tracks.Add(Path.Combine(disc, name));
            left.AddRange(unsupported.Select(name => (Path.Combine(disc, name), UnsupportedReason(name))));
        }
        return (tracks, left);
    }

    /// <summary>A ReplayGain value such as "-6.5 dB" or "0.98" as a number. Throws FormatException.</summary>
    public static double Parse(string value)
    {
        var text = value.Replace(" dB", "").Trim();
        if (!double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out var number))
            throw new FormatException($"could not convert string to float: '{value.Replace(" dB", "")}'");
        return number;
    }

    /// <summary>(track gain, track peak), or null when they are missing or invalid (logged).</summary>
    static (double Gain, double Peak)? ReadTrackValues(RgTags tags, string file, RunLog runLog)
    {
        var (gain, peak) = (tags.Get(RgTags.TrackGain), tags.Get(RgTags.TrackPeak));
        if (gain is null || peak is null)
        {
            runLog.Warn($"Missing ReplayGain track values in {file}, skipping");
            return null;
        }
        try
        {
            return (Parse(gain), Parse(peak));
        }
        catch (FormatException)
        {
            runLog.Warn($"Invalid ReplayGain value in {file}: gain='{gain}', peak='{peak}', skipping");
            return null;
        }
    }

    /// <summary>Scans all files at once in album mode, writing ReplayGain tags. True when rsgain succeeded.</summary>
    public static bool ScanAlbum(string folder, IReadOnlyList<string> files, RunLog runLog, Scanner scanner, string exe,
        CancellationToken cancel)
    {
        runLog.Reporter.Info($"Scanning album with {runLog.Options.TargetText} LUFS");
        var result = scanner(files, folder, runLog.Options.TargetText, exe, cancel, runLog.Reporter.Info);
        if (result.Error == "not found")
        {
            runLog.Scan = "FAILED (rsgain not found)";
            runLog.Error($"rsgain not found at {exe}");
            return false;
        }
        if (result.Error is not null)
        {
            runLog.Scan = $"FAILED ({result.Error})";
            runLog.Error($"rsgain was not run: {result.Error}. The album can't be scanned in one call.");
            return false;
        }
        if (result.Cancelled)
        {
            runLog.Scan = "CANCELLED";
            runLog.Warn("Cancelled: rsgain was stopped and the tags were not post-processed");
            return false;
        }
        if (result.ExitCode != 0)
        {
            runLog.Scan = $"FAILED (exit code {result.ExitCode})";
            runLog.ScanOutput = string.Join("\n", new[] { Rsgain.StripAnsi(result.Stdout), Rsgain.StripAnsi(result.Stderr) }
                .Where(t => t.Length > 0));
            runLog.Error($"rsgain scan failed (exit code {result.ExitCode}):\n{Rsgain.StripAnsi(result.Stderr)}");
            return false;
        }
        runLog.Scan = $"OK ({Python.Fixed(result.Duration, 1)} s)";
        runLog.ScanOk = true;
        return true;
    }

    /// <summary>Lowers track gains that would push the track peak above the safety threshold, and records each
    /// track's rsgain values in the run log.</summary>
    public static void PreventClipping(string folder, IReadOnlyList<string> files, RunLog runLog)
    {
        var options = runLog.Options;
        foreach (var file in files)
        {
            try
            {
                using var tags = RgTags.Open(Path.Combine(folder, file));
                if (ReadTrackValues(tags, file, runLog) is not var (trackGain, trackPeak))
                {
                    runLog.Track(file, note: "skipped: no ReplayGain data");
                    continue;
                }
                runLog.Track(file, trackGain, trackPeak);
                if (trackPeak == 0)
                {
                    runLog.Warn($"Track peak is 0 in {file}, clipping check skipped");
                    runLog.Tracks[file].Note = "peak is 0";
                    continue;
                }
                var finalPeak = Math.Pow(10, trackGain / 20) * trackPeak;
                if (finalPeak <= options.SafetyThreshold) continue;

                var maxSafeGain = Python.Round(20 * Math.Log10(options.SafetyThreshold / trackPeak) - options.SafetyMargin, 2);
                tags.Set(RgTags.TrackGain, $"{Python.Repr(maxSafeGain)} dB");
                tags.Save();
                runLog.Tracks[file].Final = maxSafeGain;
                runLog.Tracks[file].Note = "reduced to prevent clipping";
                runLog.Reporter.Info($"Clipping detected in {file}: track gain reduced to {Python.Repr(maxSafeGain)} dB");
            }
            catch (Exception e) when (e is not OperationCanceledException)
            {
                runLog.Track(file, note: "error");
                runLog.Error($"Clipping check failed for {file}: {e.Message}");
            }
        }
    }

    /// <summary>Sets the album gain to the lowest track gain and the album peak to the highest track peak, and writes
    /// the LUFS comment.</summary>
    public static void WriteAlbumValues(string folder, IReadOnlyList<string> files, RunLog runLog)
    {
        List<double> gains = [], peaks = [];
        foreach (var file in files)
        {
            try
            {
                using var tags = RgTags.Open(Path.Combine(folder, file));
                if (ReadTrackValues(tags, file, runLog) is not var (gain, peak)) continue;
                gains.Add(gain);
                peaks.Add(peak);
            }
            catch (Exception e) when (e is not OperationCanceledException)
            {
                runLog.Error($"Could not read metadata from {file}: {e.Message}");
            }
        }
        if (gains.Count == 0)
        {
            runLog.Warn("No valid ReplayGain data extracted, album gain not written. Ensure files are correctly tagged.");
            return;
        }
        var (albumGain, albumPeak) = (gains.Min(), peaks.Max());
        runLog.Album = (albumGain, albumPeak);
        foreach (var file in files)
        {
            try
            {
                using var tags = RgTags.Open(Path.Combine(folder, file));
                tags.Set(RgTags.AlbumGain, $"{Python.Repr(albumGain)} dB");
                tags.Set(RgTags.AlbumPeak, Python.Repr(albumPeak));
                tags.SetComment(runLog.Options.LufsComment);
                tags.Save();
            }
            catch (Exception e) when (e is not OperationCanceledException)
            {
                runLog.Error($"Could not write album metadata to {file}: {e.Message}");
            }
        }
    }

    /// <summary>Copies the album's ReplayGain data to the files of its keyword subfolders that aren't discs (the discs
    /// were measured with the album, and say so). In track-data subfolders (e.g. "lite", but not excluded ones like
    /// "bonus"), the track data of the file with the same name in the album folder is copied too.</summary>
    public static void CopyToSubfolders(string folder, IReadOnlyList<string> files, RunLog runLog,
        IReadOnlySet<string> exclude)
    {
        var options = runLog.Options;
        var all = KeywordSubfolders(folder, options);
        runLog.Subfolders = [];
        foreach (var disc in all.Where(d => IsDisc(d, options)))
        {
            var measured = files.Count(f => Path.GetDirectoryName(f) == disc);
            if (measured > 0) runLog.Subfolders.Add($"{disc}: {Report.Plural(measured, "file")} measured with the album");
        }
        var subfolders = all.Where(d => !files.Any(f => Path.GetDirectoryName(f) == d)).ToList();  // a disc left out: copied as before
        if (subfolders.Count == 0) return;

        // Every album file now holds the same album data: the first one serves as reference.
        var referenceFile = files[0];
        var albumData = new List<(string Key, string Value)>();
        try
        {
            using var reference = RgTags.Open(Path.Combine(folder, referenceFile));
            foreach (var key in new[] { RgTags.AlbumGain, RgTags.AlbumPeak })
                if (reference.Get(key) is { } value) albumData.Add((key, value));
        }
        catch (Exception e) when (e is not OperationCanceledException)
        {
            runLog.Error($"Could not read reference file {referenceFile}, subfolders not updated: {e.Message}");
            return;
        }

        foreach (var subfolder in subfolders)
        {
            var name = subfolder.ToLowerInvariant();
            var copyTrackData = options.TrackDataKeywords.Any(k => name.Contains(k, StringComparison.Ordinal)) &&
                                !options.ExcludedKeywords.Any(k => name.Contains(k, StringComparison.Ordinal));
            var path = Path.Combine(folder, subfolder);
            var images = ImageNames(path);
            var folderFiles = ListAudio(path).Supported
                .Where(f => !exclude.Contains(Fold(f)) && !images.Contains(Fold(f))).ToList();
            int updated = 0, withTrackData = 0;
            foreach (var audioFile in folderFiles)
            {
                try
                {
                    using var tags = RgTags.Open(Path.Combine(path, audioFile));
                    foreach (var (key, value) in albumData) tags.Set(key, value);
                    if (copyTrackData && files.Contains(audioFile))
                    {
                        try
                        {
                            using var albumFile = RgTags.Open(Path.Combine(folder, audioFile));
                            foreach (var key in new[] { RgTags.TrackGain, RgTags.TrackPeak })
                                if (albumFile.Get(key) is { } value) tags.Set(key, value);
                            withTrackData++;
                        }
                        catch (Exception e) when (e is not OperationCanceledException)
                        {
                            runLog.Error($"Could not read track data from {audioFile} for '{subfolder}': {e.Message}");
                        }
                    }
                    tags.Save();
                    updated++;
                }
                catch (Exception e) when (e is not OperationCanceledException)
                {
                    runLog.Error($"Could not update {audioFile} in '{subfolder}': {e.Message}");
                }
            }
            var summary = $"{subfolder}: {updated}/{folderFiles.Count} files got the album data";
            if (copyTrackData) summary += $", {withTrackData} also got their track data";
            runLog.Subfolders.Add(summary);
        }
    }

    /// <summary>Runs the album workflow in <paramref name="folder"/> and writes its report to the log file.
    /// <paramref name="exclude"/>: file names that aren't tracks (Tidy's unsplit images, SPEC §28), neither measured
    /// nor given the album values. Returns the RunLog, or null when the folder has no file rsgain can scan. A crash is
    /// recorded in the log (result CRASHED), not thrown.</summary>
    public static RunLog? Process(string folder, ReplayGainOptions options, Scanner? scanner = null, string? exe = null,
        CancellationToken cancel = default, IReporter? reporter = null, Func<DateTime>? now = null,
        IEnumerable<string>? exclude = null)
    {
        scanner ??= Rsgain.Scan;
        exe ??= Rsgain.Exe;
        reporter ??= NullReporter.Instance;
        var skip = (exclude ?? []).Select(Fold).ToHashSet();
        var (files, unsupported) = ListAudio(folder);
        var images = ImageNames(folder, files.Concat(unsupported));
        var waiting = files.Where(n => images.Contains(Fold(n)) && !skip.Contains(Fold(n))).ToList();
        files = files.Where(n => !skip.Contains(Fold(n)) && !images.Contains(Fold(n))).ToList();
        // The discs' tracks join the scan, so each gets its own track values and the album values cover every disc.
        var (discTracks, discLeft) = files.Count > 0 ? DiscTracks(folder, options, skip) : ([], []);
        files.AddRange(discTracks);
        if (files.Count == 0)
        {
            reporter.Info(waiting.Count > 0 ? $"Only an image waiting to be split in {folder}" : $"No audio file rsgain can scan in {folder}");
            return null;
        }

        var runLog = new RunLog(folder, files, options, exe, reporter, now);
        try
        {
            foreach (var name in unsupported)
                if (!skip.Contains(Fold(name)))  // an excluded file isn't a track of this run at all
                    runLog.Warn($"{name}: {UnsupportedReason(name)}");
            foreach (var name in waiting) runLog.Warn($"{name}: skipped, image waiting to be split");
            foreach (var (name, reason) in discLeft) runLog.Warn($"{name}: {reason}");
            if (ScanAlbum(folder, files, runLog, scanner, exe, cancel))
            {
                PreventClipping(folder, files, runLog);
                WriteAlbumValues(folder, files, runLog);
                CopyToSubfolders(folder, files, runLog, skip);
            }
        }
        catch (Exception e)
        {
            Log.Error($"ReplayGain crashed in {folder}", e);
            runLog.Crash(e);
        }
        finally
        {
            runLog.Write();
            reporter.Info($"Report written to {options.LogFile}: {runLog.Result()}");
        }
        return runLog;
    }
}
