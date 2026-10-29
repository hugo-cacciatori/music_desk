using System.Globalization;
using TagLib;

namespace Refrain.Core.Tags;

/// <summary>The Metadata tab's fields (SPEC §7, §36 "5."), in their order on the tab.</summary>
public enum Field { Title, Artist, AlbumArtist, Album, Track, TrackTotal, Disc, DiscTotal, Year, Genre, Composer, Comment }

/// <summary>An embedded front cover: its bytes and type.</summary>
public sealed record CoverImage(byte[] Data, string Mime);

/// <summary>What the details block shows of a file (read-only).</summary>
public sealed record FileDetails(
    string FormatLine, long Size, string? Codec, double? Duration, int? Bitrate, int? SampleRate, int? BitDepth,
    int? Channels, string? TrackGain, string? TrackPeak, string? AlbumGain, string? AlbumPeak);

/// <summary>An audio file's editable tags, its embedded front cover and its details. <see cref="ReadOnly"/> says why
/// it can't be edited, when it can't.</summary>
public sealed record FileTags(string Path, IReadOnlyDictionary<Field, string> Values, CoverImage? Cover,
    FileDetails Details, string? ReadOnly)
{
    public bool NoTrackTotal => System.IO.Path.GetExtension(Path).Equals(".wma", StringComparison.OrdinalIgnoreCase);
}

/// <summary>A tag save that failed, with a message for the user.</summary>
public sealed class TagSaveException(string message, Exception? inner = null) : Exception(message, inner);

/// <summary>Reads and writes the Metadata tab's fields and the front cover through TagLib#. Reads never block a
/// rename or delete; a save writes only the given fields and leaves every other tag, frame and picture alone.</summary>
public static class TagFile
{
    public static readonly IReadOnlySet<Field> Numbers = new HashSet<Field>
        { Field.Track, Field.TrackTotal, Field.Disc, Field.DiscTotal };

    static readonly HashSet<string> Lossless = new(StringComparer.OrdinalIgnoreCase)
        { ".flac", ".wav", ".aif", ".aiff", ".aifc", ".wv", ".tak", ".ofr", ".ofs", ".tta", ".dsf", ".dff" };

    static readonly ByteVector AppleDay = new([0xA9, (byte)'d', (byte)'a', (byte)'y']);  // ©day

