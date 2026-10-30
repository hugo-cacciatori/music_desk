using System.Globalization;
using System.Text.Json;
using Refrain.Core.Cue;
using Xunit;

namespace Refrain.Tests;

/// <summary>Trim (SPEC §23, §36 "9." step d), held to the Python app's own <c>core/trim.py</c>, <c>core/waveform.py</c>
/// and <c>core/playlists.py</c> with the bundled flac: the plans of a set of edits, the copies cut on twin folders
/// (the proof, the files written, every tag and picture), the waveform's peaks and Preview in player's sheet.</summary>
public sealed class TrimTests
{
    static string Hidden => Path.Combine(Setup.Fixtures, "split", "hidden_track", "Fixture Artist - Split Album.flac");

    // (start, end, subfolder) as typed in the window: valid edits and every refusal.
    static readonly (string Start, string End, string Subfolder)[] Edits =
    [
        ("0:00.000", "0:08.000", "lite"), ("0:02.350", "0:12.000", "lite"), ("0:01", "0:5.5", "lite"), ("0:00.000", "0:12.000", "lite"),
        ("x", "0:12.000", "lite"), ("0:05.000", "0:04.000", "lite"), ("0:12.000", "0:12.000", "lite"), ("0:00.000", "0:12.001", "lite"),
        ("0:01.000", "0:02.000", ""), ("0:01.000", "0:02.000", "a:b"), ("0:01.000", "0:02.000", "lite."), ("0:01.000", "0:02.000", "com1.x"),
        ("0:01.000", "0:02.000", new string('x', 260)), ("0:01.000", "0:02.000", "Fixture Artist - Split Album.flac"), ("1:00.000", "0:02.000", "lite"),
    ];

    static List<string> PlanLines(TrimPlan plan) =>
    [
        $"{plan.Start} {plan.End} {plan.Name} {plan.Note ?? "None"} {(plan.TimesOk ? "True" : "False")}",
        .. plan.Problems.Select(p => $"{p.Field ?? "None"}: {p.Why}"),
        plan.TimesOk && plan.Length > 0 ? plan.Summary : "",
    ];

    [Fact]
    public void Edits_are_planned_as_the_python_app_plans_them()
    {
        using var temp = new TempFolder();
        var folder = temp.Sub("album");
        var source = Path.Combine(folder, Path.GetFileName(Hidden));
        File.Copy(Hidden, source);
        Directory.CreateDirectory(Path.Combine(folder, "lite"));
        File.WriteAllText(Path.Combine(folder, "lite", "Fixture Artist - Split Album.flac"), "");  // an earlier trim: numbered
        var output = PythonApp.Run("""
            import json, sys
            from refrain.core import trim
            args = json.load(sys.stdin)
            info = trim.read_source(args["source"])
            out = []
            for start, end, sub in args["edits"]:
                p = trim.plan_trim(args["source"], info, start, end, sub)
                out.append([f"{p.start} {p.end} {p.name} {p.note} {p.times_ok}", *[f"{w}: {why}" for w, why in p.problems],
                            p.summary if p.times_ok and p.length > 0 else ""])
            out.append([trim.format_time(trim.length_ms(info)), str(trim.unsupported_reason("a.mp3")), str(trim.unsupported_reason("a"))])
            print(json.dumps(out, ensure_ascii=False))
            """, JsonSerializer.Serialize(new { source, edits = Edits.Select(e => new[] { e.Start, e.End, e.Subfolder }) }));
        var expected = JsonSerializer.Deserialize<List<List<string>>>(output)!;
        var info = Trim.ReadSource(source);
        for (var i = 0; i < Edits.Length; i++)
            Assert.Equal(expected[i], PlanLines(Trim.Plan(source, info, Edits[i].Start, Edits[i].End, Edits[i].Subfolder)));
        Assert.Equal(expected[^1], [Trim.FormatTime(Trim.LengthMs(info)), Trim.UnsupportedReason("a.mp3")!, Trim.UnsupportedReason("a")!]);
        Assert.Contains(expected.SelectMany(l => l), l => l.Contains("(2).flac", StringComparison.Ordinal));
        Assert.Equal("FLAC 16-bit/44.1 kHz · 0:12.000", Trim.FormatLine(info));
    }

