namespace Refrain.Core.Tags;

/// <summary>Which fields a Metadata tab save writes (SPEC §7), from the texts in its fields: for one file, those that
/// differ from it; for several, the shared fields that were edited or cleared. An empty value clears a field. Ported
/// from the Python app's <c>core/tag_edits.py</c>.</summary>
public static class TagEdits
{
    /// <summary>Edited one file at a time; disabled with several (SPEC §7).</summary>
    public static readonly IReadOnlySet<Field> PerTrack = new HashSet<Field> { Field.Title, Field.Track };

    public static readonly IReadOnlyList<Field> Shared =
        Enum.GetValues<Field>().Where(f => !PerTrack.Contains(f)).ToList();

    /// <summary>Text and number fields compare by value: "03" is the same track number as "3".</summary>
    static object? Parse(Field field, string text)
    {
        if (text.Trim().Length == 0) return null;
        return TagFile.Numbers.Contains(field) && long.TryParse(text.Trim(), out var n) ? n : text;
    }

    /// <summary>Each shared field's value when every file has the same, else null ("(multiple values)").</summary>
    public static Dictionary<Field, string?> Common(IReadOnlyList<FileTags> files)
    {
        var common = new Dictionary<Field, string?>();
        foreach (var field in Shared)
        {
            var values = files.Select(f => f.Values[field]).Distinct().ToList();
            common[field] = values.Count == 1 ? values[0] : null;
        }
        return common;
    }

    /// <summary>The fields whose text differs from the file's value.</summary>
    public static Dictionary<Field, string> SingleChanges(FileTags file, IReadOnlyDictionary<Field, string> texts)
    {
        var changed = new Dictionary<Field, string>();
        foreach (var (field, text) in texts)
        {
            var original = file.Values[field];
            if (text == original) continue;  // untouched, even when the file holds blank text such as "  "
            if (!Equals(Parse(field, text), Parse(field, original))) changed[field] = text;
        }
        return changed;
    }

    /// <summary>The shared fields edited or cleared; a "(multiple values)" field emptied by hand stays untouched (the
    /// clear button clears it on every file).</summary>
    public static Dictionary<Field, string> MultiChanges(IReadOnlyDictionary<Field, string> texts,
        IReadOnlySet<Field> touched, IReadOnlySet<Field> cleared, IReadOnlyDictionary<Field, string?> common)
    {
        var changed = new Dictionary<Field, string>();
        foreach (var field in Shared)
        {
            if (!touched.Contains(field) && !cleared.Contains(field)) continue;
            var text = cleared.Contains(field) ? "" : texts[field];
            var multiple = common[field] is null;
            if (text.Trim().Length == 0 && !cleared.Contains(field) && multiple) continue;
            if (!multiple && Equals(Parse(field, text), Parse(field, common[field]!))) continue;
            changed[field] = text;
        }
        return changed;
    }
}