    /// <summary>Throws IOException or a TagLib error when the file can't be read.</summary>
    public static FileTags Read(string path)
    {
        var size = new FileInfo(path).Length;
        var ext = System.IO.Path.GetExtension(path);
        using var file = TagLib.File.Create(new SharedRead(path), ReadStyle.Average);
        var tag = file.Tag;
        // TagLib# makes an ID3v2 tag in memory for an MP3 without one, holding ID3v1's values as Unicode: only the
        // tags on disk say how the text was stored.
        var id3 = file.TagTypesOnDisk.HasFlag(TagTypes.Id3v2) ? file.GetTag(TagTypes.Id3v2) as TagLib.Id3v2.Tag : null;
        var id3v1 = file.TagTypesOnDisk.HasFlag(TagTypes.Id3v1);

        // ID3 text marked Latin-1, and ID3v1's, is read as SPEC §27 says.
        bool Latin1(string frame) => id3 is not null && TagLib.Id3v2.TextInformationFrame.Get(id3, frame, false) is { } f
            ? f.TextEncoding == StringType.Latin1
            : id3v1;
        string Text(string? value, string frame) =>
            string.IsNullOrEmpty(value) ? "" : Latin1(frame) ? LegacyText.Fix(value) : value;
        var commentLatin1 = id3 is not null
            ? TagLib.Id3v2.CommentsFrame.GetPreferred(id3, "", "eng") is { } c ? c.TextEncoding == StringType.Latin1 : id3v1
            : id3v1;

        var values = new Dictionary<Field, string>
        {
            [Field.Title] = Text(tag.Title, "TIT2"),
            [Field.Artist] = Text(tag.JoinedPerformers, "TPE1"),
            [Field.AlbumArtist] = Text(tag.JoinedAlbumArtists, "TPE2"),
            [Field.Album] = Text(tag.Album, "TALB"),
            [Field.Track] = tag.Track > 0 ? tag.Track.ToString(CultureInfo.InvariantCulture) : "",
            [Field.TrackTotal] = tag.TrackCount > 0 ? tag.TrackCount.ToString(CultureInfo.InvariantCulture) : "",
            [Field.Disc] = tag.Disc > 0 ? tag.Disc.ToString(CultureInfo.InvariantCulture) : "",
            [Field.DiscTotal] = tag.DiscCount > 0 ? tag.DiscCount.ToString(CultureInfo.InvariantCulture) : "",
            [Field.Year] = Date(file) ?? "",
            [Field.Genre] = Text(tag.JoinedGenres, "TCON"),
            [Field.Composer] = Text(tag.JoinedComposers, "TCOM"),
            [Field.Comment] = Comment(file) is not { Length: > 0 } comment ? "" : commentLatin1 ? LegacyText.Fix(comment) : comment,
        };
        var front = FrontCovers(file).FirstOrDefault();
        var props = file.Properties;
        var codec = props?.Codecs.FirstOrDefault(c => c is not null)?.Description;
        var lossless = Lossless.Contains(ext) || codec?.Contains("ALAC", StringComparison.OrdinalIgnoreCase) == true ||
                       codec?.Contains("FLAC", StringComparison.OrdinalIgnoreCase) == true;
        var details = new FileDetails(
            FormatLine(ext, lossless, props?.BitsPerSample, props?.AudioBitrate, props?.AudioSampleRate,
                props?.Duration.TotalSeconds, size),
            size, codec,
            props is null || props.Duration <= TimeSpan.Zero ? null : props.Duration.TotalSeconds,
            props?.AudioBitrate > 0 ? props.AudioBitrate : null,
            props?.AudioSampleRate > 0 ? props.AudioSampleRate : null,
            lossless && props?.BitsPerSample > 0 ? props.BitsPerSample : null,
            props?.AudioChannels > 0 ? props.AudioChannels : null,
            Gain(tag.ReplayGainTrackGain), Peak(tag.ReplayGainTrackPeak),
            Gain(tag.ReplayGainAlbumGain), Peak(tag.ReplayGainAlbumPeak));
        var readOnly = ext.Equals(".aac", StringComparison.OrdinalIgnoreCase)
            ? "Raw AAC has no tag standard, so it can't be tagged."
            : null;
        return new FileTags(path, values,
            front is null ? null : new CoverImage(front.Data.Data, front.MimeType ?? "image/jpeg"), details, readOnly);
    }

    /// <summary>e.g. "FLAC 16-bit/44.1 kHz · 4:44 · 32 MB" or "MP3 320 kbps/44.1 kHz · 3:12 · 7.4 MB": the bit depth
    /// for lossless audio, the bitrate for lossy.</summary>
    public static string FormatLine(string ext, bool lossless, int? bits, int? kbps, int? sampleRate, double? seconds,
        long size)
    {
        var quality = new List<string>();
        if (lossless && bits > 0) quality.Add($"{bits}-bit");
        else if (!lossless && kbps > 0) quality.Add($"{kbps} kbps");
        if (sampleRate > 0) quality.Add($"{sampleRate / 1000.0:0.###} kHz");
        var head = Formats.Label(ext);
        var line = new List<string> { quality.Count > 0 ? $"{head} {string.Join("/", quality)}" : head };
        if (seconds > 0) line.Add(Browser.Columns.Duration(seconds));
        line.Add(Browser.Columns.Size(size));
        return string.Join(" · ", line);
    }

    internal static string? Gain(double value) =>
        double.IsNaN(value) ? null : value.ToString("0.00", CultureInfo.InvariantCulture) + " dB";

    internal static string? Peak(double value) =>
        double.IsNaN(value) ? null : value.ToString("0.000000", CultureInfo.InvariantCulture);

