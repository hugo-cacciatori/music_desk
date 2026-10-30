using System.Security.Cryptography;
using System.Text;
using Refrain.Core;
using Refrain.Core.ReplayGain;
using Refrain.Core.Tags;

namespace Refrain.Tests;

/// <summary>A tree as Refrain reads it, as text (SPEC §36 "10." step c): what the tools' parity tests record of the C#
/// app's files once the Python app proved them right, and compare with afterwards. Each file in name order: an audio
/// file's tag families on disk, its fields, ReplayGain values and comment, every Vorbis comment or ID3 frame, its pictures;
/// a replaygain.log's text with its times masked; any other file's SHA-1.</summary>
static class TagDump
{
    public static string Of(string root)
    {
        var text = new StringBuilder();
        foreach (var path in Directory.GetFiles(root, "*", SearchOption.AllDirectories)
                     .Select(p => Path.GetRelativePath(root, p)).Order(StringComparer.Ordinal))
        {
            var full = Path.Combine(root, path);
            text.Append("== ").Append(path).Append('\n');
            if (Path.GetFileName(path) == "replaygain.log")
                text.Append(ReplayGainTests.Mask(File.ReadAllText(full), root)).Append('\n');
            else if (Formats.IsAudio(Path.GetExtension(path)) && RgTags.Writable(Path.GetExtension(path)))
                Audio(full, text);
            else
                text.Append("sha1 ").Append(Convert.ToHexStringLower(SHA1.HashData(File.ReadAllBytes(full)))).Append('\n');
        }
        return text.ToString();
    }

    static void Audio(string path, StringBuilder text)
    {
        var read = TagFile.Read(path);
        using var file = TagLib.File.Create(new SharedRead(path), TagLib.ReadStyle.None);
        text.Append("tags ").Append(file.TagTypesOnDisk).Append('\n');
        foreach (var (field, value) in read.Values.OrderBy(kv => kv.Key)) text.Append(field).Append(" = ").Append(value).Append('\n');
        using (var rg = RgTags.Open(path))
        {
            foreach (var key in new[] { RgTags.TrackGain, RgTags.TrackPeak, RgTags.AlbumGain, RgTags.AlbumPeak })
                text.Append(key).Append(" = ").Append(rg.Get(key) ?? "-").Append('\n');
            text.Append("rg comment = ").Append(rg.Comment ?? "-").Append('\n');
        }
        var lines = new List<string>();
        if (file.GetTag(TagLib.TagTypes.Xiph, false) is TagLib.Ogg.XiphComment xiph)
            lines.AddRange(xiph.Select(name => $"vorbis {name} = {string.Join(" | ", xiph.GetField(name))}"));
        if (file.GetTag(TagLib.TagTypes.Id3v2, false) is TagLib.Id3v2.Tag id3)
            lines.AddRange(id3.GetFrames().Select(f => $"id3v2.{id3.Version} {f.FrameId} {f}"));
        lines.AddRange(file.Tag.Pictures.Select(p =>
            $"picture {p.Type} {p.MimeType} {p.Description} {Convert.ToHexStringLower(SHA1.HashData(p.Data.Data))}"));
        foreach (var line in lines.Order(StringComparer.Ordinal)) text.Append(line).Append('\n');
    }
}
