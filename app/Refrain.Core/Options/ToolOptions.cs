using System.Globalization;
using System.Text.Json;

namespace Refrain.Core.Options;

/// <summary>The File names page (SPEC §21): the masks, in order, and the replacement for refused characters.</summary>
public sealed record FileNamesOptions(IReadOnlyList<string> Masks, string Replacement)
{
    public static readonly FileNamesOptions Default = new(Options.Masks.DefaultMasks, Options.Masks.DefaultReplacement);

    public List<string> Validate() => Options.Masks.Validate(Masks, Replacement);

    public static FileNamesOptions From(JsonElement? saved) => new(
        Saved.Strings(saved, "masks") ?? Default.Masks,
        Saved.Text(saved, "replacement") ?? Default.Replacement);

    public void Write(Utf8JsonWriter json)
    {
        json.WriteStartObject();
        Saved.Write(json, "masks", Masks);
        json.WriteString("replacement", Replacement);
        json.WriteEndObject();
    }
}

/// <summary>The ReplayGain page (SPEC §10 B): the script's configuration block, with its defaults. Ported from the
/// Python app's <c>core/replaygain/options.py</c>.</summary>
public sealed record ReplayGainOptions(
    double Target, double SafetyThreshold, double SafetyMargin, IReadOnlyList<string> SubfolderKeywords,
    IReadOnlyList<string> TrackDataKeywords, IReadOnlyList<string> ExcludedKeywords, string LogFile, string Comment)
{
    public static readonly ReplayGainOptions Default = new(-15, 0.99999, 0.01, ["lite", "bonus", "cd2", "disc 2", "cd 2"],
        ["lite"], ["bonus"], "replaygain.log", "LUFS Target : {target}");

    public const double LowestTarget = -30, HighestTarget = -5;  // rsgain's -l range

    /// <summary>The target as the script wrote it: "-15", not "-15.0" (Python's <c>{:g}</c>).</summary>
    public string TargetText => Python.G(Target);

    /// <summary>The comment line the ReplayGain step writes ("LUFS Target : -15").</summary>
    public string LufsComment => Masks.Apply(Comment, new Dictionary<string, object> { ["target"] = TargetText });

    /// <summary>The problems in the options (none when they are fine): they keep a run from failing halfway.</summary>
    public List<string> Validate()
    {
        var problems = new List<string>();
        if (Target is < LowestTarget or > HighestTarget || double.IsNaN(Target))
            problems.Add($"The target loudness must be between {Python.G(LowestTarget)} and {Python.G(HighestTarget)} LUFS (rsgain's range)");
        if (SafetyThreshold is <= 0 or > 1 || double.IsNaN(SafetyThreshold))
            problems.Add("The safety threshold must be above 0 and at most 1 (a peak of 1.0 is full scale)");
        if (SafetyMargin is < 0 or > 6 || double.IsNaN(SafetyMargin))
            problems.Add("The safety margin must be between 0 and 6 dB");
        foreach (var (label, keywords) in new[]
                 {
                     ("Album-data subfolder keywords", SubfolderKeywords), ("Track-data keywords", TrackDataKeywords),
                     ("Excluded keywords", ExcludedKeywords),
                 })
            foreach (var keyword in keywords)
            {
                if (keyword.Trim().Length == 0) problems.Add($"{label}: a keyword is empty");
                else if (keyword != keyword.ToLowerInvariant())
                    problems.Add($"{label}: '{keyword}' must be lowercase (folder names are compared in lowercase)");
            }
        if (LogFile.Trim().Length == 0 || LogFile is "." or ".." || Masks.Forbidden().IsMatch(LogFile) || LogFile.EndsWith(' ') || LogFile.EndsWith('.'))
            problems.Add("The log file name isn't a valid file name");
        try
        {
            var parts = Masks.Parse(Comment);
            if (parts.Any(p => p.Field is not null and not "target")) throw new MaskException($"'{parts.First(p => p.Field is not null and not "target").Field}'");
            _ = LufsComment;
        }
        catch (MaskException e)
        {
            problems.Add($"The comment text is invalid ({e.Message}); it may only use {{target}}");
        }
        return problems;
    }

    public static ReplayGainOptions From(JsonElement? saved) => new(
        Saved.Number(saved, "target") ?? Default.Target,
        Saved.Number(saved, "safety_threshold") ?? Default.SafetyThreshold,
        Saved.Number(saved, "safety_margin") ?? Default.SafetyMargin,
        Saved.Strings(saved, "subfolder_keywords") ?? Default.SubfolderKeywords,
        Saved.Strings(saved, "track_data_keywords") ?? Default.TrackDataKeywords,
        Saved.Strings(saved, "track_data_excluded_keywords") ?? Default.ExcludedKeywords,
        Saved.Text(saved, "log_file") ?? Default.LogFile,
        Saved.Text(saved, "comment") ?? Default.Comment);

    public void Write(Utf8JsonWriter json)
    {
        json.WriteStartObject();
        json.WriteNumber("target", Target);
        json.WriteNumber("safety_threshold", SafetyThreshold);
        json.WriteNumber("safety_margin", SafetyMargin);
        Saved.Write(json, "subfolder_keywords", SubfolderKeywords);
        Saved.Write(json, "track_data_keywords", TrackDataKeywords);
        Saved.Write(json, "track_data_excluded_keywords", ExcludedKeywords);
        json.WriteString("log_file", LogFile);
        json.WriteString("comment", Comment);
        json.WriteEndObject();
    }
}

