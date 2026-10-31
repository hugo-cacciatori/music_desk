using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;

namespace Refrain.Core.Search;

/// <summary>How the library by tags groups names (SPEC §37 "The library by tags"): an artist is one artist whatever the
/// case, accents and punctuation of its tags ("Rhapsody of Fire" is "RHAPSODY OF FIRE"); an album is one album once
/// markers such as "CD1", "Disc 2" or "[Remastered]" are set aside too.</summary>
public static partial class Names
{
    /// <summary>The grouping key: letters and digits only, accents dropped, lower case, words one space apart
    /// ("AC/DC" and "Ac-Dc" are "ac dc"). Empty when the name has no letter or digit.</summary>
    public static string Key(string? name)
    {
        if (string.IsNullOrWhiteSpace(name)) return "";
        var text = new StringBuilder(name.Length);
        var space = false;
        foreach (var c in name.Normalize(NormalizationForm.FormKD))
        {
            if (CharUnicodeInfo.GetUnicodeCategory(c) == UnicodeCategory.NonSpacingMark) continue;
            if (char.IsLetterOrDigit(c))
            {
                if (space && text.Length > 0) text.Append(' ');
                text.Append(char.ToLowerInvariant(c));
                space = false;
            }
            else space = true;
        }
        return text.ToString();
    }

    /// <summary>An album name without its disc and edition markers, as written otherwise: "Homogenic (CD1)",
    /// "Homogenic - Disc 2" and "Homogenic [Remastered 2004]" are "Homogenic". A name that is only a marker stays.</summary>
    public static string WithoutMarkers(string? album)
    {
        if (string.IsNullOrWhiteSpace(album)) return "";
        var name = album.Trim();
        for (var changed = true; changed;)
        {
            var next = Marker().Replace(name, "").Trim();
            changed = next != name && next.Length > 0;
            if (changed) name = next;
        }
        return name;
    }

    /// <summary>The album's grouping key: <see cref="Key"/> of <see cref="WithoutMarkers"/>.</summary>
    public static string AlbumKey(string? album) => Key(WithoutMarkers(album));

    /// <summary>The spelling to show for a group: the most frequent, the first seen on a tie.</summary>
    public static string Shown(IEnumerable<string> spellings) =>
        spellings.Select((s, i) => (s, i)).GroupBy(x => x.s, StringComparer.Ordinal)
            .OrderByDescending(g => g.Count()).ThenBy(g => g.First().i).Select(g => g.Key).FirstOrDefault() ?? "";

    // A marker at the end of the name: in brackets or parentheses ("(CD 1)", "[Disc 2]", "(Remastered 2004)",
    // "[2011 Remaster]", "(Deluxe Edition)"), or after a dash or alone ("- CD2", " Disc 1", " CD1").
    [GeneratedRegex(@"\s*(?:[\(\[]\s*(?:(?:cd|disc|disk)\s*\d+(?:\s*of\s*\d+)?|(?:\d{4}\s+)?(?:digital(?:ly)?\s+)?remaster(?:ed)?(?:\s+(?:version|edition))?(?:\s+\d{4})?|(?:deluxe|expanded|special|anniversary|bonus\s+track)(?:\s+(?:edition|version))?)\s*[\)\]]|(?:\s+-\s*|\s+)(?:cd|disc|disk)\s*\d+)$",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant)]
    private static partial Regex Marker();
}