    [Fact]
    public void Times_round_trip_and_the_name_checks_match()
    {
        Assert.Equal(62_005, Trim.ParseTime(" 1:02.005 "));
        Assert.Equal(75_500, Trim.ParseTime("1:15.5"));
        Assert.Equal(3_600_000, Trim.ParseTime("60:00"));
        Assert.Null(Trim.ParseTime("1:60.000"));
        Assert.Null(Trim.ParseTime("1:00.0000"));
        Assert.Equal("61:01.050", Trim.FormatTime(3_661_050));
        Assert.Equal(44_100, Trim.ToSamples(1000, 44_100));
        Assert.Equal(1, Trim.ToSamples(1, 500));  // half a sample rounds up
        Assert.Equal(1000, Trim.ToMs(44_100, 44_100));
        Assert.Equal(48, Trim.ToSamples(1, 48_000));
        string[] names = ["lite", "", " ", "a/b", "x?", "end.", "end ", "NUL", "Lpt9.txt", "con x", "ok name"];
        var output = PythonApp.Run("""
            import json, sys
            from refrain.core import trim
            print(json.dumps([str(trim.check_subfolder_name(n)) for n in json.load(sys.stdin)], ensure_ascii=False))
            """, JsonSerializer.Serialize(names));
        Assert.Equal(JsonSerializer.Deserialize<List<string>>(output)!, names.Select(n => Trim.CheckSubfolderName(n) ?? "None"));
    }

    /// <summary>Tags worth copying, on both twins: lyrics to leave out, ReplayGain to keep, a repeated key, covers.</summary>
    const string TagFixtures = """
        import json, sys
        from pathlib import Path
        from mutagen.flac import FLAC, Picture
        from mutagen.id3 import APIC, TXXX, USLT
        from mutagen import File
        root = Path(json.load(sys.stdin)["root"])
        flac = FLAC(root / "tone.flac")
        flac["LYRICS"] = "la la"
        flac["unsyncedlyrics"] = "la"
        flac["REPLAYGAIN_TRACK_GAIN"] = "-3.21 dB"
        flac["replaygain_album_peak"] = "0.912345"
        flac["PERFORMER"] = ["One", "Two"]
        for kind in (3, 4):
            p = Picture()
            p.type, p.mime, p.desc, p.data = kind, "image/png", f"pic {kind}", bytes([0x89, 0x50, 0x4E, 0x47, kind] * 20)
            flac.add_picture(p)
        flac.save()
        for name in ("tone.wav", "tone.aiff"):
            audio = File(root / name)
            audio.tags.add(TXXX(encoding=3, desc="replaygain_track_gain", text=["-2.00 dB"]))
            audio.tags.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_PEAK", text=["0.5"]))
            audio.tags.add(USLT(encoding=3, lang="eng", desc="", text="words"))
            audio.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=bytes([0xFF, 0xD8, 0xFF, 0xE0] * 30)))
            audio.save()
        """;

    [Fact]
    public void A_trim_writes_the_python_apps_copy_and_tags()
    {
        using var twins = new ReplayGainTests.Twins("formats", side => File.Copy(Hidden, Path.Combine(side, Path.GetFileName(Hidden))));
        foreach (var side in new[] { twins.Py, twins.Cs }) PythonApp.Run(TagFixtures, JsonSerializer.Serialize(new { root = side }));
        (string Name, string Start, string End)[] cuts =
        [
            ("tone.flac", "0:00.100", "0:00.900"), ("tone.wav", "0:00.250", "0:01.000"), ("tone.aiff", "0:00.000", "0:00.500"),
            ("Fixture Artist - Split Album.flac", "0:09.000", "0:12.000"),
        ];
        var output = PythonApp.Run("""
            import json, sys
            from pathlib import Path
            from refrain.core import trim
            from refrain.core.flac import FlacRunner
            args = json.load(sys.stdin)
            runner = FlacRunner(Path(args["flac"]))
            out = []
            for name, start, end in args["cuts"]:
                source = Path(args["root"]) / name
                plan = trim.plan_trim(source, trim.read_source(source), start, end, "lite")
                cut = trim.cut_trim(plan, runner)
                finished = trim.finish_trim(plan, cut) if cut.ok else None
                out.append([str(cut.ok), str(cut.error), str(cut.samples), str(cut.md5), str(cut.created_folder),
                            plan.shown_path, *([] if finished is None else [str(finished.error), *finished.warnings])])
            print(json.dumps(out, ensure_ascii=False))
            """, JsonSerializer.Serialize(new { root = twins.Py, flac = SplitTests.FlacExe, cuts = cuts.Select(c => new[] { c.Name, c.Start, c.End }) }));
        var expected = JsonSerializer.Deserialize<List<List<string>>>(output)!;

        var flac = new Flac(SplitTests.FlacExe);
        var cancel = TestContext.Current.CancellationToken;
        for (var i = 0; i < cuts.Length; i++)
        {
            var source = Path.Combine(twins.Cs, cuts[i].Name);
            var plan = Trim.Plan(source, Trim.ReadSource(source), cuts[i].Start, cuts[i].End, "lite");
            var cut = Trim.Cut(plan, flac, cancel);
            var finished = cut.Ok ? Trim.Finish(plan, cut) : null;
            List<string> actual = [cut.Ok ? "True" : "False", cut.Error ?? "None", cut.Samples.ToString(CultureInfo.InvariantCulture), cut.Md5 ?? "None",
                cut.CreatedFolder ? "True" : "False", plan.ShownPath, .. finished is null ? [] : new[] { finished.Error ?? "None" }.Concat(finished.Warnings)];
            Assert.Equal(expected[i], actual);
            Assert.True(cut.Ok, cut.Error);
        }
        ReplayGainTests.AssertSame(twins);  // the copies, every tag and picture as mutagen reads them; the originals untouched
        Assert.Empty(Directory.GetFiles(twins.Cs, "*.partial", SearchOption.AllDirectories));
        var copy = File.ReadAllText(Path.Combine(twins.Cs, "lite", "tone.flac"), System.Text.Encoding.Latin1);
        Assert.DoesNotContain("la la", copy);
        Assert.Contains("-3.21 dB", copy);
    }

