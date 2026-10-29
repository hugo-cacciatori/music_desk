using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;

namespace Refrain.Core.Options;

/// <summary>A mask that can't be read (an unbalanced brace, a bad format).</summary>
public sealed class MaskException(string message) : Exception(message);

/// <summary>File name masks (SPEC §21): how a music file is named from its tags. A mask is Python's format syntax over
/// the fields (<c>{track:02} {title}</c>), as the Python app stored it; the first mask whose fields are all present
/// names the file, with the characters Windows refuses replaced. Ported from <c>core/masks.py</c>.</summary>
public static partial class Masks
{
    public static readonly IReadOnlyList<string> NumberFields = ["track", "track_total", "disc", "disc_total"];
    public static readonly IReadOnlyList<string> TextFields = ["title", "artist", "albumartist", "album", "year", "genre", "composer"];
    public static readonly IReadOnlyList<string> Fields = [.. NumberFields, .. TextFields];
    public static readonly IReadOnlyList<string> DefaultMasks = ["{track:02} {title}", "{title}"];
    public const string DefaultReplacement = "-";

    [GeneratedRegex("[/\\\\?%*:|\"<>\\x7F\\x00-\\x1F]")]
    public static partial Regex Forbidden();

    /// <summary>A mask's parts: literal text, or a field with its format spec.</summary>
    public sealed record Part(string? Text, string? Field, string Spec);

    /// <summary>Python's <c>string.Formatter().parse</c>, for the plain fields masks use: <c>{{</c> and <c>}}</c> are
    /// braces; <c>{name}</c> or <c>{name:spec}</c> a field.</summary>
    public static List<Part> Parse(string mask)
    {
        var parts = new List<Part>();
        var text = new StringBuilder();
        for (var i = 0; i < mask.Length; i++)
        {
            var c = mask[i];
            if (c == '{' && i + 1 < mask.Length && mask[i + 1] == '{') { text.Append('{'); i++; continue; }
            if (c == '}' && i + 1 < mask.Length && mask[i + 1] == '}') { text.Append('}'); i++; continue; }
            if (c == '}') throw new MaskException("Single '}' encountered in format string");
            if (c != '{') { text.Append(c); continue; }
            var end = mask.IndexOf('}', i + 1);
            if (end < 0) throw new MaskException("expected '}' before end of string");
            var inside = mask[(i + 1)..end];
            if (inside.Contains('{')) throw new MaskException("unexpected '{' in field name");
            if (text.Length > 0) { parts.Add(new Part(text.ToString(), null, "")); text.Clear(); }
            var colon = inside.IndexOf(':');
            var name = colon < 0 ? inside : inside[..colon];
            if (name.Contains('!')) throw new MaskException("conversions aren't supported in masks");
            parts.Add(new Part(null, name, colon < 0 ? "" : inside[(colon + 1)..]));
            i = end;
        }
        if (text.Length > 0) parts.Add(new Part(text.ToString(), null, ""));
        return parts;
    }

    public static List<string> FieldsOf(string mask) => Parse(mask).Where(p => p.Field is not null).Select(p => p.Field!).ToList();

    /// <summary>Python's <c>format(value, spec)</c> for an int or a str: [[fill]align][sign][0][width][.precision][type].</summary>
    public static string Format(object value, string spec)
    {
        var m = SpecPattern().Match(spec);
        if (!m.Success) throw new MaskException($"Invalid format specifier '{spec}'");
        var fill = m.Groups["fill"].Success ? m.Groups["fill"].Value[0] : ' ';
        var align = m.Groups["align"].Success ? m.Groups["align"].Value[0] : '\0';
        var sign = m.Groups["sign"].Value;
        var zero = m.Groups["zero"].Success;
        var width = m.Groups["width"].Success ? int.Parse(m.Groups["width"].Value, CultureInfo.InvariantCulture) : 0;
        var precision = m.Groups["precision"].Success ? int.Parse(m.Groups["precision"].Value, CultureInfo.InvariantCulture) : (int?)null;
        var type = m.Groups["type"].Value;
        string body;
        var number = value is long or int;
        if (number)
        {
            var n = Convert.ToInt64(value, CultureInfo.InvariantCulture);
            if (precision is not null) throw new MaskException("Precision not allowed in integer format specifier");
            var digits = type switch
            {
                "" or "d" or "n" => Math.Abs(n).ToString(CultureInfo.InvariantCulture),
                "x" => Math.Abs(n).ToString("x", CultureInfo.InvariantCulture),
                "X" => Math.Abs(n).ToString("X", CultureInfo.InvariantCulture),
                "o" => Convert.ToString(Math.Abs(n), 8),
                "b" => Convert.ToString(Math.Abs(n), 2),
                _ => throw new MaskException($"Unknown format code '{type}' for object of type 'int'"),
            };
            var prefix = n < 0 ? "-" : sign == "+" ? "+" : sign == " " ? " " : "";
            if (zero && align == '\0') { fill = '0'; align = '='; }
            if (align == '=') return prefix + digits.PadLeft(Math.Max(0, width - prefix.Length), fill);
            body = prefix + digits;
            if (align == '\0') align = '>';
        }
        else
        {
            if (type is not ("" or "s")) throw new MaskException($"Unknown format code '{type}' for object of type 'str'");
            if (sign.Length > 0) throw new MaskException("Sign not allowed in string format specifier");
            if (align == '=') throw new MaskException("'=' alignment not allowed in string format specifier");
            body = value.ToString() ?? "";
            if (precision is { } p && body.Length > p) body = body[..p];
            if (zero && align == '\0') { fill = '0'; align = '<'; }
            if (align == '\0') align = '<';
        }
        if (body.Length >= width) return body;
        var pad = width - body.Length;
        return align switch
        {
            '<' => body + new string(fill, pad),
            '^' => new string(fill, pad / 2) + body + new string(fill, pad - pad / 2),
            _ => new string(fill, pad) + body,
        };
    }

