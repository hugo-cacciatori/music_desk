using System.Text;
using System.Text.RegularExpressions;

namespace Refrain.Core.Tags;

/// <summary>The LRCLIB cache key of a track, as the Python app computes it, so its results carry over (task 10):
/// <c>casefold(artist + "\x1f" + title)</c>, with the artist and title from the tags, else from the file name.</summary>
public static partial class LyricsKey
{
    [GeneratedRegex(@"^\d{1,3}[\s\-._]+")]
    private static partial Regex LeadingNumber();

    public static string? Of(string? artist, string? albumArtist, string? title, string fileName)
    {
        var a = FirstText(artist, albumArtist);
        var t = FirstText(title);
        if (a is null || t is null)
        {
            var (fileArtist, fileTitle) = FromFileName(fileName);
            a ??= fileArtist;
            t ??= fileTitle;
        }
        return a is null || string.IsNullOrEmpty(t) ? null : CaseFold(a + "\x1f" + t);
    }

    /// <summary>Best-effort "Artist - Title" from a file name, after a leading track number.</summary>
    public static (string? Artist, string Title) FromFileName(string name)
    {
        var stem = LeadingNumber().Replace(Path.GetFileNameWithoutExtension(name), "");
        var dash = stem.IndexOf(" - ", StringComparison.Ordinal);
        if (dash < 0) return (null, stem.Trim());
        var artist = stem[..dash].Trim();
        return (artist.Length > 0 ? artist : null, stem[(dash + 3)..].Trim());
    }

    static string? FirstText(params string?[] values) =>
        values.Select(v => v?.Trim()).FirstOrDefault(v => !string.IsNullOrEmpty(v));

    /// <summary>Python's str.casefold for the text tags hold: lowercase, plus the full foldings that differ ("ß" →
    /// "ss", final sigma → sigma, the Latin ligatures).</summary>
    public static string CaseFold(string text)
    {
        var builder = new StringBuilder(text.Length);
        foreach (var c in text)
        {
            builder.Append(c switch
            {
                'ß' or 'ẞ' => "ss",
                'ς' => "σ",
                'ﬀ' => "ff",
                'ﬁ' => "fi",
                'ﬂ' => "fl",
                'ﬃ' => "ffi",
                'ﬄ' => "ffl",
                'ﬅ' or 'ﬆ' => "st",
                'ŉ' => "ʼn",
                _ => char.ToLowerInvariant(c).ToString(),
            });
        }
        return builder.ToString();
    }
}
