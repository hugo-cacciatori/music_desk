namespace Refrain.Core.Tags;

/// <summary>What the index keeps of an audio file's tags and stream (a <c>tracks</c> row).</summary>
public sealed record TrackInfo
{
    public string? Format { get; init; }
    public string? Title { get; init; }
    public string? Artist { get; init; }
    public string? AlbumArtist { get; init; }
    public string? Album { get; init; }
    public long? Track { get; init; }
    public long? TrackTotal { get; init; }
    public long? Disc { get; init; }
    public long? DiscTotal { get; init; }
    public string? Date { get; init; }
    public string? Genre { get; init; }
    public bool HasCover { get; init; }
    public double? Duration { get; init; }
    public long? Bitrate { get; init; }
    public long? SampleRate { get; init; }
    public long? Channels { get; init; }
    public string? RgTrackGain { get; init; }
    public string? RgTrackPeak { get; init; }
    public string? RgAlbumGain { get; init; }
    public string? RgAlbumPeak { get; init; }
    public string? LyricsKey { get; init; }
    public long? ReadSize { get; init; }
    public double? ReadMtime { get; init; }
    public string? ReadError { get; init; }
}
