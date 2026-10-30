using System.Text.Json;
using Refrain.Core;
using Refrain.Core.Cue;
using Xunit;

namespace Refrain.Tests;

/// <summary>The editable cue sheet (SPEC §22 "Saving the sheet", §36 "9." step c), held to the Python app's own
/// <c>core/cue_edit.py</c>: every fixture sheet read by both (values, other lines, checks, the bytes written back),
/// then the same edits made on both sides and the bytes each would save compared.</summary>
public sealed class CueEditTests
{
    static IEnumerable<string> Sheets() =>
        Directory.GetFiles(Path.Combine(Setup.Fixtures, "split"), "*.cue", SearchOption.AllDirectories)
            .Append(Path.Combine(Setup.Fixtures, "cleanup", "single_file_album", "Fixture Album.cue"))
            .Append(Path.Combine(Setup.Fixtures, "cleanup", "per_track_cue", "album.cue"))
            .Order(StringComparer.Ordinal);

    // The edits, by name, the same on both sides.
    static readonly string[] Steps =
    [
        "album title|New Title", "album date|1999", "album catalog|0123", "album genre|", "track 0 title|Renamed One",
        "track 1 performer|Guest", "track 1 isrc|GBAYE0000001", "track 0 flags|DCP", "add 0", "track 1 gap|00:00:30",
        "album other|REM COMMENT \"edited\"", "track 0 other|REM X 1", "delete 0", "file other.flac", "fill 0:05,0:02:30",
    ];

    const string PythonDump = """
        import base64, hashlib, json, sys
        from refrain.core import cue_edit
        from refrain.core.cue import CueError
        def dump(doc):
            album = doc.album()
            lines = [f"encoding={doc.encoding} {doc.fell_back} {doc.not_cp1252} file={doc.file} files={doc.file_count}"]
            lines += [f"album {k}={album[k]}" for k in sorted(album)]
            for v in doc.tracks():
                lines.append(f"track {v.row} {v.number} " + " ".join(f"{k}={v.values[k]}" for k in sorted(v.values)) + f" other={v.other}")
            lines += [f"problem {p.row} {p.column} {p.message} {p.blocks_save}" for p in cue_edit.validate(doc)]
            try:
                lines.append("bytes=" + base64.b64encode(cue_edit.format_cue(doc)).decode())
            except cue_edit.CueEncodingError as e:
                lines.append(f"encoding error {e}")
            lines.append(f"modified={doc.modified}")
            return lines
        def apply(doc, step):
            kind, _, rest = step.partition(" ")
            words = rest.split("|", 1)
            if kind == "album":
                name, value = words[0], words[1]
                if name == "other":
                    doc.set_album_other(value)
                else:
                    doc.set_album(name, value)
            elif kind == "track":
                row, name = words[0].split(" ")
                if name == "other":
                    doc.set_track_other(int(row), words[1])
                else:
                    doc.set_track(int(row), name, words[1])
            elif kind == "add":
                doc.add_track(int(rest), 100000)
            elif kind == "delete":
                doc.delete_tracks([int(rest)])
            elif kind == "file":
                doc.set_file(rest)
            elif kind == "fill":
                doc.fill_from_lengths([cue_edit.parse_length(t) for t in rest.split(",")])
        """;

    static List<string> Dump(CueDocument doc)
    {
        var album = doc.Album();
        var lines = new List<string> { $"encoding={doc.EncodingName} {Py(doc.FellBack)} {Py(doc.NotCp1252)} file={doc.File} files={doc.FileCount}" };
        lines.AddRange(album.Keys.Order(StringComparer.Ordinal).Select(k => $"album {k}={album[k]}"));
        foreach (var v in doc.Tracks())
            lines.Add($"track {v.Row} {(v.Number is { } n ? n.ToString() : "None")} " +
                      string.Join(" ", v.Values.Keys.Order(StringComparer.Ordinal).Select(k => $"{k}={v.Values[k]}")) + $" other={v.Other}");
        lines.AddRange(doc.Validate().Select(p => $"problem {(p.Row is { } r ? r.ToString() : "None")} {p.Column} {p.Message} {Py(p.BlocksSave)}"));
        try
        {
            lines.Add("bytes=" + Convert.ToBase64String(doc.Format()));
        }
        catch (CueEncodingException e)
        {
            lines.Add($"encoding error {e.Message}");
        }
        lines.Add($"modified={Py(doc.Modified)}");
        return lines;
    }

    static string Py(bool value) => value ? "True" : "False";

