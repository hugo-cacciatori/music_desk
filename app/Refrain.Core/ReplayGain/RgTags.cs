using TagLib;
using Refrain.Core.Tags;

namespace Refrain.Core.ReplayGain;

/// <summary>A file's ReplayGain values and comment, as the Python app's tag adapters handle them (SPEC §1): keys
/// match ignoring case, a write replaces every case variant and is in uppercase, values are kept as the exact text
/// given (TagLib#'s own ReplayGain properties would reformat them). Only the format's own tag family is touched.
/// Errors are the file's (IOException, TagLib's), for the caller to record.</summary>
public sealed class RgTags : IDisposable
{
    public const string TrackGain = "replaygain_track_gain", TrackPeak = "replaygain_track_peak",
        AlbumGain = "replaygain_album_gain", AlbumPeak = "replaygain_album_peak";
    const string Prefix = "replaygain_";  // every ReplayGain tag name starts so, ignoring case
    const string ITunes = "com.apple.iTunes";

    readonly TagLib.File file;
    readonly TagTypes family;

    RgTags(TagLib.File file)
    {
        this.file = file;
        family = TagFile.MainTag(file);
    }

    public static RgTags Open(string path) => new(TagLib.File.Create(path));

    /// <summary>Whether the format holds tags at all (raw AAC doesn't, SPEC §1).</summary>
    public static bool Taggable(string path) =>
        !Path.GetExtension(path).Equals(".aac", StringComparison.OrdinalIgnoreCase);

    /// <summary>Whether Refrain can write this format's tags (TagLib# opens it).</summary>
    public static bool Writable(string ext) =>
        SupportedMimeType.AllExtensions.Contains(ext.TrimStart('.').ToLowerInvariant()) && !Formats.NameOnly.Contains(ext);

    Tag? Existing => file.GetTag(family, false);
    Tag Created => file.GetTag(family, true);

    static bool Same(string? a, string b) => string.Equals(a, b, StringComparison.OrdinalIgnoreCase);

    /// <summary>A ReplayGain value such as "replaygain_track_gain", or null.</summary>
    public string? Get(string key) => Existing switch
    {
        TagLib.Ogg.XiphComment xiph => xiph.GetField(key) is [var first, ..] ? first : null,
        TagLib.Id3v2.Tag id3 => id3.GetFrames<TagLib.Id3v2.UserTextInformationFrame>()
            .FirstOrDefault(f => Same(f.Description, key)) is { } frame ? frame.Text[0] : null,
        TagLib.Mpeg4.AppleTag apple => DashNames(apple).FirstOrDefault(n => Same(n.Mean, ITunes) && Same(n.Name, key)) is { Mean: not null } d
            ? apple.GetDashBox(d.Mean, d.Name) : null,
        TagLib.Ape.Tag ape => ape.FirstOrDefault(k => Same(k, key)) is { } k2 ? ape.GetItem(k2).ToString() : null,
        TagLib.Asf.Tag asf => asf.FirstOrDefault(d => Same(d.Name, key)) is { } descriptor ? descriptor.ToString() : null,
        _ => null,
    };

    public void Set(string key, string value)
    {
        var upper = key.ToUpperInvariant();
        switch (Created)
        {
            case TagLib.Ogg.XiphComment xiph:
                xiph.SetField(upper, value);  // its names are kept in capitals: every case variant goes
                break;
            case TagLib.Id3v2.Tag id3:
                foreach (var frame in id3.GetFrames<TagLib.Id3v2.UserTextInformationFrame>().Where(f => Same(f.Description, key)).ToList())
                    id3.RemoveFrame(frame);
                id3.AddFrame(new TagLib.Id3v2.UserTextInformationFrame(upper, StringType.UTF8) { Text = [value] });
                break;
            case TagLib.Mpeg4.AppleTag apple:
                foreach (var (mean, name) in DashNames(apple).Where(n => Same(n.Mean, ITunes) && Same(n.Name, key)).ToList())
                    apple.SetDashBox(mean!, name, null);
                apple.SetDashBox(ITunes, upper, value);
                break;
            case TagLib.Ape.Tag ape:
                foreach (var existing in ape.Where(k => Same(k, key)).ToList()) ape.RemoveItem(existing);
                ape.SetValue(upper, value);
                break;
            case TagLib.Asf.Tag asf:
                foreach (var name in asf.Where(d => Same(d.Name, key)).Select(d => d.Name).Distinct().ToList())
                    asf.RemoveDescriptors(name);
                asf.SetDescriptorString(value, upper);
                break;
            default:
                throw new NotSupportedException($"{Path.GetFileName(file.Name)} has no tags Refrain can write.");
        }
    }

