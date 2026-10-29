using System.Globalization;
using TagLib;

namespace Refrain.Core.Tags;

/// <summary>Reads an audio file's tags and stream info for the index (SPEC §4 phase B). Never throws: an unreadable
/// file is indexed with its error.</summary>
public static class TagReader
{
    public static TrackInfo Read(string path, long? size, double? mtime)
    {
        var info = new TrackInfo { Format = Formats.Label(System.IO.Path.GetExtension(path)), ReadSize = size, ReadMtime = mtime };
        try
        {
            using var file = TagLib.File.Create(new SharedRead(path), ReadStyle.Average);
            var tag = file.Tag;
            var props = file.Properties;
            var title = Text(tag.Title);
            var artist = Text(tag.JoinedPerformers);
            var albumArtist = Text(tag.JoinedAlbumArtists);
            return info with
            {
                Title = title,
                Artist = artist,
                AlbumArtist = albumArtist,
                Album = Text(tag.Album),
                Track = tag.Track > 0 ? tag.Track : null,
                TrackTotal = tag.TrackCount > 0 ? tag.TrackCount : null,
                Disc = tag.Disc > 0 ? tag.Disc : null,
                DiscTotal = tag.DiscCount > 0 ? tag.DiscCount : null,
                Date = Date(file),
                Genre = Text(tag.JoinedGenres),
                HasCover = tag.Pictures.Length > 0,
                Duration = props is null || props.Duration <= TimeSpan.Zero ? null : props.Duration.TotalSeconds,
                Bitrate = props?.AudioBitrate > 0 ? props.AudioBitrate : null,
                SampleRate = props?.AudioSampleRate > 0 ? props.AudioSampleRate : null,
                Channels = props?.AudioChannels > 0 ? props.AudioChannels : null,
                RgTrackGain = Gain(tag.ReplayGainTrackGain),
                RgTrackPeak = Peak(tag.ReplayGainTrackPeak),
                RgAlbumGain = Gain(tag.ReplayGainAlbumGain),
                RgAlbumPeak = Peak(tag.ReplayGainAlbumPeak),
                LyricsKey = LyricsKey.Of(artist, albumArtist, title, System.IO.Path.GetFileName(path)),
            };
        }
        catch (Exception e)
        {
            return info with { ReadError = $"{e.GetType().Name}: {e.Message}" };
        }
    }

    static readonly ByteVector AppleDay = new([0xA9, (byte)'d', (byte)'a', (byte)'y']);  // ©day

    static string? Text(string? value) => string.IsNullOrWhiteSpace(value) ? null : value.Trim();

    static string? Gain(double value) =>
        double.IsNaN(value) ? null : value.ToString("0.00", CultureInfo.InvariantCulture) + " dB";

    static string? Peak(double value) =>
        double.IsNaN(value) ? null : value.ToString("0.000000", CultureInfo.InvariantCulture);

    /// <summary>The date as tagged ("1997-05-21"), where the tag family keeps it as text; else the year.</summary>
    static string? Date(TagLib.File file)
    {
        string? raw = null;
        if (file.GetTag(TagTypes.Xiph) is TagLib.Ogg.XiphComment xiph) raw = xiph.GetFirstField("DATE");
        else if (file.GetTag(TagTypes.Id3v2) is TagLib.Id3v2.Tag id3) raw = id3.GetTextAsString("TDRC");
        else if (file.GetTag(TagTypes.Apple) is TagLib.Mpeg4.AppleTag apple) raw = apple.GetText(AppleDay).FirstOrDefault();
        else if (file.GetTag(TagTypes.Ape) is TagLib.Ape.Tag ape) raw = ape.GetItem("Year")?.ToString();
        else if (file.GetTag(TagTypes.Asf) is TagLib.Asf.Tag asf) raw = asf.GetDescriptorString("WM/Year");
        return Text(raw) ?? (file.Tag.Year > 0 ? file.Tag.Year.ToString(CultureInfo.InvariantCulture) : null);
    }

    /// <summary>A read that never blocks a rename or delete of the file (SPEC §7).</summary>
    sealed class SharedRead(string path) : TagLib.File.IFileAbstraction
    {
        public string Name => path;
        public Stream ReadStream => new FileStream(path, FileMode.Open, FileAccess.Read,
            FileShare.ReadWrite | FileShare.Delete);
        public Stream WriteStream => throw new NotSupportedException("The index only reads tags.");
        public void CloseStream(Stream stream) => stream.Dispose();
    }
}