    static void Apply(CueDocument doc, string step)
    {
        var space = step.IndexOf(' ');
        var (kind, rest) = (step[..space], step[(space + 1)..]);
        var words = rest.Split('|', 2);
        switch (kind)
        {
            case "album" when words[0] == "other": doc.SetAlbumOther(words[1]); break;
            case "album": doc.SetAlbum(words[0], words[1]); break;
            case "track":
                var (row, name) = (int.Parse(words[0].Split(' ')[0]), words[0].Split(' ')[1]);
                if (name == "other") doc.SetTrackOther(row, words[1]);
                else doc.SetTrack(row, name, words[1]);
                break;
            case "add": doc.AddTrack(int.Parse(rest), 100000); break;
            case "delete": doc.DeleteTracks([int.Parse(rest)]); break;
            case "file": doc.SetFile(rest); break;
            case "fill": doc.FillFromLengths(rest.Split(',').Select(t => CueDocument.ParseLength(t)!.Value).ToList()); break;
        }
    }

    [Fact]
    public void Every_fixture_sheet_reads_and_writes_back_as_the_python_app_does()
    {
        var sheets = Sheets().ToList();
        var output = PythonApp.Run(PythonDump + """

            args = json.load(sys.stdin)
            out = []
            for path in args["sheets"]:
                doc = cue_edit.read_document(path)
                out.append(dump(doc))
            print(json.dumps(out, ensure_ascii=False))
            """, JsonSerializer.Serialize(new { sheets }));
        var expected = JsonSerializer.Deserialize<List<List<string>>>(output)!;
        for (var i = 0; i < sheets.Count; i++)
        {
            var doc = CueDocument.Read(sheets[i]);
            Assert.Equal(expected[i], Dump(doc));
            Assert.Equal(File.ReadAllBytes(sheets[i]), doc.Format());  // unedited: byte-identical
        }
    }

    [Fact]
    public void Edits_write_the_same_bytes_as_the_python_app()
    {
        var sheets = Sheets().ToList();
        var output = PythonApp.Run(PythonDump + """

            args = json.load(sys.stdin)
            out = []
            for path in args["sheets"]:
                doc = cue_edit.read_document(path)
                result = []
                for step in args["steps"]:
                    try:
                        apply(doc, step)
                        result.append(step)
                    except (CueError, IndexError) as e:
                        result.append(f"{step}: refused")
                    result += dump(doc)
                out.append(result)
            print(json.dumps(out, ensure_ascii=False))
            """, JsonSerializer.Serialize(new { sheets, steps = Steps }));
        var expected = JsonSerializer.Deserialize<List<List<string>>>(output)!;
        for (var i = 0; i < sheets.Count; i++)
        {
            var doc = CueDocument.Read(sheets[i]);
            var actual = new List<string>();
            foreach (var step in Steps)
            {
                try
                {
                    Apply(doc, step);
                    actual.Add(step);
                }
                catch (Exception e) when (e is CueException or ArgumentOutOfRangeException or IndexOutOfRangeException)
                {
                    actual.Add($"{step}: refused");
                }
                actual.AddRange(Dump(doc));
            }
            Assert.Equal(expected[i], actual);
        }
    }

    [Fact]
    public void A_new_sheet_and_the_fill_helpers_are_the_python_apps()
    {
        var output = PythonApp.Run("""
            import base64, json
            from refrain.core import cue_edit
            doc = cue_edit.new_document("Album \"Live\".flac", "Björk", 'Début "Live"')
            out = [base64.b64encode(cue_edit.format_cue(doc)).decode(), str(doc.modified)]
            out += [str(cue_edit.parse_length(t)) for t in ["3:05", "3:05:20", "61:00", "3:60", "x", "0:00:75"]]
            out += [cue_edit.format_length(n) for n in [13875, 13895, 0]]
            for texts, first, audio in [(["3:00", "2:00"], 0, 30000), (["3:00", "x"], 0, 30000), (["3:00", "0:00"], 0, 30000), (["9:00"], 75, 30000)]:
                lengths, why = cue_edit.check_lengths(texts, first, audio)
                out.append(f"{lengths} {why}")
            print(json.dumps(out, ensure_ascii=False))
            """);
        var expected = JsonSerializer.Deserialize<List<string>>(output)!;
        var doc = CueDocument.New("Album \"Live\".flac", "Björk", "Début \"Live\"");
        List<string> actual = [Convert.ToBase64String(doc.Format()), Py(doc.Modified)];
        actual.AddRange(new[] { "3:05", "3:05:20", "61:00", "3:60", "x", "0:00:75" }.Select(t => CueDocument.ParseLength(t)?.ToString() ?? "None"));
        actual.AddRange(new long[] { 13875, 13895, 0 }.Select(CueDocument.FormatLength));
        foreach (var (texts, first, audio) in new (string[], long, long)[] { (["3:00", "2:00"], 0, 30000), (["3:00", "x"], 0, 30000), (["3:00", "0:00"], 0, 30000), (["9:00"], 75, 30000) })
        {
            var (lengths, why) = CueDocument.CheckLengths(texts, first, audio);
            actual.Add($"{(lengths is null ? "None" : "[" + string.Join(", ", lengths) + "]")} {why}");
        }
        Assert.Equal(expected, actual);
    }

