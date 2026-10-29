namespace Refrain.Core.Index;

/// <summary>The RG column's states (SPEC §5): ✓ track and album, ◐ partial, – none.</summary>
public enum ReplayGainState { None, Partial, Full }

/// <summary>One row of the file list: a folder or a file, with its tag and health columns (SPEC §5).</summary>
public sealed record FileRow
{
    public required long Id { get; init; }
    public required string Name { get; init; }
    public required bool IsDir { get; init; }
    public string? Ext { get; init; }
    public long? Size { get; init; }
    public double? Mtime { get; init; }
    public bool IsAudio => !IsDir && Formats.IsAudio(Ext);
    public bool IsRead { get; init; }  // its tags were read (a tracks row)
    public long? Track { get; init; }
    public string? Title { get; init; }
    public string? Artist { get; init; }
    public string? Album { get; init; }
    public string? Year { get; init; }
    public double? Duration { get; init; }  // a folder: its subtree's total
    public string? Format { get; init; }
    public long? Bitrate { get; init; }
    public ReplayGainState? ReplayGain { get; init; }
    public string? Lrclib { get; init; }  // "synced", "plain", "instrumental", "not found", "pending", "no tags", ""
    public string? ReadError { get; init; }
    public long? TrackCount { get; init; }  // a folder: tracks in its subtree
    public string? LyricsKey { get; init; }

    // Search results only (SPEC §6).
    public long FolderId { get; init; }
    public string Folder { get; init; } = "";
    public IReadOnlyList<(int Start, int Length)> TitleSpans { get; init; } = [];
    public IReadOnlyList<(int Start, int Length)> ArtistSpans { get; init; } = [];
    public string Extract { get; init; } = "";  // Lyrics search: the matching lyric line and its neighbours
    public IReadOnlyList<(int Start, int Length)> ExtractSpans { get; init; } = [];

    /// <summary>What a screen reader says for a list item: its name, not every field.</summary>
    public override string ToString() => Name;
}

/// <summary>A folder of the tree: its name, and whether it has subfolders to expand.</summary>
public sealed record FolderNode(long Id, string Name, bool HasSubfolders, long TrackCount);
