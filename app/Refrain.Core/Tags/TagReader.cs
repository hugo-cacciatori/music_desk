namespace Refrain.Core.Tags;

/// <summary>Reads an audio file's tags and stream info for the index (SPEC §4 phase B), through the same read as the
/// Metadata tab. Never throws: an unreadable file is indexed with its error.</summary>
public static class TagReader
{
    public static TrackInfo Read(string path, long? size, double? mtime)
    {
        var info = new TrackInfo { Format = Formats.Label(Path.GetExtension(path)), ReadSize = size, ReadMtime = mtime };
        try
        {
            var tags = TagFile.Read(path);
            var v = tags.Values;
            var d = tags.Details;
            string? Text(Field field) => v[field].Trim().Length == 0 ? null : v[field].Trim();
            long? Number(Field field) => long.TryParse(v[field], out var n) && n > 0 ? n : null;
            return info with
            {
                Title = Text(Field.Title),
                Artist = Text(Field.Artist),
                AlbumArtist = Text(Field.AlbumArtist),
                Album = Text(Field.Album),
                Track = Number(Field.Track),
                TrackTotal = Number(Field.TrackTotal),
                Disc = Number(Field.Disc),
                DiscTotal = Number(Field.DiscTotal),
                Date = Text(Field.Year),
                Genre = Text(Field.Genre),
                HasCover = tags.Cover is not null,
                Duration = d.Duration,
                Bitrate = d.Bitrate,
                SampleRate = d.SampleRate,
                Channels = d.Channels,
                RgTrackGain = d.TrackGain,
                RgTrackPeak = d.TrackPeak,
                RgAlbumGain = d.AlbumGain,
                RgAlbumPeak = d.AlbumPeak,
                LyricsKey = LyricsKey.Of(Text(Field.Artist), Text(Field.AlbumArtist), Text(Field.Title), Path.GetFileName(path)),
            };
        }
        catch (Exception e)
        {
            return info with { ReadError = $"{e.GetType().Name}: {e.Message}" };
        }
    }
}