    /// <summary>Deletes every REPLAYGAIN_* value (rsgain's four, and any other a tool wrote); returns how many
    /// went.</summary>
    public int RemoveAll()
    {
        switch (Existing)
        {
            case TagLib.Ogg.XiphComment xiph:
            {
                var names = xiph.Where(n => n.StartsWith(Prefix, StringComparison.OrdinalIgnoreCase)).ToList();
                foreach (var name in names) xiph.RemoveField(name);
                return names.Count;
            }
            case TagLib.Id3v2.Tag id3:
            {
                var frames = id3.GetFrames<TagLib.Id3v2.UserTextInformationFrame>()
                    .Where(f => f.Description?.StartsWith(Prefix, StringComparison.OrdinalIgnoreCase) == true).ToList();
                foreach (var frame in frames) id3.RemoveFrame(frame);
                return frames.Count;
            }
            case TagLib.Mpeg4.AppleTag apple:
            {
                var names = DashNames(apple).Where(n => n.Mean == ITunes && n.Name.StartsWith(Prefix, StringComparison.OrdinalIgnoreCase)).ToList();
                foreach (var (mean, name) in names) apple.SetDashBox(mean!, name, null);
                return names.Count;
            }
            case TagLib.Ape.Tag ape:
            {
                var keys = ape.Where(k => k.StartsWith(Prefix, StringComparison.OrdinalIgnoreCase)).ToList();
                foreach (var key in keys) ape.RemoveItem(key);
                return keys.Count;
            }
            case TagLib.Asf.Tag asf:
            {
                var names = asf.Where(d => d.Name.StartsWith(Prefix, StringComparison.OrdinalIgnoreCase)).Select(d => d.Name).ToList();
                foreach (var name in names.Distinct()) asf.RemoveDescriptors(name);
                return names.Count;
            }
            default:
                return 0;
        }
    }

    /// <summary>The comment, as the Metadata tab reads it: ID3's COMM without a description (English first).</summary>
    public string? Comment => Existing switch
    {
        TagLib.Ogg.XiphComment xiph => xiph.GetField("COMMENT") is [var first, ..] ? first : null,
        TagLib.Id3v2.Tag id3 => id3.GetFrames<TagLib.Id3v2.CommentsFrame>().Where(f => f.Description == "")
            .OrderBy(f => f.Language != "eng").FirstOrDefault() is { } frame ? frame.Text : null,
        TagLib.Asf.Tag asf => asf.Description,
        { } tag => tag.Comment,
        _ => null,
    } is { Length: > 0 } text ? text : null;

    /// <summary>Replaces the comment; null removes it.</summary>
    public void SetComment(string? text)
    {
        switch (Created)
        {
            case TagLib.Ogg.XiphComment xiph:
                if (text is null) xiph.RemoveField("COMMENT");
                else xiph.SetField("COMMENT", text);
                break;
            case TagLib.Id3v2.Tag id3:
                // A "comment" TXXX was written by older versions of the script.
                foreach (var frame in id3.GetFrames<TagLib.Id3v2.UserTextInformationFrame>().Where(f => Same(f.Description, "comment")).ToList())
                    id3.RemoveFrame(frame);
                foreach (var frame in id3.GetFrames<TagLib.Id3v2.CommentsFrame>().Where(f => f.Description == "").ToList())
                    id3.RemoveFrame(frame);
                if (text is not null) id3.AddFrame(new TagLib.Id3v2.CommentsFrame("", "eng", StringType.UTF8) { Text = text });
                break;
            case TagLib.Asf.Tag asf:
                asf.Description = text;  // the content description's, as SPEC §1 (TagLib#'s Comment is WM/Text)
                break;
            case { } tag:
                tag.Comment = text;
                break;
        }
    }

    /// <summary>Writes the changes, keeping only the tag types on disk (and the format's own).</summary>
    public void Save()
    {
        TagFile.KeepTagsOnDisk(file);
        file.Save();
    }

    public void Dispose() => file.Dispose();

    /// <summary>The freeform ("----") items: their mean and name.</summary>
    static List<(string? Mean, string Name)> DashNames(TagLib.Mpeg4.AppleTag apple)
    {
        var names = new List<(string?, string)>();
        foreach (var box in apple)
        {
            if (box.BoxType != (ByteVector)"----" || box.Children is null) continue;
            string? mean = null, name = null;
            foreach (var child in box.Children)
                if (child is TagLib.Mpeg4.AppleAdditionalInfoBox info)
                {
                    if (child.BoxType == (ByteVector)"mean") mean = info.Text;
                    else if (child.BoxType == (ByteVector)"name") name = info.Text;
                }
            if (name is not null) names.Add((mean, name));
        }
        return names;
    }
}