    [Fact]
    public void A_stop_or_a_short_drive_leaves_the_folder_as_it_was()
    {
        using var temp = new TempFolder();
        var folder = temp.Sub("album");
        var source = Path.Combine(folder, "track.flac");
        File.Copy(Hidden, source);
        var plan = Trim.Plan(source, Trim.ReadSource(source), "0:01.000", "0:05.000", "lite");
        var flac = new Flac(SplitTests.FlacExe);
        using var stopped = new CancellationTokenSource();
        stopped.Cancel();
        var outcome = TrimTask.Run(plan, flac, stopped.Token);
        Assert.True(outcome.Cancelled);
        Assert.Equal("Stopped: nothing left half-done. The copy being written was deleted; the original is unchanged.", outcome.Text());
        Assert.Equal([source], Directory.GetFileSystemEntries(folder));  // the subfolder this run made is gone

        var short_ = Trim.Cut(plan, flac, TestContext.Current.CancellationToken, freeSpace: _ => 10);
        Assert.StartsWith("Not enough free space: the copy needs about ", short_.Error);
        Assert.Equal([source], Directory.GetFileSystemEntries(folder));

        Directory.CreateDirectory(Path.Combine(folder, "lite"));  // a subfolder there before the run stays
        Assert.True(TrimTask.Run(plan, flac, stopped.Token).Cancelled);
        Assert.True(Directory.Exists(Path.Combine(folder, "lite")));

        var done = TrimTask.Run(plan, flac, TestContext.Current.CancellationToken);
        Assert.True(done.Trimmed, done.Error);
        Assert.StartsWith(@"Done: lite\track.flac, 0:04.000, identical to its part of the original.", done.Text());
        var again = Trim.Plan(source, plan.Info, "0:01.000", "0:05.000", "lite");
        Assert.Equal("track (2).flac", again.Name);
        Assert.Equal("track.flac is already in lite, so the copy is named track (2).flac.", again.Note);
    }

