using System.Text.RegularExpressions;

namespace Refrain.Core.Search;

/// <summary>A parsed query (SPEC §6 "As built"): the free text parts and every filter are ANDed; a filter's values
/// are ORed.</summary>
public sealed partial record SearchQuery(
    IReadOnlyList<string> Text,
    IReadOnlyDictionary<string, IReadOnlyList<string>> Filters,
    IReadOnlyList<string> Errors)
{
    /// <summary>Filters on text: their columns of the search table. <c>artist:</c> covers the album artist too.</summary>
    public static readonly IReadOnlyDictionary<string, string[]> TextFields = new Dictionary<string, string[]>
    {
        ["artist"] = ["artist", "albumartist"],
        ["album"] = ["album"],
        ["genre"] = ["genre"],
    };

    /// <summary>Filters with a fixed set of values.</summary>
    public static readonly IReadOnlyDictionary<string, string[]> ValueFields = new Dictionary<string, string[]>
    {
        ["rg"] = ["none", "partial", "full"],
    };

    static readonly HashSet<string> Fields = [.. TextFields.Keys, "year", "ext", .. ValueFields.Keys];

    /// <summary>The filters that need tags: they limit the results to audio files whose tags are read.</summary>
    public static readonly IReadOnlySet<string> TagFilters = new HashSet<string> { "artist", "album", "genre", "year", "rg" };

    [GeneratedRegex("""(?<field>\b[A-Za-z]+):(?:"(?<quoted>[^"]*)"?|(?<bare>[^\s"]*))|"(?<phrase>[^"]*)"?""")]
    private static partial Regex Token();

    public bool IsEmpty => Text.Count == 0 && Filters.Count == 0;

    /// <summary>The folded text to highlight: the free text, plus the values of the named text filters.</summary>
    public List<string> Needles(params string[] fields)
    {
        var found = Text.Select(Folding.Fold).ToList();
        foreach (var name in fields)
            if (Filters.TryGetValue(name, out var values))
                found.AddRange(values.Select(Folding.Fold));
        return found.Where(n => n.Length > 0).ToList();
    }

    /// <summary><c>artist:x album:"a b" "quoted phrase" free words</c>. A word before a colon that isn't a filter
    /// stays text ("Re: Stacks"). The unquoted remainder, trimmed, is one substring, as seek_lyrics' box took it; a
    /// filter with an unknown value is reported in <see cref="Errors"/> and ignored.</summary>
    public static SearchQuery Parse(string text)
    {
        var free = new List<string>();
        var phrases = new List<string>();
        var filters = new Dictionary<string, List<string>>();
        var errors = new List<string>();
        var position = 0;
        foreach (Match match in Token().Matches(text))
        {
            var field = match.Groups["field"];
            var name = field.Success ? field.Value.ToLowerInvariant() : "";
            if (field.Success && !Fields.Contains(name)) continue;  // "Re:" stays in the free text
            free.Add(text[position..match.Index]);
            position = match.Index + match.Length;
            if (!field.Success)
            {
                var phrase = match.Groups["phrase"].Value;
                if (phrase.Trim().Length > 0) phrases.Add(phrase);
                continue;
            }
            var value = match.Groups["quoted"].Success ? match.Groups["quoted"].Value : match.Groups["bare"].Value;
            if (value.Trim().Length == 0) continue;  // "artist:" while it is being typed
            if (ValueFields.TryGetValue(name, out var allowed))
            {
                value = value.ToLowerInvariant();
                if (!allowed.Contains(value))
                {
                    errors.Add($"{name}:{value} isn't a known filter. Use {string.Join(", ", allowed)}.");
                    continue;
                }
            }
            else if (name == "ext")
            {
                value = "." + value.ToLowerInvariant().TrimStart('.');
            }
            if (!filters.TryGetValue(name, out var list)) filters[name] = list = [];
            list.Add(value);
        }
        free.Add(text[position..]);
        var remainder = string.Join(" ", free.Select(p => p.Trim()).Where(p => p.Length > 0));
        var parts = new List<string>();
        if (remainder.Length > 0) parts.Add(remainder);
        parts.AddRange(phrases);
        return new SearchQuery(parts,
            filters.ToDictionary(kv => kv.Key, kv => (IReadOnlyList<string>)kv.Value),
            errors);
    }
}
