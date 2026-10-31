using System.Globalization;
using Microsoft.Data.Sqlite;
using Refrain.Core.Index;
using Refrain.Core.Options;

namespace Refrain.Core.ReplayGain;

/// <summary>ReplayGain around the player (SPEC §37): the albums of the library without ReplayGain tags, from the index
/// alone (a scan's end asks about them; no file is read), and a tag's gain as a number.</summary>
public static class Missing
{
    /// <summary>The album folders (as ReplayGain's album workflow sees them, SPEC §10 B) with a track rsgain scans that
    /// lacks its album or track gain. A disc subfolder (a keyword subfolder that isn't a copy) counts with its album
    /// when that folder holds tracks itself; a copy (lite) is never counted; a folder whose one track has a cue sheet
    /// beside it is an image waiting to be split, left out. An album with the tags, even for another target, isn't
    /// counted.</summary>
    public static List<string> AlbumsWithoutTags(SqliteConnection conn, ReplayGainOptions options)
    {
        var scanned = string.Join(", ", Formats.ReplayGain.Select(e => $"'{e}'"));
        var folders = new Dictionary<long, (string Name, long? Parent, int Tracks, bool Missing, bool Cue)>();
        using (var cmd = conn.CreateCommand())
        {
            cmd.CommandText = $"""
                SELECT f.id, f.name, f.parent_id,
                       count(t.entry_id),
                       max(t.entry_id IS NOT NULL AND (t.rg_album_gain IS NULL OR t.rg_track_gain IS NULL)),
                       (SELECT count(*) FROM entries c WHERE c.parent_id = f.id AND lower(c.ext) = '.cue')
                FROM entries f
                JOIN entries e ON e.parent_id = f.id AND lower(e.ext) IN ({scanned})
                LEFT JOIN tracks t ON t.entry_id = e.id AND t.read_error IS NULL
                WHERE f.is_dir = 1
                GROUP BY f.id
                """;
            using var r = cmd.ExecuteReader();
            while (r.Read())
                folders[r.GetInt64(0)] = (r.GetString(1), r.IsDBNull(2) ? null : r.GetInt64(2), r.GetInt32(3),
                    !r.IsDBNull(4) && r.GetInt64(4) != 0, r.GetInt64(5) > 0);
        }
        var albums = new HashSet<long>();
        foreach (var (id, folder) in folders)
        {
            if (folder.Tracks == 0) continue;
            var keyword = options.SubfolderKeywords.Any(k => folder.Name.ToLowerInvariant().Contains(k, StringComparison.Ordinal));
            if (keyword && folder.Parent is { } parent && folders.ContainsKey(parent))
            {
                if (Album.IsDisc(folder.Name, options) && folder.Missing) albums.Add(parent);  // measured with its album
                continue;
            }
            if (folder.Tracks == 1 && folder.Cue) continue;  // an image waiting to be split
            if (folder.Missing) albums.Add(id);
        }
        return albums.Select(id => Queries.PathOf(conn, id)).Where(p => p is not null).Select(p => p!)
            .Order(StringComparer.OrdinalIgnoreCase).ToList();
    }

    /// <summary>"-6.52 dB" as −6.52; null when it doesn't start with a number.</summary>
    public static double? Gain(string? tag)
    {
        if (string.IsNullOrWhiteSpace(tag)) return null;
        var text = tag.Trim();
        var end = 0;
        while (end < text.Length && (char.IsAsciiDigit(text[end]) || text[end] is '-' or '+' or '.')) end++;
        return double.TryParse(text[..end], NumberStyles.Float, CultureInfo.InvariantCulture, out var gain) ? gain : null;
    }

    /// <summary>The volume's factor for a gain in dB, never above 1: the player can lower a loud song, not raise a quiet
    /// one past its own level.</summary>
    public static double Factor(double? gain) => gain is { } g ? Math.Min(1, Math.Pow(10, g / 20)) : 1;
}
