using System.Globalization;
using System.Net;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;
using Refrain.Core.Tags;

namespace Refrain.Core.Lyrics;

/// <summary>What a lookup needs from a file. Only artist, title, album and duration are ever sent to LRCLIB.</summary>
public sealed record LyricsTrack(string Artist, string Title, string? Album, int Duration)
{
    public string Key => LyricsKey.CaseFold(Artist + "\x1f" + Title);

    /// <summary>Rebuilt as the scanner built it, so the "Artist - Title" file-name fallback gives the same key; null
    /// without an artist or a title.</summary>
    public static LyricsTrack? From(string? artist, string? albumArtist, string? title, string? album, double? duration,
        string fileName) =>
        LyricsKey.Parts(artist, albumArtist, title, fileName) is var (a, t) && a is not null && t is { Length: > 0 }
            ? new LyricsTrack(a, t, string.IsNullOrWhiteSpace(album) ? null : album.Trim(), (int)Math.Round(duration ?? 0, MidpointRounding.ToEven))
            : null;
}

/// <summary>A cached result: found (with lyrics), instrumental, or not found, with the date it was checked
/// (yyyy-MM-dd, as the Python app stored it). A lookup error is never cached.</summary>
public sealed record LyricsEntry(string Status, long? LrclibId = null, string? Plain = null, string? Synced = null,
    string Checked = "")
{
    public const string Found = "found";
    public const string Instrumental = "instrumental";
    public const string NotFound = "not_found";
}

/// <summary>An LRCLIB record, as its JSON names it.</summary>
public sealed class LrclibRecord
{
    [JsonPropertyName("id")] public long? Id { get; set; }
    [JsonPropertyName("artistName")] public string? ArtistName { get; set; }
    [JsonPropertyName("duration")] public double? Duration { get; set; }
    [JsonPropertyName("instrumental")] public bool Instrumental { get; set; }
    [JsonPropertyName("plainLyrics")] public string? PlainLyrics { get; set; }
    [JsonPropertyName("syncedLyrics")] public string? SyncedLyrics { get; set; }
}

[JsonSerializable(typeof(LrclibRecord))]
[JsonSerializable(typeof(List<LrclibRecord>))]
internal sealed partial class LrclibJson : JsonSerializerContext;

/// <summary>LRCLIB's rules, ported from the Python app's <c>core/lyrics.py</c> (the seek_lyrics script's: the same
/// requests, retries, matching and cache entries; SPEC §9).</summary>
public static partial class Lrclib
{
    public const int NotFoundRetryDays = 30;
    public const int DurationTolerance = 3;  // seconds between a file and an LRCLIB search result

    [GeneratedRegex(@"^\s*\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]")]
    private static partial Regex Timestamp();

    [GeneratedRegex(@"[\W_]+")]
    private static partial Regex NonWord();

    public static string Normalize(string? text) => NonWord().Replace(LyricsKey.CaseFold(text ?? ""), "");

    /// <summary>Whether an LRCLIB search result is the same recording as the file.</summary>
    public static bool Matches(LyricsTrack track, LrclibRecord record)
    {
        if (track.Duration > 0 && record.Duration is > 0 && Math.Abs(track.Duration - record.Duration.Value) > DurationTolerance)
            return false;
        var wanted = Normalize(track.Artist);
        var found = Normalize(record.ArtistName);
        return wanted.Length > 0 && found.Length > 0 && (found.Contains(wanted) || wanted.Contains(found));
    }

    public static string StripTimestamps(string lrc) =>
        string.Join("\n", lrc.Replace("\r\n", "\n").Split('\n').Select(line => Timestamp().Replace(line, "").Trim())).Trim();

    /// <summary>Looks a track up: the exact match first (with album and duration), then a search filtered on the
    /// duration and the artist, preferring synced lyrics, then plain. Throws on a failed request.</summary>
    public static async Task<LyricsEntry> Fetch(LyricsTrack track, LrclibClient client, DateOnly today,
        CancellationToken cancel = default)
    {
        LrclibRecord? record = null;
        if (track.Album is not null && track.Duration > 0)
            record = await client.Get("get", LrclibJson.Default.LrclibRecord, cancel,
                ("artist_name", track.Artist), ("track_name", track.Title), ("album_name", track.Album),
                ("duration", track.Duration.ToString(CultureInfo.InvariantCulture)));
        if (record is null)
        {
            var results = await client.Get("search", LrclibJson.Default.ListLrclibRecord, cancel,
                ("track_name", track.Title), ("artist_name", track.Artist)) ?? [];
            record = results.Where(r => Matches(track, r))
                .OrderBy(r => string.IsNullOrEmpty(r.SyncedLyrics))
                .ThenBy(r => string.IsNullOrEmpty(r.PlainLyrics))
                .FirstOrDefault();
        }
        var checkedOn = today.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        if (record is null) return new LyricsEntry(LyricsEntry.NotFound, Checked: checkedOn);
        if (record.Instrumental) return new LyricsEntry(LyricsEntry.Instrumental, record.Id, Checked: checkedOn);
        if (string.IsNullOrEmpty(record.PlainLyrics) && string.IsNullOrEmpty(record.SyncedLyrics))
            return new LyricsEntry(LyricsEntry.NotFound, Checked: checkedOn);
        return new LyricsEntry(LyricsEntry.Found, record.Id,
            string.IsNullOrEmpty(record.PlainLyrics) ? StripTimestamps(record.SyncedLyrics!) : record.PlainLyrics,
            string.IsNullOrEmpty(record.SyncedLyrics) ? null : record.SyncedLyrics, checkedOn);
    }

