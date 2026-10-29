using System.Text.Json;
using Refrain.Core.Options;
using Refrain.Core.ReplayGain;
using Refrain.Core.Tidy;
using Xunit;

namespace Refrain.Tests;

/// <summary>Tidy's engine without the split (SPEC §28, §36 "8." step d), held to the Python app's own code: its
/// planner and its job's album step run on one copy of a composed tree, the port on the other, both with the real
/// rsgain; then the plans, the reports, every tag and every log must match.</summary>
public sealed class TidyTests
{
    static readonly FileNamesOptions Names = FileNamesOptions.Default;
    static readonly ReplayGainOptions Gain = ReplayGainOptions.Default;
    static readonly TidyOptions Tidy = TidyOptions.Default;

    /// <summary>The tree: the album fixture, Album B (with its Scans folder), .lrc sidecars, a multi-file cue, a
    /// half-split album (its image, its sheet and a track split from it), an empty folder and a folder of artwork.</summary>
    static void Compose(string root)
    {
        var fixtures = Setup.Fixtures;
        Setup.Copy(Path.Combine(fixtures, "replaygain", "Fixture Artist", "Album B"), Path.Combine(root, "Album B"));
        Setup.Copy(Path.Combine(fixtures, "cleanup", "lrc"), Path.Combine(root, "Lyrics"));
        Setup.Copy(Path.Combine(fixtures, "cleanup", "per_track_cue"), Path.Combine(root, "Per track cue"));
        var half = Path.Combine(root, "Half split");
        Setup.Copy(Path.Combine(fixtures, "cleanup", "single_file_album"), half);
        File.Copy(Path.Combine(fixtures, "album", "Fixture Artist - Fixture Album", "01 First.flac"), Path.Combine(half, "01 First.flac"));
        Directory.CreateDirectory(Path.Combine(root, "Empty", "Nothing"));
        Directory.CreateDirectory(Path.Combine(root, "Artwork"));
        File.Copy(Path.Combine(fixtures, "cover.jpg"), Path.Combine(root, "Artwork", "front.jpg"));
    }

    /// <summary>The plan as lines, the same shape on both sides; paths relative to the root.</summary>
    const string PythonPlanLines = """
        def rel(p):
            p = Path(p)
            return "." if p == root else str(p.relative_to(root))
        def name(p):
            return "None" if p is None else Path(p).name
        def plan_lines(plan):
            lines = []
            for a in plan.albums:
                lines.append(f"A {rel(a.folder)} | rg={a.replaygain} | tidy={a.tidy} | exclude={','.join(sorted(a.replaygain_exclude))}")
                for f in a.folders:
                    lines.append(f"  F {rel(f.folder)} | {f.kind} | skip={f.skip} | image={f.image}")
                    lines += [f"    note {n}" for n in f.notes]
                    lines += [f"    offer {rel(o)}" for o in f.offer]
                    for i in (f.renames.items if f.renames else []):
                        detail = "" if str(i.status) == "unreadable" else i.detail
                        lines.append(f"    R {name(i.source)} | {i.status} | {name(i.target)} | {detail} | fallback={i.fallback_missing}"
                                     f" | lrc={name(i.sidecar.target) if i.sidecar else None}")
                lines += [f"  C {rel(c.path)} | {c.why} | {c.name} | {c.is_folder}" for c in a.clutter]
            lines += [f"L {rel(c.path)} | {c.why} | {c.name} | {c.is_folder}" for c in plan.loose]
            lines += [f"S {plan.summary()}", f"W {plan.has_work}"]
            return lines
        """;

    static string Rel(string root, string path) => path == root ? "." : Path.GetRelativePath(root, path);
    static string Name(string? path) => path is null ? "None" : Path.GetFileName(path);
    static string Py(object? value) => value switch { null => "None", bool b => b ? "True" : "False", _ => value.ToString()! };

    static string KindText(FolderKind kind) => kind switch
    {
        FolderKind.NotSplit => "not split",
        FolderKind.HalfSplit => "half-split",
        _ => kind.ToString().ToLowerInvariant(),
    };

