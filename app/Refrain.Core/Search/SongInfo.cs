using System.Globalization;
using Microsoft.Data.Sqlite;
using Refrain.Core.Index;

namespace Refrain.Core.Search;

/// <summary>A song as the discography view's info panel shows it (SPEC §37): its tags and file information, the path
/// among them, as the index has them. Fields with no value are left out.</summary>
public sealed record SongInfo(string? Path, IReadOnlyList<(string Label, string Value)> Fields, string? RatingKey = null,
    double? Seconds = null)
{
    public static SongInfo? Read(SqliteConnection conn, long entryId)
    {
        using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT t.title, t.artist, t.albumartist, t.album, t.track, t.track_total, t.disc, t.disc_total, t.date, t.genre,
                   t.duration, t.format, t.bitrate, t.sample_rate, t.channels, t.has_cover, t.rg_track_gain, t.rg_album_gain,
                   e.size, e.name
            FROM tracks t JOIN entries e ON e.id = t.entry_id
            WHERE t.entry_id = $id
            """;
        cmd.Parameters.AddWithValue("$id", entryId);
        using var r = cmd.ExecuteReader();
        if (!r.Read()) return null;
        string? S(int i) => r.IsDBNull(i) || string.IsNullOrWhiteSpace(r.GetValue(i)?.ToString()) ? null : r.GetValue(i).ToString()!.Trim();
        long? N(int i) => r.IsDBNull(i) ? null : Convert.ToInt64(r.GetValue(i), CultureInfo.InvariantCulture);
        var fields = new List<(string, string)>();
        void Add(string label, string? value)
        {
            if (!string.IsNullOrWhiteSpace(value)) fields.Add((label, value));
        }
        Add("Title", S(0) ?? S(19));
        Add("Artist", S(1));
        if (S(2) is { } albumArtist && albumArtist != S(1)) Add("Album artist", albumArtist);
        Add("Album", S(3));
        Add("Track", N(4) is { } n ? N(5) is { } total ? $"{n} of {total}" : $"{n}" : null);
        Add("Disc", N(6) is { } d ? N(7) is { } discs ? $"{d} of {discs}" : $"{d}" : null);
        Add("Date", S(8));
        Add("Genre", S(9));
        Add("Length", r.IsDBNull(10) ? null : Length(r.GetDouble(10)));
        var format = new List<string>();
        if (S(11) is { } f) format.Add(f);
        if (N(13) is { } rate) format.Add($"{rate / 1000.0:0.#} kHz");
        if (N(14) is { } channels) format.Add(channels switch { 1 => "mono", 2 => "stereo", _ => $"{channels} channels" });
        if (N(12) is { } bitrate) format.Add($"{bitrate} kbps");
        Add("Format", string.Join(" · ", format));
        Add("Size", N(18) is { } size ? $"{size / 1048576.0:0.0} MB" : null);
        Add("Cover", N(15) is 1 ? "In the file" : "None in the file");
        Add("ReplayGain", S(17) is { } album ? $"album {album}{(S(16) is { } track ? $", track {track}" : "")}"
            : S(16) is { } trackOnly ? $"track {trackOnly}" : "No ReplayGain tags");
        var ratingKey = Ratings.Key(S(2) ?? S(1), S(3), S(0) ?? S(19));
        double? seconds = r.IsDBNull(10) ? null : r.GetDouble(10);
        r.Close();
        var path = Queries.PathOf(conn, entryId);
        Add("Location", path);
        return new SongInfo(path, fields, ratingKey, seconds);
    }

    /// <summary>"3:07", "1:02:45".</summary>
    public static string Length(double seconds)
    {
        var time = TimeSpan.FromSeconds(Math.Round(seconds));
        return time.TotalHours >= 1 ? $"{(int)time.TotalHours}:{time.Minutes:00}:{time.Seconds:00}" : $"{time.Minutes}:{time.Seconds:00}";
    }
}
