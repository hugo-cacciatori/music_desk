using System.Text;
using Refrain.Core;
using Refrain.Core.Cue;
using Refrain.Core.Index;
using Refrain.Core.ReplayGain;

namespace Refrain;

/// <summary>The release gate (AGENTS.md rule 9): <c>Refrain.exe --self-test report.txt</c> runs the installer's build
/// with no window, from a copy outside the repository on an empty data folder, through what a published build can
/// break (Native AOT trims what is only reached by reflection; a bundled file may be missing): the index, the settings,
/// tags read and written by TagLib#, flac's cut and decode, rsgain, the right-click handler and the keyword's helper
/// beside the app. Exit code 0 when every check passed; the report says what each found.</summary>
static class SelfTest
{
    public static int Run(string report)
    {
        var lines = new List<string> { $"Refrain {typeof(SelfTest).Assembly.GetName().Version} self-test, release build: {AppPaths.Release}" };
        var failed = 0;
        void Check(string name, Func<string> test)
        {
            try
            {
                lines.Add($"ok    {name}: {test()}");
            }
            catch (Exception e)
            {
                failed++;
                lines.Add($"FAIL  {name}: {e.GetType().Name}: {e.Message}");
            }
        }

        var work = Directory.CreateTempSubdirectory("refrain-self-test-").FullName;
        try
        {
            Check("data folder", () => AppPaths.DataFolder);
            Check("index", () =>
            {
                using var db = new Database(Path.Combine(work, "library.db"));
                using var conn = db.Read();
                return $"schema {Schema.CurrentVersion(conn)}, {Roots.All(conn).Count} folders";
            });
            Check("settings", () =>
            {
                var file = Path.Combine(work, "settings.json");
                var settings = new Settings { TrimSubfolder = "check" };
                settings.Set(settings.GetReplayGain() with { Target = -18 });
                settings.Save(file);
                var read = Settings.Load(file);
                if (read.TrimSubfolder != "check" || read.GetReplayGain().Target != -18) throw new InvalidDataException("not read back");
                return "saved and read back";
            });
            foreach (var (name, path) in new[]
                     {
                         ("flac", Flac.Exe), ("rsgain", Rsgain.Exe), ("right-click handler", Path.Combine(AppContext.BaseDirectory, "RefrainMenu.dll")),
                         ("keyword helper", Path.Combine(AppContext.BaseDirectory, "RefrainHere.exe")),
                     })
                Check(name + " present", () => File.Exists(path) ? path : throw new FileNotFoundException("missing", path));
            Check("flac runs", () => Flac.Version(Flac.Exe) ?? throw new InvalidOperationException("no version"));
            Check("rsgain runs", () => Rsgain.Version(Rsgain.Exe) ?? throw new InvalidOperationException("no version"));
            Check("tags and the cut", () =>
            {
                // One second of a 16-bit stereo tone as WAV, cut to FLAC by flac, tagged and read back by TagLib#.
                var wav = Path.Combine(work, "tone.wav");
                WriteTone(wav, 44_100);
                var source = SplitPlanner.ReadSource(wav);
                var flac = new Flac(Flac.Exe);
                var cut = Path.Combine(work, "tone.flac");
                flac.Encode(wav, 4_410, null, cut);
                Core.Tags.TagFile.Save(cut, new Dictionary<Core.Tags.Field, string> { [Core.Tags.Field.Title] = "Ünïcode ✓", [Core.Tags.Field.Track] = "3" }, null);
                var read = Core.Tags.TagFile.Read(cut);
                if (read.Values[Core.Tags.Field.Title] != "Ünïcode ✓" || read.Values[Core.Tags.Field.Track] != "3") throw new InvalidDataException("tags not read back");
                var peaks = Waveform.Read(flac, cut, SplitPlanner.ReadSource(cut), columns: 16);
                return $"{source.Samples} samples in, {read.Details.FormatLine}, {peaks.Count} waveform columns";
            });
        }
        finally
        {
            try
            {
                Directory.Delete(work, recursive: true);  // the test's own temporary folder
            }
            catch (IOException)
            {
            }
        }
        lines.Add(failed == 0 ? "Every check passed." : $"{failed} check(s) failed.");
        File.WriteAllText(report, string.Join("\r\n", lines) + "\r\n", new UTF8Encoding(false));
        return failed == 0 ? 0 : 1;
    }

    /// <summary>A 440 Hz tone, 16-bit stereo, one second.</summary>
    static void WriteTone(string path, int rate)
    {
        using var writer = new BinaryWriter(File.Create(path));
        var samples = rate;
        var data = samples * 4;
        writer.Write("RIFF"u8);
        writer.Write(36 + data);
        writer.Write("WAVEfmt "u8);
        writer.Write(16);
        writer.Write((short)1);
        writer.Write((short)2);
        writer.Write(rate);
        writer.Write(rate * 4);
        writer.Write((short)4);
        writer.Write((short)16);
        writer.Write("data"u8);
        writer.Write(data);
        for (var i = 0; i < samples; i++)
        {
            var value = (short)(Math.Sin(2 * Math.PI * 440 * i / rate) * 8000);
            writer.Write(value);
            writer.Write(value);
        }
    }
}