    /// <summary>A cached entry is used unless it is a not_found older than the retry delay.</summary>
    public static bool IsFresh(LyricsEntry? entry, DateOnly today) =>
        entry is not null && (entry.Status != LyricsEntry.NotFound ||
                              string.CompareOrdinal(entry.Checked, RetryBefore(today)) >= 0);

    /// <summary>not_found entries checked before this date are looked up again.</summary>
    public static string RetryBefore(DateOnly today) =>
        today.AddDays(-NotFoundRetryDays).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);

    /// <summary>The Genius and Google search links (encoded like JavaScript's encodeURIComponent).</summary>
    public static (string Genius, string Google) SearchUrls(string? artist, string? title)
    {
        var text = string.Join(" ", new[] { artist, title }.Where(p => !string.IsNullOrEmpty(p)));
        var query = Uri.EscapeDataString(text);
        foreach (var c in "!*'()") query = query.Replace(Uri.EscapeDataString(c.ToString()), c.ToString());
        return ($"https://genius.com/search?q={query}", $"https://www.google.com/search?q={query}+lyrics");
    }
}

/// <summary>GETs LRCLIB endpoints with the script's retry rules: "server busy" (429, 503) up to 10 times after the
/// pause it asks for; network and other server errors up to 4 attempts with a growing pause. The handler, the
/// pause and the jitter are injectable, so tests never touch the network or wait.</summary>
public sealed class LrclibClient : IDisposable
{
    const string Api = "https://lrclib.net/api";
    const int MaxAttempts = 4;
    const int MaxOverloadRetries = 10;

    readonly HttpClient http;
    readonly Func<TimeSpan, CancellationToken, Task> pause;
    readonly Func<double> jitter;

    public LrclibClient(HttpMessageHandler? handler = null, Func<TimeSpan, CancellationToken, Task>? pause = null,
        Func<double>? jitter = null)
    {
        http = handler is null ? new HttpClient() : new HttpClient(handler);
        http.Timeout = TimeSpan.FromSeconds(30);
        var version = typeof(LrclibClient).Assembly.GetName().Version?.ToString(3) ?? "0";
        http.DefaultRequestHeaders.UserAgent.ParseAdd($"Refrain/{version} (personal music library app)");
        this.pause = pause ?? Task.Delay;
        this.jitter = jitter ?? (() => Random.Shared.NextDouble() * 0.5);
    }

    /// <summary>The decoded JSON, or null for an unknown song (400, 404). Throws when the retries are used up.</summary>
    public async Task<T?> Get<T>(string endpoint, System.Text.Json.Serialization.Metadata.JsonTypeInfo<T> type,
        CancellationToken cancel, params (string Name, string Value)[] query)
    {
        var url = $"{Api}/{endpoint}?" + string.Join("&", query.Select(q => $"{q.Name}={WebUtility.UrlEncode(q.Value)}"));
        var overloads = 0;
        var failures = 0;
        while (true)
        {
            TimeSpan delay;
            try
            {
                using var response = await http.GetAsync(url, cancel);
                var code = (int)response.StatusCode;
                if (response.IsSuccessStatusCode)
                    return JsonSerializer.Deserialize(await response.Content.ReadAsStreamAsync(cancel), type);
                if (code is 400 or 404) return default;
                if (code is 429 or 503)
                {
                    overloads++;
                    if (overloads > MaxOverloadRetries) throw new HttpRequestException($"LRCLIB is busy ({code})", null, response.StatusCode);
                    var asked = response.Headers.RetryAfter?.Delta?.TotalSeconds;
                    delay = TimeSpan.FromSeconds(asked is > 0 ? Math.Min(60, asked.Value) : Math.Min(10, overloads));
                }
                else if (code is 500 or 502 or 504)
                {
                    failures++;
                    if (failures >= MaxAttempts) throw new HttpRequestException($"LRCLIB error {code}", null, response.StatusCode);
                    delay = TimeSpan.FromSeconds(Math.Pow(2, failures));
                }
                else
                {
                    throw new HttpRequestException($"LRCLIB answered {code}", null, response.StatusCode);
                }
            }
            catch (Exception e) when (e is HttpRequestException { StatusCode: null } or TaskCanceledException && !cancel.IsCancellationRequested)
            {
                failures++;
                if (failures >= MaxAttempts) throw;
                delay = TimeSpan.FromSeconds(Math.Pow(2, failures));
            }
            // A little randomness so parallel lookups don't all retry at the same instant.
            await pause(delay + TimeSpan.FromSeconds(jitter()), cancel);
        }
    }

    public void Dispose() => http.Dispose();
}