    static List<string> PlanLines(TidyPlan plan, string root)
    {
        var lines = new List<string>();
        foreach (var a in plan.Albums)
        {
            lines.Add($"A {Rel(root, a.Folder)} | rg={Py(a.ReplayGain)} | tidy={Py(a.IsTidy)} | exclude={string.Join(",", a.ReplayGainExclude.Order(StringComparer.Ordinal))}");
            foreach (var f in a.Folders)
            {
                lines.Add($"  F {Rel(root, f.Folder)} | {KindText(f.Kind)} | skip={Py(f.Skip)} | image={Py(f.Image)}");
                lines.AddRange(f.Notes.Select(n => $"    note {n}"));
                lines.AddRange(f.Offer.Select(o => $"    offer {Rel(root, o)}"));
                foreach (var i in f.Renames?.Items ?? [])
                    lines.Add($"    R {Name(i.Source)} | {RenameItem.Label(i.Status)} | {Name(i.Target)} | " +
                              $"{(i.Status == RenameStatus.Unreadable ? "" : i.Detail)} | fallback={Py(i.FallbackMissing)} | lrc={(i.Sidecar is { } s ? Name(s.Target) : "None")}");
            }
            lines.AddRange(a.Clutter.Select(c => $"  C {Rel(root, c.Path)} | {c.Why} | {c.Name} | {Py(c.IsFolder)}"));
        }
        lines.AddRange(plan.Loose.Select(c => $"L {Rel(root, c.Path)} | {c.Why} | {c.Name} | {Py(c.IsFolder)}"));
        lines.Add($"S {plan.Summary()}");
        lines.Add($"W {Py(plan.HasWork)}");
        return lines;
    }

    /// <summary>The Python app plans Tidy on <paramref name="root"/>; with <paramref name="run"/>, its job's album step
    /// then runs every album that isn't tidy. Returns the plan's lines, the report and the offered items.</summary>
    static (List<string> Plan, string Report, List<string> Offers) RunPython(string root, bool run)
    {
        var output = PythonApp.Run(PythonPlanLines + """

            import json, sys
            from pathlib import Path
            from types import SimpleNamespace
            from refrain.core import cleanup, rename, rsgain, tidy
            from refrain.core.cancel import CancelToken
            from refrain.core.replaygain import DEFAULT_OPTIONS
            from refrain.core.masks import DEFAULT_MASKS, DEFAULT_REPLACEMENT
            from refrain.services.tidy_job import TidyFinished, TidyRunner, report_text
            args = json.load(sys.stdin)
            root = Path(args["root"])
            plan = tidy.plan_tidy(root, rename.RenameOptions(), DEFAULT_OPTIONS, DEFAULT_MASKS, DEFAULT_REPLACEMENT)
            out = {"plan": plan_lines(plan), "report": "", "offers": []}
            if args["run"]:
                class Lines:
                    def line(self, text): pass
                    def info(self, text): pass
                    def warn(self, text): pass
                    def error(self, text): pass
                    def progress(self, done, total, label): pass
                job = SimpleNamespace(scanner=rsgain.scan, exe=Path(args["exe"]))
                result = TidyFinished(0, root)
                for album in [a for a in plan.albums if not a.tidy]:
                    result.albums.append(TidyRunner._album(job, album, DEFAULT_OPTIONS, CancelToken(), Lines(), result))
                result.offers += [item for item in plan.clutter if cleanup.recheck(item) is None]
                out["report"] = report_text(result)
                out["offers"] = [f"{rel(i.path)} | {i.why}" for i in result.offers]
            print(json.dumps(out))
            """, JsonSerializer.Serialize(new { root, run, exe = ReplayGainTests.Rsgain }));
        using var json = JsonDocument.Parse(output);
        List<string> Strings(string key) => json.RootElement.GetProperty(key).EnumerateArray().Select(e => e.GetString()!).ToList();
        return (Strings("plan"), json.RootElement.GetProperty("report").GetString()!, Strings("offers"));
    }

    [Fact]
    public void The_plan_is_the_python_apps()
    {
        using var twins = new ReplayGainTests.Twins("album", Compose);
        var (expected, _, _) = RunPython(twins.Py, run: false);
        var plan = Tidying.Plan(twins.Cs, Names, Gain, Tidy, TestContext.Current.CancellationToken);
        Assert.Equal(expected, PlanLines(plan, twins.Cs));
        // The tree holds the hard cases, so the comparison means something.
        foreach (var part in new[] { "| name taken |", "| missing tags |", "| rename |", "| kept |", "fallback=track", "lrc=01 With Lyrics.lrc",
                     "rg=missing tags", "| half-split |", "(empty folder)", "picture beside music" })
            Assert.True(expected.Any(line => line.Contains(part)), part + Environment.NewLine + string.Join(Environment.NewLine, expected));
        Assert.Contains(plan.AllFolders, f => f.Kind == FolderKind.HalfSplit);
        Assert.Contains(plan.AllFolders, f => f.RenameCount > 0);
        Assert.Contains(plan.Loose, c => c.IsFolder);
    }