    /// <summary>The date as tagged ("1997-05-21"), where the tag family keeps it as text; else the year.</summary>
    internal static string? Date(TagLib.File file)
    {
        string? raw = null;
        if (file.GetTag(TagTypes.Xiph) is TagLib.Ogg.XiphComment xiph) raw = xiph.GetFirstField("DATE");
        else if (file.GetTag(TagTypes.Id3v2) is TagLib.Id3v2.Tag id3) raw = id3.GetTextAsString("TDRC");
        else if (file.GetTag(TagTypes.Apple) is TagLib.Mpeg4.AppleTag apple) raw = apple.GetText(AppleDay).FirstOrDefault();
        else if (file.GetTag(TagTypes.Ape) is TagLib.Ape.Tag ape) raw = ape.GetItem("Year")?.ToString();
        else if (file.GetTag(TagTypes.Asf) is TagLib.Asf.Tag asf) raw = asf.GetDescriptorString("WM/Year");
        if (!string.IsNullOrWhiteSpace(raw)) return raw.Trim();
        return file.Tag.Year > 0 ? file.Tag.Year.ToString(CultureInfo.InvariantCulture) : null;
    }

    /// <summary>WMA's comment is the content description's (SPEC §1); TagLib#'s Comment there is WM/Text.</summary>
    static string? Comment(TagLib.File file) =>
        file.GetTag(TagTypes.Asf) is TagLib.Asf.Tag asf ? asf.Description : file.Tag.Comment;

    /// <summary>The front covers: pictures of that type, or every MP4 cover (MP4 pictures have no type).</summary>
    static List<IPicture> FrontCovers(TagLib.File file) =>
        file.Tag.Pictures.Where(p => file.TagTypesOnDisk.HasFlag(TagTypes.Apple) || p.Type == PictureType.FrontCover)
            .ToList();

    /// <summary>What a cover change does: a new front cover, or none.</summary>
    public sealed record CoverChange(CoverImage? Image);

    /// <summary>Writes the given fields (an empty value clears one) and the cover change, and nothing else. Throws
    /// <see cref="TagSaveException"/> with a plain message.</summary>
    public static void Save(string path, IReadOnlyDictionary<Field, string> changes, CoverChange? cover)
    {
        if (changes.Count == 0 && cover is null) return;
        foreach (var (field, value) in changes)
            if (Numbers.Contains(field) && value.Trim().Length > 0 && !uint.TryParse(value.Trim(), out _))
                throw new TagSaveException($"{Label(field)} must be a whole number.");
        TagLib.File file;
        try
        {
            file = TagLib.File.Create(path);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            throw new TagSaveException($"{System.IO.Path.GetFileName(path)} can't be opened for writing: {e.Message}", e);
        }
        catch (Exception e) when (e is UnsupportedFormatException or CorruptFileException)
        {
            throw new TagSaveException($"{System.IO.Path.GetFileName(path)} can't be tagged: {e.Message}", e);
        }
        using (file)
        {
            KeepTagsOnDisk(file);
            var tag = file.Tag;
            foreach (var (field, raw) in changes)
            {
                var value = raw.Trim().Length == 0 ? null : raw;
                var number = value is null ? 0u : Numbers.Contains(field) ? uint.Parse(value.Trim(), CultureInfo.InvariantCulture) : 0u;
                switch (field)
                {
                    case Field.Title: tag.Title = value; break;
                    case Field.Artist: tag.Performers = value is null ? [] : [value]; break;
                    case Field.AlbumArtist: tag.AlbumArtists = value is null ? [] : [value]; break;
                    case Field.Album: tag.Album = value; break;
                    case Field.Track: tag.Track = number; break;
                    case Field.TrackTotal: tag.TrackCount = number; break;
                    case Field.Disc: tag.Disc = number; break;
                    case Field.DiscTotal: tag.DiscCount = number; break;
                    case Field.Year: SetDate(file, value); break;
                    case Field.Genre: tag.Genres = value is null ? [] : [value]; break;
                    case Field.Composer: tag.Composers = value is null ? [] : [value]; break;
                    case Field.Comment when file.GetTag(TagTypes.Asf) is TagLib.Asf.Tag asf: asf.Description = value; break;
                    case Field.Comment: tag.Comment = value; break;
                }
            }
            if (cover is not null)
            {
                var front = FrontCovers(file);
                var pictures = tag.Pictures.Where(p => !front.Contains(p)).ToList();
                if (cover.Image is { } image)
                    pictures.Insert(0, new Picture(new ByteVector(image.Data))
                    {
                        Type = PictureType.FrontCover,
                        MimeType = image.Mime,
                        Description = "",
                    });
                tag.Pictures = [.. pictures];
            }
            try
            {
                file.Save();
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException or NotImplementedException)
            {
                throw new TagSaveException($"{System.IO.Path.GetFileName(path)} couldn't be saved: {e.Message}", e);
            }
        }
    }

