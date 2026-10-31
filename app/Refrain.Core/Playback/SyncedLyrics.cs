using System.Globalization;
using System.Text.RegularExpressions;

namespace Refrain.Core.Playback;

/// <summary>A line of LRCLIB's synced lyrics: when it is sung, and its words (empty for an instrumental pause).</summary>
public sealed record SyncedLine(TimeSpan At, string Text);

/// <summary>LRCLIB's synced lyrics (LRC: "[01:02.34] words"), for the sung line (SPEC §37 "The player").</summary>
public static partial class SyncedLyrics
{
    /// <summary>The timed lines in time order; a line with several stamps is sung each time; other lines ("[ar: …]"
    /// tags, untimed text) are left out. Empty when nothing is timed.</summary>
    public static List<SyncedLine> Parse(string? lrc)
    {
        var lines = new List<SyncedLine>();
        if (string.IsNullOrWhiteSpace(lrc)) return lines;
        foreach (var raw in lrc.Replace("\r\n", "\n").Split('\n'))
        {
            var stamps = new List<TimeSpan>();
            var rest = raw.TrimStart();
            for (var match = Stamp().Match(rest); match.Success; match = Stamp().Match(rest))
            {
                var minutes = int.Parse(match.Groups[1].Value, CultureInfo.InvariantCulture);
                var seconds = double.Parse(match.Groups[2].Value.Replace(':', '.'), CultureInfo.InvariantCulture);
                stamps.Add(TimeSpan.FromMinutes(minutes) + TimeSpan.FromSeconds(seconds));
                rest = rest[match.Length..].TrimStart();
            }
            lines.AddRange(stamps.Select(at => new SyncedLine(at, rest.Trim())));
        }
        return [.. lines.OrderBy(l => l.At)];
    }

    /// <summary>The line sung at <paramref name="position"/>: the last one started; −1 before the first.</summary>
    public static int IndexAt(IReadOnlyList<SyncedLine> lines, TimeSpan position)
    {
        int low = 0, high = lines.Count - 1, found = -1;
        while (low <= high)
        {
            var middle = (low + high) / 2;
            if (lines[middle].At <= position)
            {
                found = middle;
                low = middle + 1;
            }
            else high = middle - 1;
        }
        return found;
    }

    [GeneratedRegex(@"^\[(\d{1,3}):(\d{1,2}(?:[.:]\d{1,3})?)\]")]
    private static partial Regex Stamp();
}