    [Fact]
    public void A_run_renames_and_replaygains_as_the_python_app_does()
    {
        using var twins = new ReplayGainTests.Twins("album", Compose);
        var (_, pyReport, pyOffers) = RunPython(twins.Py, run: true);
        var plan = Tidying.Plan(twins.Cs, Names, Gain, Tidy, TestContext.Current.CancellationToken);
        var outcome = TidyTask.Run(plan, Gain, TestContext.Current.CancellationToken, exe: ReplayGainTests.Rsgain);

        Assert.Equal(pyReport.Replace(twins.Py, "<ROOT>"), outcome.Text().Replace(twins.Cs, "<ROOT>"));
        Assert.Equal(pyOffers, outcome.Offers.Select(i => $"{Rel(twins.Cs, i.Path)} | {i.Why}"));
        ReplayGainTests.AssertSame(twins);

        // Run again: everything is tidy now but what is offered (Tidy never deletes).
        var again = Tidying.Plan(twins.Cs, Names, Gain, Tidy, TestContext.Current.CancellationToken);
        Assert.All(again.Albums.Where(a => a.Clutter.Count == 0 && a.Own.Offer.Count == 0),
            a => Assert.True(a.IsTidy || a.Folders.Any(f => f.Notes.Count > 0 || f.Kind == FolderKind.Skipped), a.Folder));
    }

    [Fact]
    public void An_unsplit_album_is_skipped_and_left_untouched_until_the_split_comes()
    {
        using var temp = new TempFolder();
        var album = Path.Combine(temp.Path, "Single file");
        Setup.Copy(Path.Combine(Setup.Fixtures, "cleanup", "single_file_album"), album);
        var before = Directory.GetFiles(album).Select(f => (f, File.GetLastWriteTimeUtc(f))).ToList();

        var plan = Tidying.Plan(temp.Path, Names, Gain, Tidy, TestContext.Current.CancellationToken);
        var own = Assert.Single(plan.Albums).Own;
        Assert.Equal(FolderKind.Skipped, own.Kind);
        Assert.Equal("Fixture Album.cue plays Fixture Album.flac, not split yet: split it from its .cue", own.Skip);
        Assert.Null(plan.Albums[0].ReplayGain);  // the image is never measured
        var outcome = TidyTask.Run(plan, Gain, TestContext.Current.CancellationToken, exe: ReplayGainTests.Rsgain);

        Assert.Contains("    skipped: Fixture Album.cue plays Fixture Album.flac, not split yet: split it from its .cue", outcome.Text());
        Assert.Equal(before, Directory.GetFiles(album).Select(f => (f, File.GetLastWriteTimeUtc(f))));
        Assert.Contains(outcome.Offers, i => i.Why == Clutter.PictureBesideMusic);  // folder.jpg beside the image
    }

    [Fact]
    public void Clutter_is_found_rechecked_and_the_root_never_offered()
    {
        using var temp = new TempFolder();
        var lists = ClutterLists.Of(Tidy);
        Directory.CreateDirectory(Path.Combine(temp.Path, "Scans", "Inner"));
        File.WriteAllText(Path.Combine(temp.Path, "Scans", "back.jpg"), "");
        File.WriteAllText(Path.Combine(temp.Path, "Scans", "Thumbs.db"), "");
        Directory.CreateDirectory(Path.Combine(temp.Path, "Notes"));
        File.WriteAllText(Path.Combine(temp.Path, "Notes", "a.jpg"), "");
        File.WriteAllText(Path.Combine(temp.Path, "Notes", "read me.txt"), "");
        File.WriteAllText(Path.Combine(temp.Path, "cover.png"), "");

        var items = Clutter.Find(temp.Path, lists, TestContext.Current.CancellationToken);
        var scans = Assert.Single(items);  // the root has no audio: its cover isn't beside music; Notes holds a .txt
        Assert.Equal(("Scans (folder, 1 picture)", "folder of 1 picture"), (scans.Name, scans.Why));
        Assert.Null(Clutter.Recheck(scans));
        File.WriteAllText(Path.Combine(temp.Path, "Scans", "Inner", "x.nfo"), "");
        Assert.Equal("it now holds other files", Clutter.Recheck(scans));
        Assert.Empty(Clutter.Find(Path.Combine(temp.Path, "Scans", "Inner"), lists, TestContext.Current.CancellationToken));  // root itself: never
    }
}