/// <summary>The Tidy page (SPEC §36 "8."): the music types its rename step renames (Rename's "Music file types"), and
/// the picture, video and other kinds it offers (Clean up's lists, SPEC §28 "Clutter"; the other files, cue sheets and
/// playlists by default, Hugo 2026-10-30).</summary>
public sealed record TidyOptions(IReadOnlyList<string> MusicTypes, IReadOnlyList<string> Pictures, IReadOnlyList<string> Videos,
    IReadOnlyList<string> Others)
{
    public static readonly TidyOptions Default = new(
        [.. Formats.Audio.Order(StringComparer.Ordinal)],
        [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"],
        [".mkv", ".avi", ".mov", ".wmv", ".m4v", ".webm", ".mpg", ".mpeg", ".vob"],
        [".cue", ".m3u", ".m3u8", ".pls", ".wpl", ".xspf", ".asx"]);

    public List<string> Validate()
    {
        var problems = new List<string>();
        void Check(string name, IReadOnlyList<string> extensions) => problems.AddRange(extensions
            .Where(e => !e.StartsWith('.') || e != e.ToLowerInvariant())
            .Order(StringComparer.Ordinal)
            .Select(e => $"{name}: '{e}' must be lowercase and start with a dot"));
        if (MusicTypes.Count == 0) problems.Add("Music file types: no file type");
        Check("Music file types", MusicTypes);
        problems.AddRange(MusicTypes.Where(e => !Formats.IsAudio(e)).Select(e => $"Music file types: '{e}' isn't a music format Refrain reads"));
        Check("Pictures", Pictures);
        Check("Videos", Videos);
        Check("Other files", Others);
        foreach (var e in Pictures.Concat(Videos).Concat(Others).Where(Formats.IsAudio))
            problems.Add($"'{e}' is a music format, so Tidy can't offer it");
        return problems;
    }

    public static TidyOptions From(JsonElement? saved) => new(
        Saved.Strings(saved, "music_types") ?? Default.MusicTypes,
        Saved.Strings(saved, "pictures") ?? Default.Pictures,
        Saved.Strings(saved, "videos") ?? Default.Videos,
        Saved.Strings(saved, "others") ?? Default.Others);

    public void Write(Utf8JsonWriter json)
    {
        json.WriteStartObject();
        Saved.Write(json, "music_types", MusicTypes);
        Saved.Write(json, "pictures", Pictures);
        Saved.Write(json, "videos", Videos);
        Saved.Write(json, "others", Others);
        json.WriteEndObject();
    }
}

/// <summary>Saved values read leniently: a missing or mistyped value falls back to its default (null here); a
/// well-typed invalid one is kept for the page's checks to show.</summary>
static class Saved
{
    static JsonElement? Get(JsonElement? saved, string key) =>
        saved is { ValueKind: JsonValueKind.Object } o && o.TryGetProperty(key, out var v) ? v : null;

    public static double? Number(JsonElement? saved, string key) =>
        Get(saved, key) is { ValueKind: JsonValueKind.Number } v ? v.GetDouble() : null;

    public static string? Text(JsonElement? saved, string key) =>
        Get(saved, key) is { ValueKind: JsonValueKind.String } v ? v.GetString() : null;

    public static IReadOnlyList<string>? Strings(JsonElement? saved, string key) =>
        Get(saved, key) is { ValueKind: JsonValueKind.Array } v && v.EnumerateArray().All(e => e.ValueKind == JsonValueKind.String)
            ? v.EnumerateArray().Select(e => e.GetString()!).ToList()
            : null;

    public static void Write(Utf8JsonWriter json, string key, IReadOnlyList<string> values)
    {
        json.WriteStartArray(key);
        foreach (var value in values) json.WriteStringValue(value);
        json.WriteEndArray();
    }
}

/// <summary>Python's number formatting where the Python app's text depends on it.</summary>
public static class Python
{
    /// <summary><c>f"{x:g}"</c>: six significant digits, no trailing zeros ("-15", "-18.5", "1e-05").</summary>
    public static string G(double value)
    {
        if (value == 0) return "0";
        var exponent = (int)Math.Floor(Math.Log10(Math.Abs(value)));
        if (exponent is < -4 or >= 6)
        {
            var mantissa = (value / Math.Pow(10, exponent)).ToString("0.#####", CultureInfo.InvariantCulture);
            return $"{mantissa}e{(exponent < 0 ? "-" : "+")}{Math.Abs(exponent):00}";
        }
        var decimals = Math.Max(0, 5 - exponent);
        return Math.Round(value, decimals).ToString("0." + new string('#', decimals), CultureInfo.InvariantCulture);
    }

    /// <summary><c>str(x)</c> of a float: the shortest text that reads back the same, "5.0" for a whole number,
    /// "1e-05" in exponent form.</summary>
    public static string Repr(double value)
    {
        if (double.IsNaN(value)) return "nan";
        if (double.IsInfinity(value)) return value > 0 ? "inf" : "-inf";
        var text = value.ToString("R", CultureInfo.InvariantCulture);
        var e = text.IndexOf('E');
        if (e < 0 && !text.Contains('.') && text.TrimStart('-').Length > 16)
        {
            // Python turns to an exponent from 1e16, .NET later.
            var minus = text.StartsWith('-') ? "-" : "";
            var integer = text[minus.Length..];
            var mantissa = integer.TrimEnd('0');
            return $"{minus}{mantissa[0]}{(mantissa.Length > 1 ? "." + mantissa[1..] : "")}e+{integer.Length - 1:00}";
        }
        if (e < 0) return text.Contains('.') ? text : text + ".0";
        var exponent = int.Parse(text[(e + 1)..], CultureInfo.InvariantCulture);
        if (exponent is < -4 or >= 16) return $"{text[..e]}e{(exponent < 0 ? "-" : "+")}{Math.Abs(exponent):00}";
        // .NET turns to an exponent from 1e15, Python from 1e16: write the digits out.
        var sign = text.StartsWith('-') ? "-" : "";
        var digits = text[sign.Length..e].Replace(".", "");
        var point = exponent + 1;
        var whole = digits.PadRight(point, '0');
        var fraction = whole[point..].TrimEnd('0');
        return $"{sign}{whole[..point]}.{(fraction.Length > 0 ? fraction : "0")}";
    }

    /// <summary><c>f"{x:.Nf}"</c>: the exact value rounded to N decimals, a tie to even (as Python).</summary>
    public static string Fixed(double value, int decimals)
    {
        if (!double.IsFinite(value)) return Repr(value);
        // .NET writes a double's exact digits at any precision; Python rounds those, a tie going to the even digit.
        var exact = value.ToString("F" + (decimals + 400), CultureInfo.InvariantCulture);
        var dot = exact.IndexOf('.');
        var tail = exact[(dot + 1 + decimals)..];
        var tie = tail[0] == '5' && tail.AsSpan(1).TrimEnd('0').IsEmpty;
        if (!tie) return value.ToString("F" + decimals, CultureInfo.InvariantCulture);
        var kept = exact[..(dot + 1 + decimals)].TrimEnd('.');
        var last = kept[^1];
        if ((last - '0') % 2 == 0) return kept;  // already even: the tie rounds down
        return value.ToString("F" + decimals, CultureInfo.InvariantCulture);  // odd: away from zero, as .NET does
    }

    /// <summary><c>round(x, n)</c>.</summary>
    public static double Round(double value, int decimals) =>
        double.Parse(Fixed(value, decimals), CultureInfo.InvariantCulture);
}
