using System.Text.Json;
using System.Text.RegularExpressions;
using Refrain.Core.Cue;
using Refrain.Core.Options;
using Refrain.Core.ReplayGain;
using Xunit;

namespace Refrain.Tests;

/// <summary>The ReplayGain engine (SPEC §10 B, §36 "8." step b), held to the Python app's own code: both run the real
/// bundled rsgain on twin copies of the fixtures, then every file's tags (read by mutagen, as the Python app reads them)
/// and every replaygain.log must match, with the twin folder, clock times and durations masked.</summary>
public sealed class ReplayGainTests
{
    internal static string Rsgain
    {
        get
        {
            for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir is not null; dir = dir.Parent)
            {
                var candidate = Path.Combine(dir.FullName, "resources", "rsgain", "rsgain.exe");
                if (File.Exists(candidate)) return candidate;
            }
            throw new FileNotFoundException("resources/rsgain/rsgain.exe not found");
        }
    }

    static readonly ReplayGainOptions Options = ReplayGainOptions.Default;

    /// <summary>The same tree twice: "py" for the Python app, "cs" for the port.</summary>
    internal sealed class Twins : IDisposable
    {
        readonly TempFolder temp = new();
        public string Py { get; }
        public string Cs { get; }

        public Twins(string relative, Action<string>? prepare = null)
        {
            var source = Path.Combine(Setup.Fixtures, relative);
            var name = Path.GetFileName(relative);
            Py = Path.Combine(temp.Sub("py"), name);
            Cs = Path.Combine(temp.Sub("cs"), name);
            foreach (var side in new[] { Py, Cs })
            {
                Setup.Copy(source, side);
                prepare?.Invoke(side);
            }
        }

        public void Dispose() => temp.Dispose();
    }

    /// <summary>Runs the Python app's ReplayGain on <paramref name="target"/>.</summary>
    static string RunPython(string mode, string target, IEnumerable<string>? tracks = null) =>
        PythonApp.Run("""
            import json, sys
            from pathlib import Path
            from refrain.core import replaygain as rg
            args = json.load(sys.stdin)
            exe = Path(args["exe"])
            if args["mode"] == "album":
                rg.process_album(args["target"], exe=exe)
            elif args["mode"] == "discography":
                rg.process_discography(args["target"], exe=exe)
            elif args["mode"] == "loose":
                print(rg.process_loose_tracks(args["tracks"], exe=exe).text())
            elif args["mode"] == "remove":
                print(json.dumps([rg.remove_replaygain(t) for t in args["tracks"]]))
            """, JsonSerializer.Serialize(new { mode, target, exe = Rsgain, tracks = tracks?.ToArray() }));

    /// <summary>Every file of the tree: an audio file's tags as mutagen reads them (ID3 frames with their version),
    /// a log file's exact text, other files by name.</summary>
    internal static Dictionary<string, string?> Dump(string root)
    {
        var output = PythonApp.Execute("""
            import hashlib, json, sys
            from pathlib import Path
            from mutagen import File
            from refrain.core.formats import AUDIO_EXTENSIONS
            args = json.load(sys.stdin)
            root = Path(args["root"])
            out = {}
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                key = str(path.relative_to(root))
                if path.name == "replaygain.log":
                    out[key] = path.read_bytes().decode("utf-8")
                elif path.suffix.lower() in AUDIO_EXTENSIONS:
                    audio = File(path)
                    tags = audio.tags if audio is not None else None
                    if tags is None:
                        out[key] = "no tags"
                        continue
                    if hasattr(tags, "getall"):
                        items = sorted([k, f.pprint()] for k, f in tags.items()) + [["version", str(tags.version)]]
                    else:
                        items = sorted([str(k).lower(), repr(v)] for k, v in tags.items())
                    items += [["picture", hashlib.sha1(p.data).hexdigest()] for p in getattr(audio, "pictures", [])]
                    out[key] = json.dumps(items, ensure_ascii=False)
                else:
                    out[key] = None
            print(json.dumps(out))
            """, JsonSerializer.Serialize(new { root }));
        return JsonSerializer.Deserialize<Dictionary<string, string?>>(output)!;
    }

    internal static string Mask(string text, string root)
    {
        text = text.Replace(root, "<ROOT>");
        text = Regex.Replace(text, @"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", "<TIMESTAMP>");
        text = Regex.Replace(text, @"\b\d{2}:\d{2}:\d{2}\b", "<TIME>");
        return Regex.Replace(text, @"\d+\.\d s\b", "<N> s");
    }

    /// <summary>The two trees hold the same files, tags and logs. Returns how many logs were compared. While recording,
    /// the Python app's tree and the C# app's are read by mutagen and compared, and the C# tree as Refrain reads it is
    /// recorded; a normal run compares the C# tree with that recording (SPEC §36 "10." step c).</summary>
    internal static int AssertSame(Twins twins)
    {
        if (PythonApp.Recording) AssertSameAsPython(twins);
        var dump = TagDump.Of(twins.Cs);
        Assert.Equal(PythonApp.Recorded(dump, twins.Cs), dump);
        return Directory.GetFiles(twins.Cs, "replaygain.log", SearchOption.AllDirectories).Length;
    }

    static void AssertSameAsPython(Twins twins)
    {
        var (py, cs) = (Dump(twins.Py), Dump(twins.Cs));
        Assert.Equal(py.Keys.Order(), cs.Keys.Order());
        foreach (var (key, pyValue) in py)
        {
            if (Path.GetFileName(key) == "replaygain.log")
                Assert.Equal(Mask(pyValue!, twins.Py), Mask(cs[key]!, twins.Cs));
            else
            {
                // TagLib# writes an emptied WMA description as "" where mutagen leaves it out: the same empty comment.
                var csValue = cs[key]?.Replace("""["description", "[ASFUnicodeAttribute('')]"], """, "");
                Assert.True(pyValue == csValue, $"{key}\nPython: {pyValue}\nC#:     {csValue}");
            }
        }
    }

    [Fact]
    public void An_album_gets_the_python_apps_tags_and_log()
    {
        using var twins = new Twins(@"replaygain\Fixture Artist\Album A");
        RunPython("album", twins.Py);
        var runLog = Album.Process(twins.Cs, Options, exe: Rsgain, cancel: TestContext.Current.CancellationToken);

        Assert.Equal("OK", runLog!.Result());
        Assert.Equal(1, AssertSame(twins));
        Assert.Contains("reduced to prevent clipping", File.ReadAllText(Path.Combine(twins.Cs, "replaygain.log")));
    }

    [Fact]
    public void A_discography_is_leveled_as_the_python_app_does()
    {
        using var twins = new Twins(@"replaygain\Fixture Artist");
        RunPython("discography", twins.Py);
        var result = Discography.Process(twins.Cs, Options, exe: Rsgain, cancel: TestContext.Current.CancellationToken);

        Assert.Equal(["OK", "OK"], result!.Albums.Select(a => a.Result));
        Assert.Equal(3, AssertSame(twins));  // two album logs and the artist summary
    }

    [Fact]
    public void Every_format_is_tagged_as_the_python_app_tags_it()
    {
        // Ogg FLAC (.oga) is left out: TagLib# can't write it, so the port skips it with its own reason.
        using var twins = new Twins("formats", side => File.Delete(Path.Combine(side, "tone.oga")));
        RunPython("album", twins.Py);
        var runLog = Album.Process(twins.Cs, Options, exe: Rsgain, cancel: TestContext.Current.CancellationToken);

        Assert.Equal("OK", runLog!.Result());
        Assert.Equal(1, AssertSame(twins));
    }

    [Fact]
    public void Loose_tracks_and_removal_match_the_python_app()
    {
        using var twins = new Twins("formats", side =>
        {
            File.Delete(Path.Combine(side, "tone.oga"));
            File.Copy(Path.Combine(Setup.Fixtures, "album", "Fixture Artist - Fixture Album", "lowercase_rg.mp3"),
                Path.Combine(side, "lowercase_rg.mp3"));
        });
        string[] Tracks(string root) => [.. Directory.GetFiles(root).Order(StringComparer.Ordinal), Path.Combine(root, "missing.flac")];

        var pyText = RunPython("loose", twins.Py, Tracks(twins.Py)).Replace("\r\n", "\n");
        var result = Loose.Process(Tracks(twins.Cs), Options, exe: Rsgain, cancel: TestContext.Current.CancellationToken);
        Assert.Equal(Mask(pyText, twins.Py), Mask(result.Text() + "\n", twins.Cs));
        Assert.Equal("OK", result.Result());
        AssertSame(twins);

        // True Audio (.tta) is refused: TagLib# can't open it.
        List<string> Removable(string root) => [.. Directory.GetFiles(root).Where(p => Path.GetExtension(p) != ".tta").Order(StringComparer.Ordinal)];
        var removed = JsonSerializer.Deserialize<bool[]>(RunPython("remove", twins.Py, Removable(twins.Py)))!;
        Assert.Equal(removed, Removable(twins.Cs).Select(p => Remove.Run(p, Options)));
        Assert.Contains(true, removed);
        AssertSame(twins);
        var refused = Assert.Throws<NotSupportedException>(() => Remove.Run(Path.Combine(twins.Cs, "tone.tta"), Options));
        Assert.Equal("Refrain can't write the tags of TTA files.", refused.Message);
    }

    [Fact]
    public void An_unsplit_image_is_never_measured()
    {
        using var twins = new Twins(@"replaygain\Fixture Artist\Album A", side =>
        {
            File.Copy(Path.Combine(Setup.Fixtures, "formats", "tone.flac"), Path.Combine(side, "Image.flac"));
            File.WriteAllText(Path.Combine(side, "Image.cue"), "FILE \"Image.flac\" WAVE\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n");
        });
        RunPython("album", twins.Py);
        var runLog = Album.Process(twins.Cs, Options, exe: Rsgain, cancel: TestContext.Current.CancellationToken);

        Assert.DoesNotContain("Image.flac", runLog!.Files);
        Assert.Equal(1, AssertSame(twins));
        Assert.Contains("Image.flac: skipped, image waiting to be split", File.ReadAllText(Path.Combine(twins.Cs, "replaygain.log")));
    }

    [Fact]
    public void Python_number_formatting_is_reproduced()
    {
        double[] values = [0, -0.0, 5, -10.4, 6.75, 0.124969, 0.990021, 1e-5, 1.5e-4, 1e15, 1e16, 123456789.125, 0.125,
            0.375, -2.675, 2.5, -0.005, 1 / 3.0, 20 * Math.Log10(0.99999 / 0.990021) - 0.01];
        // Sent as round-trip text, so Python reads floats (JSON's 5 would be an int) and keeps negative zero.
        var output = PythonApp.Run("""
            import json, sys
            values = [float(v) for v in json.load(sys.stdin)]
            print(json.dumps([[repr(v), f"{v:.2f}", f"{v:.1f}", repr(round(v, 2))] for v in values]))
            """, JsonSerializer.Serialize(values.Select(v => v.ToString("R", System.Globalization.CultureInfo.InvariantCulture))));
        var expected = JsonSerializer.Deserialize<string[][]>(output)!;
        var actual = values
            .Select(v => new[] { Python.Repr(v), Python.Fixed(v, 2), Python.Fixed(v, 1), Python.Repr(Python.Round(v, 2)) });
        Assert.Equal(expected, actual);
    }

    [Fact]
    public void Unsplit_images_follow_the_single_file_sheets()
    {
        using var temp = new TempFolder();
        File.WriteAllText(Path.Combine(temp.Path, "a.cue"), "FILE \"Album.flac\" WAVE\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n");
        Assert.Equal(["Album.FLAC"], CueSheet.UnsplitImages(temp.Path, ["Album.FLAC", "01 x.flac"]));
        // A sheet naming another file: the folder's only audio file is the image.
        Assert.Equal(["Other.wav"], CueSheet.UnsplitImages(temp.Path, ["Other.wav"]));
        Assert.Empty(CueSheet.UnsplitImages(temp.Path, ["01 x.flac", "02 y.flac"]));
        File.WriteAllText(Path.Combine(temp.Path, "b.cue"), "FILE \"1.flac\" WAVE\nFILE \"2.flac\" WAVE\n");
        Assert.Equal(["Album.FLAC"], CueSheet.UnsplitImages(temp.Path, ["Album.FLAC", "1.flac", "2.flac"]));
    }

    [Fact]
    public void The_lufs_comment_pattern_matches_any_target()
    {
        var pattern = Remove.LufsCommentPattern("LUFS Target : {target}");
        Assert.Matches(pattern, "LUFS Target : -15");
        Assert.Matches(pattern, " LUFS Target : -18.5 ");
        Assert.DoesNotMatch(pattern, "LUFS Target : loud");
        Assert.DoesNotMatch(pattern, "My comment");
    }

    [Fact]
    public void A_scanner_failure_stops_the_album_and_says_why()
    {
        using var temp = new TempFolder();
        Setup.Copy(Path.Combine(Setup.Fixtures, "replaygain", "Fixture Artist", "Album B"), temp.Path);
        ScanResult Fail(IReadOnlyList<string> files, string folder, string target, string exe, CancellationToken cancel,
            Action<string>? onLine) => new(2, "", "\u001b[31mbad file\u001b[0m\n", 0.1);

        var runLog = Album.Process(temp.Path, Options, Fail, exe: "rsgain.exe", cancel: TestContext.Current.CancellationToken);

        Assert.Equal("STOPPED", runLog!.Result());
        var log = File.ReadAllText(Path.Combine(temp.Path, "replaygain.log"));
        Assert.Contains("Scan: FAILED (exit code 2)", log);
        Assert.Contains("rsgain scan failed (exit code 2):", log);
        Assert.Contains("    bad file", log);
    }
}
