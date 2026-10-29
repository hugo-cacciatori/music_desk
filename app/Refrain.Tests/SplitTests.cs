using System.Globalization;
using System.Security.Cryptography;
using System.Text.Json;
using Refrain.Core.Cue;
using Refrain.Core.Options;
using Xunit;

namespace Refrain.Tests;

/// <summary>The cue split (SPEC §22, §36 "9." step a), held to the Python app's own <c>core/cue.py</c> and
/// <c>core/split.py</c> with the bundled flac: every fixture sheet planned by both (read only), and the splittable ones
/// cut by both on twin copies, comparing the proof, the files written and every tag.</summary>
public sealed class SplitTests
{
    internal static string FlacExe
    {
        get
        {
            for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir is not null; dir = dir.Parent)
            {
                var candidate = Path.Combine(dir.FullName, "resources", "flac", "flac.exe");
                if (File.Exists(candidate)) return candidate;
            }
            throw new FileNotFoundException("resources/flac/flac.exe not found");
        }
    }

    static readonly FileNamesOptions Names = FileNamesOptions.Default;

    static IEnumerable<string> Sheets() =>
        Directory.GetFiles(Path.Combine(Setup.Fixtures, "split"), "*.cue", SearchOption.AllDirectories)
            .Append(Path.Combine(Setup.Fixtures, "cleanup", "single_file_album", "Fixture Album.cue"))
            .Order(StringComparer.Ordinal);

    const string PythonLines = """
        import hashlib
        def plan_lines(plan):
            lines = [f"refusal={plan.refusal}", f"source={plan.source.name if plan.source else None}"]
            if plan.info is not None:
                i = plan.info
                lines.append(f"info={i.format} {i.rate} {i.samples} {i.channels} {i.bits} {i.md5} "
                             f"{sorted((k, str(v)) for k, v in i.tags.items())} "
                             f"{hashlib.sha1(i.cover).hexdigest() if i.cover else None}")
            if plan.sheet is not None:
                lines.append(f"sheet={plan.sheet.encoding} {plan.sheet.fell_back} {plan.sheet.not_cp1252}")
            lines += [f"warning {w}" for w in plan.warnings]
            lines += [f"note {n} {w}" for n, w in plan.notes]
            lines += [f"problem {n} {w}" for n, w in plan.problems]
            for t in plan.tracks:
                lines.append(f"track {t.number} {t.start} {t.end} {t.name} isrc={t.isrc} "
                             f"{sorted((k, str(v)) for k, v in t.fields.items())}")
            return lines
        """;

    static string PyList(IEnumerable<(string, string)> pairs) =>
        "[" + string.Join(", ", pairs.OrderBy(p => p.Item1, StringComparer.Ordinal).Select(p => $"('{p.Item1}', '{p.Item2}')")) + "]";

    static List<string> PlanLines(AlbumPlan plan)
    {
        var lines = new List<string> { $"refusal={plan.Refusal ?? "None"}", $"source={(plan.Source is null ? "None" : Path.GetFileName(plan.Source))}" };
        if (plan.Info is { } i)
            lines.Add($"info={i.Format} {i.Rate} {i.Samples} {i.Channels} {i.Bits} {i.Md5 ?? "None"} " +
                      $"{PyList(i.Tags.Select(kv => (kv.Key, Convert.ToString(kv.Value, CultureInfo.InvariantCulture)!)))} " +
                      $"{(i.Cover is { } c ? Convert.ToHexStringLower(SHA1.HashData(c)) : "None")}");
        if (plan.Sheet is { } s) lines.Add($"sheet={s.Encoding} {(s.FellBack ? "True" : "False")} {(s.NotCp1252 ? "True" : "False")}");
        lines.AddRange(plan.Warnings.Select(w => $"warning {w}"));
        lines.AddRange(plan.Notes.Select(n => $"note {n.Track} {n.Why}"));
        lines.AddRange(plan.Problems.Select(n => $"problem {n.Track} {n.Why}"));
        lines.AddRange(plan.Tracks.Select(t => $"track {t.Number} {t.Start} {t.End} {t.Name} isrc={t.Isrc} " +
            PyList(t.Fields.Select(kv => (kv.Key, Convert.ToString(kv.Value, CultureInfo.InvariantCulture)!)))));
        return lines;
    }

    [Fact]
    public void Every_fixture_sheet_is_planned_as_the_python_app_plans_it()
    {
        var sheets = Sheets().ToList();
        var output = PythonApp.Run(PythonLines + """

            import json, sys
            from pathlib import Path
            from refrain.core import cue, split
            from refrain.core.flac import FlacRunner
            from refrain.core.masks import DEFAULT_MASKS, DEFAULT_REPLACEMENT
            args = json.load(sys.stdin)
            runner = FlacRunner(Path(args["flac"]))
            out = [plan_lines(cue.plan_album(p, DEFAULT_MASKS, DEFAULT_REPLACEMENT, is_silent=split.is_silent(runner)))
                   for p in args["sheets"]]
            print(json.dumps(out, ensure_ascii=False))
            """, JsonSerializer.Serialize(new { sheets, flac = FlacExe }));
        var expected = JsonSerializer.Deserialize<List<List<string>>>(output)!;
        var silent = Split.IsSilent(new Flac(FlacExe), TestContext.Current.CancellationToken);
        for (var i = 0; i < sheets.Count; i++)
            Assert.Equal(expected[i], PlanLines(SplitPlanner.Plan(sheets[i], Names.Masks, Names.Replacement, silent)));
        // The fixtures hold the cases: a hidden track, a pregap joined to track 1, a refusal, a warning, a renamed track.
        var all = expected.SelectMany(l => l).ToList();
        foreach (var part in new[] { "track 0 0 ", "refusal=Not a single-file album", "warning 1 data track", "note 2 ", "refusal=Track 2 starts after the end of the audio" })
            Assert.True(all.Any(line => line.StartsWith(part, StringComparison.Ordinal)), part + Environment.NewLine + string.Join(Environment.NewLine, all));
    }

    [Theory]
    [InlineData(@"split\hidden_track")]
    [InlineData(@"split\silent_pregap")]
    [InlineData(@"split\wav_image")]
    [InlineData(@"split\name_clash")]
    [InlineData(@"split\data_track")]
    [InlineData(@"cleanup\single_file_album")]
    public void A_cut_writes_the_python_apps_tracks_and_tags(string fixture)
    {
        using var twins = new ReplayGainTests.Twins(fixture);
        var cueName = Path.GetFileName(Directory.GetFiles(twins.Py, "*.cue").Single());
        var output = PythonApp.Run("""
            import json, sys
            from pathlib import Path
            from refrain.core import cue, split
            from refrain.core.flac import FlacRunner
            from refrain.core.masks import DEFAULT_MASKS, DEFAULT_REPLACEMENT
            args = json.load(sys.stdin)
            runner = FlacRunner(Path(args["flac"]))
            plan = cue.plan_album(args["cue"], DEFAULT_MASKS, DEFAULT_REPLACEMENT, is_silent=split.is_silent(runner))
            cut = split.cut_album(plan, runner)
            finished = split.finish_album(plan, cut) if cut.ok else None
            print(json.dumps([str(cut.ok), str(cut.error), str(cut.md5), str(cut.expected_md5), *cut.track_md5s,
                              *map(str, cut.lengths), *([] if finished is None else [Path(t).name for t in finished.tracks]),
                              *([] if finished is None else finished.warnings)], ensure_ascii=False))
            """, JsonSerializer.Serialize(new { cue = Path.Combine(twins.Py, cueName), flac = FlacExe }));
        var expected = JsonSerializer.Deserialize<List<string>>(output)!;

        var flac = new Flac(FlacExe);
        var cancel = TestContext.Current.CancellationToken;
        var plan = SplitPlanner.Plan(Path.Combine(twins.Cs, cueName), Names.Masks, Names.Replacement, Split.IsSilent(flac, cancel));
        var cut = Split.Cut(plan, flac, cancel);
        var finished = cut.Ok ? Split.Finish(plan, cut) : null;
        List<string> actual = [cut.Ok ? "True" : "False", cut.Error ?? "None", cut.Md5 ?? "None", cut.ExpectedMd5 ?? "None",
            .. cut.TrackMd5s, .. cut.Lengths.Select(l => l.ToString(CultureInfo.InvariantCulture)),
            .. finished?.Tracks.Select(t => Path.GetFileName(t)!) ?? [], .. finished?.Warnings ?? []];

        Assert.Equal(expected, actual);
        Assert.True(cut.Ok, cut.Error);
        ReplayGainTests.AssertSame(twins);  // the same files, every tag and cover as mutagen reads them
        Assert.Empty(Directory.GetFiles(twins.Cs, "*.partial"));
    }

    [Fact]
    public void A_stop_or_a_failed_proof_leaves_nothing_half_written()
    {
        using var twins = new ReplayGainTests.Twins(@"split\name_clash");
        var flac = new Flac(FlacExe);
        var cue = Path.Combine(twins.Cs, "image.cue");
        var before = Directory.GetFiles(twins.Cs).Order().ToList();
        using var stopped = new CancellationTokenSource();
        stopped.Cancel();
        var cut = Split.Cut(SplitPlanner.Plan(cue, Names.Masks, Names.Replacement), flac, stopped.Token);
        Assert.True(cut.Cancelled);
        Assert.Equal(before, Directory.GetFiles(twins.Cs).Order());

        var plan = SplitPlanner.Plan(cue, Names.Masks, Names.Replacement);
        var short_ = Split.Cut(plan, flac, TestContext.Current.CancellationToken, freeSpace: _ => 10);
        Assert.StartsWith("Not enough free space: the tracks need about ", short_.Error);
        Assert.Equal(before, Directory.GetFiles(twins.Cs).Order());
    }

    [Fact]
    public void The_recap_says_what_the_split_writes_or_why_it_cant()
    {
        var silent = Split.IsSilent(new Flac(FlacExe), TestContext.Current.CancellationToken);
        var hidden = SplitPlanner.Plan(Path.Combine(Setup.Fixtures, "split", "hidden_track", "Split Album.cue"), Names.Masks, Names.Replacement, silent);
        Assert.Empty(SplitRecap.Reasons(hidden, masksValid: true, "the Options"));
        Assert.Equal("Split writes 4 tracks as FLAC files, each proven identical to its part of the original. The image and its cue sheet stay as they are.",
            SplitRecap.Message(hidden, []));
        Assert.Equal(("hidden", "00 Hidden track.flac", "0:03"), SplitRecap.Rows(hidden)[0]);
        Assert.Equal("Source: Fixture Artist - Split Album.flac: FLAC, 44.1 kHz, 0:12", SplitRecap.Details(hidden)[0]);
        Assert.Equal("Cue sheet read as UTF-8", SplitRecap.Details(hidden)[2]);

        var data = SplitPlanner.Plan(Path.Combine(Setup.Fixtures, "split", "data_track", "image.cue"), Names.Masks, Names.Replacement, silent);
        var reasons = SplitRecap.Reasons(data, masksValid: false, "the Options");
        Assert.Equal(["The File names masks need fixing: the Options.", "1 data track skipped."], reasons);
        Assert.StartsWith("This cue sheet can't be split as it is: The File names masks need fixing", SplitRecap.Message(data, reasons));
        Assert.EndsWith(SplitRecap.FixIt, SplitRecap.Message(data, reasons));
    }

    [Fact]
    public void The_split_task_reports_what_it_wrote()
    {
        using var twins = new ReplayGainTests.Twins(@"split\hidden_track");
        var flac = new Flac(FlacExe);
        var cancel = TestContext.Current.CancellationToken;
        var plan = SplitPlanner.Plan(Path.Combine(twins.Cs, "Split Album.cue"), Names.Masks, Names.Replacement, Split.IsSilent(flac, cancel));
        Assert.Equal([twins.Cs], SplitTask.LockPaths(plan));
        var progress = new List<string>();
        var outcome = SplitTask.Run(plan, flac, cancel, (done, total, name) => progress.Add($"{done}/{total} {name}"));

        Assert.True(outcome.Split);
        Assert.Equal(5, progress.Count);
        Assert.StartsWith("0/4 00 Hidden track.flac", progress[0]);
        Assert.Equal("4/4 done", progress[4]);
        var text = outcome.Text();
        Assert.StartsWith("Done: 4 tracks split, all identical to the original.\n\nWritten to " + twins.Cs + ":\n    00 Hidden track.flac", text);
        Assert.Contains("Together, the tracks have the original's MD5 (", text);
        Assert.EndsWith("The image and its cue sheet are as they were.", text);
    }
}
