using System.Text.RegularExpressions;

namespace Refrain.Core.Cue;

/// <summary>A sheet the split can't use; the message says why, in words for the user.</summary>
public sealed class CueException(string message) : Exception(message);

/// <summary>An audio track of a sheet; times in CD frames (1/75 s).</summary>
public sealed class CueTrack(int number, string file)
{
    public int Number { get; } = number;
    public string File { get; } = file;  // the FILE line it falls under
    public string Title { get; set; } = "";
    public string Performer { get; set; } = "";
    public string Songwriter { get; set; } = "";
    public string Isrc { get; set; } = "";
    public int? Index00 { get; set; }
    public int? Index01 { get; set; }
}

/// <summary>A cue sheet's album fields, files and audio tracks (SPEC §22). Ported from the Python app's
/// <c>core/cue.py</c>: unknown lines are ignored, as players do.</summary>
public sealed partial class CueSheet
{
    public const int FramesPerSecond = 75;

    public string? Path { get; init; }
    public string Encoding { get; init; } = "UTF-8";
    public string Title { get; set; } = "";
    public string Performer { get; set; } = "";
    public string Date { get; set; } = "";
    public string Genre { get; set; } = "";
    public int? Disc { get; set; }
    public int? DiscTotal { get; set; }
    public List<string> Files { get; } = [];  // every FILE name, in order
    public List<CueTrack> Tracks { get; } = [];  // audio tracks only
    public int DataTracks { get; set; }  // MODE1/2352 and other non-audio tracks, skipped

    [GeneratedRegex(@"^(\d+):(\d{1,2}):(\d{1,2})$")]
    private static partial Regex Time();

    // Python's str.splitlines boundaries (U+2028 and U+2029 by code, never as text).
    static readonly Regex LineBreaks = new("\r\n|[\n\r\v\f\x1c\x1d\x1e\x85" + (char)0x2028 + (char)0x2029 + "]");
    static Regex LineBreak() => LineBreaks;

    /// <summary>'mm:ss:ff' → CD frames. Throws CueException.</summary>
    public static int FramesFromTime(string text)
    {
        var match = Time().Match(text.Trim());
        if (!match.Success) throw new CueException($"'{text}' isn't a cue time (mm:ss:ff)");
        var (minutes, seconds, frames) = (int.Parse(match.Groups[1].Value), int.Parse(match.Groups[2].Value),
            int.Parse(match.Groups[3].Value));
        if (seconds >= 60 || frames >= FramesPerSecond) throw new CueException($"'{text}' isn't a cue time (mm:ss:ff)");
        return (minutes * 60 + seconds) * FramesPerSecond + frames;
    }

    /// <summary>A cue value: the text between quotes, or the rest of the line.</summary>
    static string Value(string rest)
    {
        rest = rest.Trim();
        if (!rest.StartsWith('"')) return rest;
        var end = rest.IndexOf('"', 1);
        return end > 0 ? rest[1..end] : rest[1..];
    }

    /// <summary>FILE "name with spaces.flac" WAVE → the name (the type is the last word).</summary>
    static string FileName(string rest)
    {
        rest = rest.Trim();
        if (rest.StartsWith('"')) return Value(rest);
        if (!rest.Contains(' ')) return rest;
        var words = Words(rest);
        var last = rest.LastIndexOf(words[^1], StringComparison.Ordinal);
        return rest[..last].TrimEnd();
    }

