namespace Refrain.Core;

/// <summary>The audio formats (SPEC §1): the one list of what counts as audio, and which files have tags read.</summary>
public static class Formats
{
    /// <summary>Every audio extension: listed, searched by name and (later) played. Monkey's Audio (.ape) is a plain
    /// file on purpose (SPEC §1 "Ignored").</summary>
    public static readonly IReadOnlySet<string> Audio = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
    {
        ".flac", ".ogg", ".oga", ".opus", ".spx", ".mp3", ".mp2", ".wav", ".aif", ".aiff", ".aifc", ".m4a", ".mp4",
        ".m4b", ".wv", ".mpc", ".tak", ".ofr", ".ofs", ".tta", ".wma", ".dsf", ".dff", ".aac",
    };

    /// <summary>Audio the tag library can't read (SPEC §36 "4."): Ogg FLAC and True Audio are listed and found by
    /// name only, without tag columns.</summary>
    public static readonly IReadOnlySet<string> NameOnly = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
    {
        ".oga", ".tta",
    };

    public static bool IsAudio(string? ext) => ext is not null && Audio.Contains(ext);

    /// <summary>Whether the indexer reads this file's tags and stream info.</summary>
    public static bool IsRead(string? ext) => IsAudio(ext) && !NameOnly.Contains(ext!);

    /// <summary>The Format column's text: the extension without its dot, in capitals ("FLAC").</summary>
    public static string Label(string ext) => ext.TrimStart('.').ToUpperInvariant();
}