    [GeneratedRegex(@"^(?:(?<fill>.)?(?<align>[<>=^]))?(?<sign>[+\- ]?)(?<zero>0)?(?<width>\d+)?(?:\.(?<precision>\d+))?(?<type>[a-zA-Z%]?)$", RegexOptions.Singleline)]
    private static partial Regex SpecPattern();

    /// <summary>A mask filled from these fields (a field missing throws).</summary>
    public static string Apply(string mask, IReadOnlyDictionary<string, object> fields)
    {
        var text = new StringBuilder();
        foreach (var part in Parse(mask))
            text.Append(part.Text ?? Format(fields[part.Field!], part.Spec));
        return text.ToString();
    }

    /// <summary>The problems in the masks and the replacement (none when they are fine): the script's checks.</summary>
    public static List<string> Validate(IReadOnlyList<string> masks, string replacement)
    {
        var problems = new List<string>();
        if (masks.Count == 0) problems.Add("No rename mask");
        var sample = Fields.ToDictionary(f => f, f => NumberFields.Contains(f) ? (object)1L : "x");
        foreach (var mask in masks)
        {
            List<Part> parts;
            try
            {
                parts = Parse(mask);
            }
            catch (MaskException e)
            {
                problems.Add($"mask '{mask}': {e.Message}");
                continue;
            }
            var unknown = parts.Where(p => p.Field is not null && !sample.ContainsKey(p.Field)).Select(p => p.Field).ToList();
            if (unknown.Count > 0)
            {
                problems.Add($"mask '{mask}': unknown field(s) {string.Join(", ", unknown)}");
                continue;
            }
            if (parts.All(p => p.Field is null)) problems.Add($"mask '{mask}': uses no field");
            if (Forbidden().IsMatch(string.Concat(parts.Select(p => p.Text ?? "")))) problems.Add($"mask '{mask}': contains a character not allowed in file names");
            try
            {
                Apply(mask, sample);
            }
            catch (MaskException e)
            {
                problems.Add($"mask '{mask}': invalid format ({e.Message})");
            }
        }
        if (Forbidden().IsMatch(replacement)) problems.Add("The replacement contains a character not allowed in file names");
        return problems;
    }

    public static string Sanitize(string text, string replacement) => Forbidden().Replace(text, replacement);

    /// <summary>The full file name these fields give (the extension lowercased), or null when no mask fits. Numbers
    /// stay numbers; text is sanitized.</summary>
    public static (string Name, int Mask)? NameFor(IReadOnlyDictionary<string, object> fields, IReadOnlyList<string> masks,
        string replacement, string extension)
    {
        var values = fields.ToDictionary(kv => kv.Key, kv => kv.Value is string s ? (object)Sanitize(s, replacement) : kv.Value);
        for (var i = 0; i < masks.Count; i++)
            if (FieldsOf(masks[i]).All(values.ContainsKey))
                return (Apply(masks[i], values) + extension.ToLowerInvariant(), i);
        return null;
    }

    public static string MissingFrom(string mask, IReadOnlyDictionary<string, object> fields) =>
        string.Join(", ", FieldsOf(mask).Where(f => !fields.ContainsKey(f)));
}