    static string[] Words(string text) => text.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries);

    static bool IsDigits(string text) => text.Length > 0 && text.All(char.IsAsciiDigit);

    static int? Number(string text)
    {
        text = text.Trim().Split('/')[0];
        return IsDigits(text) ? int.Parse(text) : null;
    }

    static (string Head, string Tail) Partition(string text)
    {
        var space = text.IndexOf(' ');
        return space < 0 ? (text, "") : (text[..space], text[(space + 1)..]);
    }

    public static CueSheet Parse(string text, string encoding = "UTF-8", string? path = null)
    {
        var sheet = new CueSheet { Path = path, Encoding = encoding };
        CueTrack? track = null;
        var inDataTrack = false;
        var currentFile = "";
        var lines = LineBreak().Split(text);
        if (lines.Length > 0 && lines[^1].Length == 0) lines = lines[..^1];
        for (var i = 0; i < lines.Length; i++)
        {
            var line = lines[i].Trim();
            if (line.Length == 0) continue;
            var (keyword, rest) = Partition(line);
            keyword = keyword.ToUpperInvariant();
            if (keyword == "REM")
            {
                var (key, value) = Partition(rest.Trim());
                key = key.ToUpperInvariant();
                value = Value(value);
                if (track is null && !inDataTrack)
                {
                    if (key == "DATE") sheet.Date = value;
                    else if (key == "GENRE") sheet.Genre = value;
                    else if (key == "DISCNUMBER") sheet.Disc = Number(value);
                    else if (key == "TOTALDISCS") sheet.DiscTotal = Number(value);
                }
            }
            else if (keyword == "FILE")
            {
                currentFile = FileName(rest);
                sheet.Files.Add(currentFile);
            }
            else if (keyword == "TRACK")
            {
                var parts = Words(rest);
                inDataTrack = parts.Length < 2 || parts[1].ToUpperInvariant() != "AUDIO";
                if (inDataTrack)
                {
                    sheet.DataTracks++;
                    track = null;
                    continue;
                }
                if (!IsDigits(parts[0])) throw new CueException($"line {i + 1}: '{line}' has no track number");
                track = new CueTrack(int.Parse(parts[0]), currentFile);
                sheet.Tracks.Add(track);
            }
            else if (inDataTrack)
            {
            }
            else if (keyword is "TITLE" or "PERFORMER" or "SONGWRITER")
            {
                if (track is not null)
                {
                    if (keyword == "TITLE") track.Title = Value(rest);
                    else if (keyword == "PERFORMER") track.Performer = Value(rest);
                    else track.Songwriter = Value(rest);
                }
                else if (keyword == "TITLE") sheet.Title = Value(rest);
                else if (keyword == "PERFORMER") sheet.Performer = Value(rest);
                // an album songwriter has no field of its own
            }
            else if (keyword == "ISRC" && track is not null)
            {
                track.Isrc = Value(rest);
            }
            else if (keyword == "INDEX" && track is not null)
            {
                var parts = Words(rest);
                if (parts.Length != 2 || !IsDigits(parts[0])) throw new CueException($"line {i + 1}: '{line}' isn't an INDEX line");
                var frames = FramesFromTime(parts[1]);
                if (int.Parse(parts[0]) == 0) track.Index00 = frames;
                else if (int.Parse(parts[0]) == 1) track.Index01 = frames;
            }
        }
        foreach (var t in sheet.Tracks)
            if (t.Index01 is null) throw new CueException($"track {t.Number} has no INDEX 01, so its start is unknown");
        return sheet;
    }

    /// <summary>Reads a .cue: a BOM, then UTF-8, then Windows-1252 (SPEC §22's Auto). Throws IOException,
    /// CueException.</summary>
    public static CueSheet Read(string path)
    {
        var (text, name) = Preview.Decode(System.IO.File.ReadAllBytes(path));
        return Parse(text, name, path);
    }

    /// <summary>The folder's .cue files in name order, each read (null when it can't be: Tidy and ReplayGain skip
    /// it).</summary>
    public static List<(string Path, CueSheet? Sheet)> ReadSheets(string folder)
    {
        var sheets = new List<(string, CueSheet?)>();
        foreach (var entry in Directory.EnumerateFiles(folder)
                     .Where(p => System.IO.Path.GetExtension(p).Equals(".cue", StringComparison.OrdinalIgnoreCase))
                     .OrderBy(p => Tags.LyricsKey.CaseFold(System.IO.Path.GetFileName(p)), StringComparer.Ordinal))
        {
            try
            {
                sheets.Add((entry, Read(entry)));
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException or CueException)
            {
                sheets.Add((entry, null));
            }
        }
        return sheets;
    }

    /// <summary>The audio files of a folder that are images waiting to be split, not tracks (SPEC §10 B, §28): those
    /// a single-file sheet names (any format), and the folder's only audio file when such a sheet names another
    /// file. ReplayGain never measures them. <paramref name="audioNames"/>: the folder's audio file names.</summary>
    public static HashSet<string> UnsplitImages(string folder, IEnumerable<string> audioNames)
    {
        var audio = new Dictionary<string, string>();
        foreach (var name in audioNames) audio[Tags.LyricsKey.CaseFold(name)] = name;
        var images = new HashSet<string>();
        if (audio.Count == 0) return images;
        List<(string, CueSheet?)> sheets;
        try
        {
            sheets = ReadSheets(folder);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            return images;
        }
        foreach (var (_, sheet) in sheets)
        {
            var names = sheet is null ? [] : sheet.Files.Select(Tags.LyricsKey.CaseFold).ToHashSet();
            if (names.Count != 1) continue;
            if (audio.TryGetValue(names.First(), out var named)) images.Add(named);
            else if (audio.Count == 1) images.Add(audio.Values.First());
        }
        return images;
    }
}
