using System.Globalization;
using System.Text;

namespace Refrain.Core.Search;

/// <summary>Text as the search sees it: case and accents ignored, as the trigram index's remove_diacritics does
/// ("Sigur Ros" finds "Sigur Rós").</summary>
public static class Folding
{
    /// <summary>Folded text, and each folded character's offset in the original, so a match found in the folded
    /// text can be highlighted in the original.</summary>
    public static (string Folded, int[] Offsets) FoldWithOffsets(string text)
    {
        var folded = new StringBuilder(text.Length);
        var offsets = new List<int>(text.Length);
        var enumerator = StringInfo.GetTextElementEnumerator(text);
        while (enumerator.MoveNext())
        {
            var element = (string)enumerator.Current;
            var start = enumerator.ElementIndex;
            foreach (var c in Fold(element))
            {
                folded.Append(c);
                offsets.Add(start);
            }
        }
        return (folded.ToString(), offsets.ToArray());
    }

    /// <summary>Lowercased (with Python casefold's "ß" → "ss"), accents removed.</summary>
    public static string Fold(string text)
    {
        if (IsAscii(text)) return text.ToLowerInvariant();
        var decomposed = text.Normalize(NormalizationForm.FormD);
        var builder = new StringBuilder(decomposed.Length);
        foreach (var c in decomposed)
        {
            var category = CharUnicodeInfo.GetUnicodeCategory(c);
            if (category is UnicodeCategory.NonSpacingMark or UnicodeCategory.SpacingCombiningMark
                or UnicodeCategory.EnclosingMark)
                continue;
            switch (c)
            {
                case 'ß' or 'ẞ':
                    builder.Append("ss");
                    break;
                case 'ς':
                    builder.Append('σ');
                    break;
                default:
                    builder.Append(char.ToLowerInvariant(c));
                    break;
            }
        }
        return builder.ToString();
    }

    static bool IsAscii(string text)
    {
        foreach (var c in text)
            if (c > 0x7f) return false;
        return true;
    }

    /// <summary>Where the folded needles occur in <paramref name="text"/>, as (start, length) in the original,
    /// merged and sorted.</summary>
    public static IReadOnlyList<(int Start, int Length)> Spans(string? text, IReadOnlyList<string> needles)
    {
        if (string.IsNullOrEmpty(text) || needles.Count == 0) return [];
        var (folded, offsets) = FoldWithOffsets(text);
        var found = new List<(int Start, int End)>();
        foreach (var needle in needles)
        {
            if (needle.Length == 0) continue;
            var at = folded.IndexOf(needle, StringComparison.Ordinal);
            while (at >= 0)
            {
                var last = offsets[at + needle.Length - 1];
                var end = last + StringInfo.GetNextTextElementLength(text, last);
                found.Add((offsets[at], end));
                at = folded.IndexOf(needle, at + needle.Length, StringComparison.Ordinal);
            }
        }
        found.Sort();
        var merged = new List<(int Start, int End)>();
        foreach (var (start, end) in found)
        {
            if (merged.Count > 0 && start <= merged[^1].End)
                merged[^1] = (merged[^1].Start, Math.Max(end, merged[^1].End));
            else
                merged.Add((start, end));
        }
        return merged.Select(span => (span.Start, span.End - span.Start)).ToList();
    }
}