    [Fact]
    public void Save_recycles_the_old_version_and_asks_for_utf8_when_a_character_doesnt_fit()
    {
        using var temp = new TempFolder();
        var path = Path.Combine(temp.Path, "cp1252.cue");
        File.Copy(Path.Combine(Setup.Fixtures, "split", "sheets", "cp1252.cue"), path);
        var doc = CueDocument.Read(path);
        doc.SetAlbum("title", "東京");
        var refused = Assert.Throws<CueEncodingException>(() => doc.Save(path, _ => throw new InvalidOperationException(), _ => null));
        Assert.Equal(["京", "東"], refused.Characters.Order(StringComparer.Ordinal));
        doc.UseUtf8Bom();
        var recycled = new List<string>();
        doc.Save(path, recycled.Add, _ => null);
        Assert.Equal([path], recycled);
        Assert.StartsWith("﻿PERFORMER \"Björk\"", File.ReadAllText(path));
        Assert.Contains("TITLE \"東京\"", File.ReadAllText(path));
        Assert.False(doc.Modified);
        Assert.Throws<CannotRecycleException>(() => doc.Save(path, recycled.Add, _ => "no Recycle Bin"));
        Assert.Equal(Path.Combine(temp.Path, "cp1252 (2).cue"), CueDocument.NumberedPath(path));
    }

    [Fact]
    public void Preview_in_player_writes_the_python_apps_sheet()
    {
        var cues = new[] { Path.Combine(Setup.Fixtures, "split", "hidden_track", "Split Album.cue"), Path.Combine(Setup.Fixtures, "split", "wav_image", "image.cue") };
        var output = PythonApp.Run("""
            import json, sys
            from pathlib import Path
            from refrain.core import cue, playlists, split
            from refrain.core.flac import FlacRunner
            from refrain.core.masks import DEFAULT_MASKS, DEFAULT_REPLACEMENT
            args = json.load(sys.stdin)
            runner = FlacRunner(Path(args["flac"]))
            out = [playlists.render_preview(split.preview_sheet(cue.plan_album(c, DEFAULT_MASKS, DEFAULT_REPLACEMENT, is_silent=split.is_silent(runner))))
                   for c in args["cues"]]
            print(json.dumps(out, ensure_ascii=False))
            """, JsonSerializer.Serialize(new { cues, flac = SplitTests.FlacExe }));
        var expected = JsonSerializer.Deserialize<List<string>>(output)!;
        var flac = new Flac(SplitTests.FlacExe);
        var names = Refrain.Core.Options.FileNamesOptions.Default;
        Assert.Equal(expected, cues.Select(c => CueEditing.PreviewText(SplitPlanner.Plan(c, names.Masks, names.Replacement,
            Split.IsSilent(flac, TestContext.Current.CancellationToken)))));
        Assert.Contains("TITLE \"Hidden track\"", expected[0]);
    }

    [Fact]
    public void The_editor_marks_a_source_it_cant_use_and_starts_fill_from_the_starts()
    {
        var doc = CueDocument.Read(Path.Combine(Setup.Fixtures, "split", "sheets", "bom.cue"));
        Assert.Equal("not found", CueEditing.SourceMark(doc.File, ["a.flac"]));
        Assert.Equal("This sheet points to x.flac, which isn't in this folder. Choose its audio file.", CueEditing.SourceWarning(doc, ["a.flac"], null));
        doc.SetFile("x.ape");
        Assert.Equal("not supported", CueEditing.SourceMark(doc.File, ["x.ape"]));
        Assert.StartsWith("This sheet points to x.ape, which the split can't read", CueEditing.SourceWarning(doc, [], null));
        Assert.Null(CueEditing.SourceWarning(CueDocument.Read(Path.Combine(Setup.Fixtures, "split", "name_clash", "image.cue")), ["image.flac"], null));
        Assert.Equal(["02 Two.flac", "image.flac"], CueEditing.AudioFiles(Path.Combine(Setup.Fixtures, "split", "name_clash")));

        var (first, lengths) = CueEditing.FillStart(doc)!.Value;
        Assert.Equal(0, first);
        Assert.Equal([75L], lengths);
        doc.SetTrack(0, "start", "bad");
        Assert.Null(CueEditing.FillStart(doc));
    }
}