    /// <summary>TagLib# adds tag types a format may hold (an ID3v1 tag to every MP3); only the ones on disk are kept,
    /// plus the format's main one when the file has none yet.</summary>
    internal static void KeepTagsOnDisk(TagLib.File file)
    {
        var keep = file.TagTypesOnDisk;
        var main = MainTag(file);
        if ((keep & main) == 0)
        {
            keep |= main;
            file.GetTag(main, true);
        }
        file.RemoveTags(file.TagTypes & ~keep);
    }

    /// <summary>The format's own tag family (SPEC §1): the one the Python app reads and writes.</summary>
    internal static TagTypes MainTag(TagLib.File file) => file switch
    {
        TagLib.Flac.File or TagLib.Ogg.File => TagTypes.Xiph,
        TagLib.Mpeg4.File => TagTypes.Apple,
        TagLib.Asf.File => TagTypes.Asf,
        TagLib.WavPack.File or TagLib.MusePack.File => TagTypes.Ape,
        _ => TagTypes.Id3v2,
    };

    /// <summary>The date as typed, where the tag family keeps text ("1997-05-21"); else its year.</summary>
    static void SetDate(TagLib.File file, string? value)
    {
        var written = false;
        if (file.GetTag(TagTypes.Xiph) is TagLib.Ogg.XiphComment xiph)
        {
            xiph.SetField("DATE", value is null ? [] : [value]);
            written = true;
        }
        if (file.GetTag(TagTypes.Id3v2) is TagLib.Id3v2.Tag id3)
        {
            id3.SetTextFrame("TDRC", value);
            written = true;
        }
        if (file.GetTag(TagTypes.Apple) is TagLib.Mpeg4.AppleTag apple)
        {
            apple.SetText(AppleDay, value);
            written = true;
        }
        if (file.GetTag(TagTypes.Ape) is TagLib.Ape.Tag ape)
        {
            ape.SetValue("Year", value);
            written = true;
        }
        if (file.GetTag(TagTypes.Asf) is TagLib.Asf.Tag asf)
        {
            asf.SetDescriptorString(value, "WM/Year");
            written = true;
        }
        if (!written)
            file.Tag.Year = value is not null && value.Length >= 4 && uint.TryParse(value[..4], out var year) ? year : 0;
    }

    public static string Label(Field field) => field switch
    {
        Field.AlbumArtist => "Album artist",
        Field.Track => "Track #",
        Field.TrackTotal => "Track total",
        Field.Disc => "Disc #",
        Field.DiscTotal => "Disc total",
        _ => field.ToString(),
    };

    /// <summary>A JPEG or PNG, by its signature; null for anything else.</summary>
    public static string? ImageType(byte[] data) =>
        data.Length > 3 && data[0] == 0xFF && data[1] == 0xD8 && data[2] == 0xFF ? "image/jpeg"
        : data.Length > 8 && data[0] == 0x89 && data[1] == (byte)'P' && data[2] == (byte)'N' && data[3] == (byte)'G' ? "image/png"
        : null;
}

/// <summary>A read that never blocks a rename or delete of the file (SPEC §7).</summary>
sealed class SharedRead(string path) : TagLib.File.IFileAbstraction
{
    public string Name => path;
    public Stream ReadStream => new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
    public Stream WriteStream => throw new NotSupportedException("This read never writes.");
    public void CloseStream(Stream stream) => stream.Dispose();
}