    [Fact]
    public void The_waveform_has_the_python_apps_peaks()
    {
        var files = new[] { Hidden, Path.Combine(Setup.Fixtures, "formats", "tone.wav"), Path.Combine(Setup.Fixtures, "formats", "tone.aiff") };
        // Raw PCM of 24-bit stereo and 20-bit mono, fed in uneven chunks: the reducer alone.
        var random = new Random(7);
        var pcm24 = new byte[3 * 2 * 1000];
        random.NextBytes(pcm24);
        var pcm20 = new byte[3 * 777];
        random.NextBytes(pcm20);
        for (var i = 2; i < pcm20.Length; i += 3) pcm20[i] = (byte)(sbyte)random.Next(-8, 8);
        var output = PythonApp.Run("""
            import json, sys
            from pathlib import Path
            from refrain.core import trim, waveform
            from refrain.core.flac import FlacRunner
            args = json.load(sys.stdin)
            runner = FlacRunner(Path(args["flac"]))
            out = []
            for f in args["files"]:
                for columns in (4096, 37):
                    p = waveform.read_peaks(runner, f, trim.read_source(f), columns=columns)
                    out.append([p.lows, p.highs])
            for data, samples, channels, bits in ((args["pcm24"], 1000, 2, 24), (args["pcm20"], 777, 1, 20)):
                data = bytes.fromhex(data)
                r = waveform.PeakReducer(samples, channels, bits, 50)
                for i in range(0, len(data), 1001):
                    r.feed(data[i:i + 1001])
                p = r.result()
                out.append([p.lows, p.highs])
            p = waveform.Peaks([-0.5, 0.0, -1.0], [0.5, 0.25, 0.0])
            out.append([[a for a, b in waveform.fit(p, 2)], [b for a, b in waveform.fit(p, 2)]])
            out.append([[a for a, b in waveform.fit(p, 5)], [b for a, b in waveform.fit(p, 5)]])
            print(json.dumps(out))
            """, JsonSerializer.Serialize(new { files, flac = SplitTests.FlacExe, pcm24 = Convert.ToHexString(pcm24), pcm20 = Convert.ToHexString(pcm20) }));
        var expected = JsonSerializer.Deserialize<List<List<List<double>>>>(output)!;
        var flac = new Flac(SplitTests.FlacExe);
        var actual = new List<List<List<double>>>();
        foreach (var file in files)
            foreach (var columns in new[] { 4096, 37 })
            {
                var peaks = Waveform.Read(flac, file, Trim.ReadSource(file), TestContext.Current.CancellationToken, columns);
                actual.Add([[.. peaks.Lows], [.. peaks.Highs]]);
            }
        foreach (var (data, samples, channels, bits) in new[] { (pcm24, 1000L, 2, 24), (pcm20, 777L, 1, 20) })
        {
            var reducer = new PeakReducer(samples, channels, bits, 50);
            for (var i = 0; i < data.Length; i += 1001) reducer.Feed(data.AsSpan(i, Math.Min(1001, data.Length - i)));
            var peaks = reducer.Result();
            actual.Add([[.. peaks.Lows], [.. peaks.Highs]]);
        }
        var small = new Peaks([-0.5, 0.0, -1.0], [0.5, 0.25, 0.0]);
        foreach (var width in new[] { 2, 5 })
        {
            var fitted = Waveform.Fit(small, width);
            actual.Add([[.. fitted.Select(c => c.Low)], [.. fitted.Select(c => c.High)]]);
        }
        Assert.Equal(expected.Count, actual.Count);
        for (var i = 0; i < expected.Count; i++)
        {
            Assert.Equal(expected[i][0], actual[i][0]);
            Assert.Equal(expected[i][1], actual[i][1]);
        }
    }

    [Fact]
    public void Preview_in_player_writes_the_python_apps_sheet()
    {
        var info = Trim.ReadSource(Hidden);
        (string Start, string End)[] edits = [("0:02.350", "0:09.000"), ("0:00.000", "0:09.000"), ("0:03.000", "0:12.000")];
        var output = PythonApp.Run("""
            import json, sys
            from refrain.core import playlists, trim
            args = json.load(sys.stdin)
            info = trim.read_source(args["source"])
            out = []
            for start, end in args["edits"]:
                plan = trim.plan_trim(args["source"], info, start, end, "lite")
                out.append(playlists.render_preview(trim.preview_sheet(plan, "Song \"A\"", "Artist", "Album")))
            out.append(playlists.render_preview(trim.preview_sheet(trim.plan_trim(args["source"], info, "0:01", "0:02", "lite"))))
            print(json.dumps(out, ensure_ascii=False))
            """, JsonSerializer.Serialize(new { source = Hidden, edits = edits.Select(e => new[] { e.Start, e.End }) }));
        var expected = JsonSerializer.Deserialize<List<string>>(output)!;
        var actual = edits.Select(e => Trim.PreviewText(Trim.Plan(Hidden, info, e.Start, e.End, "lite"), "Song \"A\"", "Artist", "Album")).ToList();
        actual.Add(Trim.PreviewText(Trim.Plan(Hidden, info, "0:01", "0:02", "lite")));
        Assert.Equal(expected, actual);
        Assert.Contains("Cut from the end", actual[0]);
        Assert.DoesNotContain("Cut from the start", actual[1]);
    }
}
