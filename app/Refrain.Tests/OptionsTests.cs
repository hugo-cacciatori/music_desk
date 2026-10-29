using System.Text.Json;
using Refrain.Core;
using Refrain.Core.Options;
using Xunit;

namespace Refrain.Tests;

/// <summary>The masks and the Options pages' settings (SPEC §12, §21, §36 "8."), the masks held to the Python app's
/// own <c>core/masks.py</c>.</summary>
public sealed class OptionsTests
{
    static readonly string[] SampleMasks =
    [
        "{track:02} {title}", "{title}", "{disc}-{track:03} {artist} - {title}", "{track} {title:.3}", "{track:>4}",
        "{title:*^9}", "{albumartist} [{year}] {album}", "{track:+d}", "{{x}} {title}", "{track:x}", "{title:d}",
        "{track", "{nope} {title}", "plain", "{title}?", "{track:.2}", "{track:02}{track_total:02} {composer}",
    ];

    static readonly Dictionary<string, object>[] SampleFields =
    [
        new() { ["track"] = 3L, ["title"] = "Airbag", ["artist"] = "Radiohead" },
        new() { ["track"] = 12L, ["disc"] = 2L, ["title"] = "A/B: \"C\"?", ["artist"] = "Sigur Rós", ["albumartist"] = "Various", ["year"] = "1997", ["album"] = "Ágætis byrjun" },
        new() { ["title"] = "Only a title" },
        new() { ["track"] = 7L, ["track_total"] = 9L, ["composer"] = "Bach", ["title"] = "Prélude|1" },
    ];

    [Fact]
    public void Masks_name_files_and_refuse_masks_as_the_python_app_does()
    {
        var input = JsonSerializer.Serialize(new { masks = SampleMasks, fields = SampleFields.Select(f => f.ToDictionary(kv => kv.Key, kv => kv.Value is long n ? (object)(int)n : kv.Value)).ToArray() });
        var output = PythonApp.Run("""
            import json, sys
            from refrain.core import masks as m
            data = json.load(sys.stdin)
            result = {"valid": [not m.validate_masks([mask], "-") for mask in data["masks"]], "names": []}
            for fields in data["fields"]:
                row = []
                for mask in data["masks"]:
                    if m.validate_masks([mask], "-"):
                        row.append(None)
                        continue
                    row.append(m.name_for(fields, [mask], "-", ".FLAC"))
                result["names"].append(row)
            print(json.dumps(result))
            """, input);
        var python = JsonDocument.Parse(output).RootElement;
        for (var i = 0; i < SampleMasks.Length; i++)
            Assert.True(python.GetProperty("valid")[i].GetBoolean() == (Masks.Validate([SampleMasks[i]], "-").Count == 0),
                $"mask {SampleMasks[i]}: Python valid={python.GetProperty("valid")[i]}, C# problems={string.Join("; ", Masks.Validate([SampleMasks[i]], "-"))}");
        for (var f = 0; f < SampleFields.Length; f++)
            for (var i = 0; i < SampleMasks.Length; i++)
            {
                var expected = python.GetProperty("names")[f][i];
                if (expected.ValueKind == JsonValueKind.Null && Masks.Validate([SampleMasks[i]], "-").Count > 0) continue;
                var actual = Masks.NameFor(SampleFields[f], [SampleMasks[i]], "-", ".FLAC")?.Name;
                Assert.True(expected.GetString() == actual, $"mask {SampleMasks[i]}, fields {f}: Python {expected}, C# {actual}");
            }
    }

    [Fact]
    public void The_target_reads_as_python_writes_it()
    {
        Assert.Equal("-15", Python.G(-15));
        Assert.Equal("-18.5", Python.G(-18.5));
        Assert.Equal("1e-05", Python.G(0.00001));
        Assert.Equal("0.99999", Python.G(0.99999));
        Assert.Equal("LUFS Target : -18.5", (ReplayGainOptions.Default with { Target = -18.5 }).LufsComment);
    }

    [Fact]
    public void The_pages_check_their_values()
    {
        Assert.Empty(FileNamesOptions.Default.Validate());
        Assert.Empty(ReplayGainOptions.Default.Validate());
        Assert.Empty(TidyOptions.Default.Validate());
        Assert.Contains(new FileNamesOptions([], "-").Validate(), p => p == "No rename mask");
        Assert.Contains(new FileNamesOptions(["{title}"], "?").Validate(), p => p.Contains("replacement"));
        var bad = ReplayGainOptions.Default with
        {
            Target = -40, SafetyThreshold = 0, SafetyMargin = 7, SubfolderKeywords = ["Lite", " "], LogFile = "a?.log",
            Comment = "{oops}",
        };
        Assert.Equal(7, bad.Validate().Count);
        Assert.Equal(3, (TidyOptions.Default with { MusicTypes = [".FLAC", ".txt"], Pictures = [".flac"] }).Validate().Count);
    }

    [Fact]
    public void Saved_options_survive_and_a_mistyped_value_falls_back_alone()
    {
        using var temp = new TempFolder();
        var path = Path.Combine(temp.Path, "settings.json");
        var settings = new Settings();
        settings.Set(new FileNamesOptions(["{title}"], "_"));
        settings.Set(ReplayGainOptions.Default with { Target = -18 });
        settings.Set(TidyOptions.Default with { Videos = [".mkv"] });
        settings.Save(path);
        var back = Settings.Load(path);
        Assert.Equal(["{title}"], back.GetFileNames().Masks);
        Assert.Equal("_", back.GetFileNames().Replacement);
        Assert.Equal(-18, back.GetReplayGain().Target);
        Assert.Equal([".mkv"], back.GetTidy().Videos);
        var text = File.ReadAllText(path).Replace("-18", "\"loud\"");
        File.WriteAllText(path, text);
        var mistyped = Settings.Load(path).GetReplayGain();
        Assert.Equal(-15, mistyped.Target);  // the default
        Assert.Equal(ReplayGainOptions.Default.LogFile, mistyped.LogFile);
        Assert.Equal(["{title}"], Settings.Load(path).GetFileNames().Masks);  // the other pages keep theirs
    }
}
