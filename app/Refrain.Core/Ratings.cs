using System.Text.Json;
using System.Text.Json.Serialization;
using Refrain.Core.Search;

namespace Refrain.Core;

/// <summary>A song's rating: 0 to 5 stars by halves (0: none), and a free note.</summary>
public sealed record Rating(double Stars, string Note)
{
    public static readonly Rating None = new(0, "");
    public bool IsEmpty => Stars <= 0 && string.IsNullOrWhiteSpace(Note);
}

/// <summary>Ratings and notes (SPEC §37 "Ratings and notes"): the app's own data (<c>ratings.json</c> in the data
/// folder), never the files' tags, tied to the song's artist (its album artist, else its artist), album and title tags
/// grouped as the discography view groups them (<see cref="Names"/>), so a copy or a moved file keeps its rating.
/// Thread-safe; each change is saved at once.</summary>
public sealed class Ratings
{
    readonly string file;
    readonly Lock gate = new();
    readonly Dictionary<string, RatingJson> entries;

    public Ratings(string file)
    {
        this.file = file;
        entries = Load(file);
    }

    /// <summary>The key of a song; null without an artist or a title.</summary>
    public static string? Key(string? artist, string? album, string? title)
    {
        var a = Names.Key(artist);
        var t = Names.Key(title);
        return a.Length == 0 || t.Length == 0 ? null : $"{a}\u001f{Names.AlbumKey(album)}\u001f{t}";
    }

    public Rating Get(string key)
    {
        lock (gate)
            return entries.TryGetValue(key, out var found) ? new Rating(found.Stars, found.Note ?? "") : Rating.None;
    }

    /// <summary>Stars are rounded to halves between 0 and 5; an empty rating is removed.</summary>
    public void Set(string key, Rating rating)
    {
        var stars = Math.Clamp(Math.Round(rating.Stars * 2) / 2, 0, 5);
        lock (gate)
        {
            if (stars <= 0 && string.IsNullOrWhiteSpace(rating.Note)) entries.Remove(key);
            else entries[key] = new RatingJson { Stars = stars, Note = rating.Note.Length > 0 ? rating.Note : null };
            Save();
        }
    }

    static Dictionary<string, RatingJson> Load(string file)
    {
        try
        {
            return File.Exists(file)
                ? JsonSerializer.Deserialize(File.ReadAllText(file), RatingsJson.Default.DictionaryStringRatingJson) ?? []
                : [];
        }
        catch (Exception e) when (e is IOException or JsonException or UnauthorizedAccessException)
        {
            Log.Error($"{file} couldn't be read; ratings start empty and the file is kept as it is until the next change", e);
            return [];
        }
    }

    void Save()
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(file)!);
            var temp = file + ".tmp";
            File.WriteAllText(temp, JsonSerializer.Serialize(entries, RatingsJson.Default.DictionaryStringRatingJson));
            File.Move(temp, file, overwrite: true);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            Log.Error($"Ratings couldn't be saved to {file}", e);
        }
    }
}

public sealed class RatingJson
{
    [JsonPropertyName("stars")] public double Stars { get; set; }
    [JsonPropertyName("note")] public string? Note { get; set; }
}

[JsonSerializable(typeof(Dictionary<string, RatingJson>))]
[JsonSourceGenerationOptions(WriteIndented = true)]
internal sealed partial class RatingsJson : JsonSerializerContext;
