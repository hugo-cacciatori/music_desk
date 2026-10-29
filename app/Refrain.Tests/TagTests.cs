using System.Text;
using Refrain.Core;
using Refrain.Core.Tags;
using Xunit;

namespace Refrain.Tests;

/// <summary>Tag reading and saving (SPEC §7, §36 "5."), the edit rules for one file and several, and legacy ID3 text
/// (SPEC §27, ported from the Python app's tests/core/test_tag_encodings.py). Always on temporary copies.</summary>
public sealed class TagTests : IDisposable
{
    readonly TempFolder temp = new();

    static TagTests() => Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);

    public void Dispose() => temp.Dispose();

    string Copy(string fixture)
    {
        var target = Path.Combine(temp.Path, Path.GetFileName(fixture));
        File.Copy(Path.Combine(Setup.Fixtures, fixture), target);
        return target;
    }

    static string AsLatin1(string text, int codePage) => Encoding.Latin1.GetString(Encoding.GetEncoding(codePage).GetBytes(text));

    [Theory]
    [InlineData("こんにちは", 932)]
    [InlineData("椎名林檎 - 丸の内サディスティック", 932)]
    [InlineData("Mr.Children - 終わりなき旅", 932)]
    [InlineData("アイウエオ", 932)]
    [InlineData("東京事変", 932)]
    [InlineData("周杰伦 - 七里香", 936)]
    [InlineData("你好", 936)]
    [InlineData("邓丽君 月亮代表我的心", 936)]
    [InlineData("周杰倫", 950)]
    [InlineData("張學友 - 吻別", 950)]
    [InlineData("五月天", 950)]
    [InlineData("こんにちは", 65001)]
    [InlineData("Björk", 65001)]
    [InlineData("안녕하세요", 65001)]
    public void Legacy_bytes_read_as_their_text(string text, int codePage) =>
        Assert.Equal(text, LegacyText.Fix(AsLatin1(text, codePage)));

    [Theory]
    [InlineData("Beyoncé")]
    [InlineData("Sigur Rós")]
    [InlineData("Mötley Crüe")]
    [InlineData("Àgua de Beber")]
    [InlineData("Ärzte")]
    [InlineData("Ångström")]
    [InlineData("Café del Mar")]
    [InlineData("Señor")]
    [InlineData("Æon Flux")]
    [InlineData("Hüsker Dü")]
    [InlineData("Les Misérables")]
    [InlineData("Édith Piaf")]
    [InlineData("Ñandú")]
    [InlineData("Sinéad O'Connor")]
    [InlineData("Ólafur Arnalds")]
    [InlineData("Øystein")]
    [InlineData("Für Elise")]
    [InlineData("Déjà vu")]
    [InlineData("ÁÉÍÓÚ")]
    [InlineData("Ünïcödé")]
    [InlineData("°C")]
    [InlineData("plain ASCII")]
    public void Western_text_is_unchanged(string text)
    {
        var latin1 = AsLatin1(text, 1252);
        Assert.Equal(latin1, LegacyText.Fix(latin1));
    }

    [Fact]
    public void An_id3v1_value_cut_in_half_keeps_the_rest()
    {
        var raw = Encoding.GetEncoding(932).GetBytes("椎名林檎 - 丸の内サディスティック")[..30];
        Assert.True(raw[^1] >= 0x80);
        Assert.Equal("椎名林檎 - 丸の内サディスティ", LegacyText.Fix(Encoding.Latin1.GetString(raw)));
    }

    [Fact]
    public void A_file_with_latin1_frames_reads_right()
    {
        var path = Copy("formats/tone.mp3");
        using (var file = TagLib.File.Create(path))
        {
            var id3 = (TagLib.Id3v2.Tag)file.GetTag(TagLib.TagTypes.Id3v2, true);
            void Set(string id, string text, TagLib.StringType encoding)
            {
                var frame = TagLib.Id3v2.TextInformationFrame.Get(id3, id, true);
                frame.Text = [text];
                frame.TextEncoding = encoding;
            }
            Set("TIT2", AsLatin1("丸の内サディスティック", 932), TagLib.StringType.Latin1);
            Set("TPE1", AsLatin1("周杰伦", 936), TagLib.StringType.Latin1);
            Set("TALB", "Ünïcödé", TagLib.StringType.UTF16);  // a Unicode frame is never second-guessed
            file.Save();
        }
        var tags = TagFile.Read(path);
        Assert.Equal("丸の内サディスティック", tags.Values[Field.Title]);
        Assert.Equal("周杰伦", tags.Values[Field.Artist]);
        Assert.Equal("Ünïcödé", tags.Values[Field.Album]);
        Assert.Equal("周杰伦", TagReader.Read(path, null, null).Artist);  // the index reads it the same way
    }

    [Fact]
    public void An_id3v1_tag_reads_right()
    {
        var path = Copy("formats/tone.mp3");
        using (var file = TagLib.File.Create(path))
        {
            file.RemoveTags(TagLib.TagTypes.AllTags);
            file.Save();
        }
        byte[] Field30(string text, int codePage) => [.. Encoding.GetEncoding(codePage).GetBytes(text), .. new byte[30]];
        var tag = new List<byte>("TAG"u8.ToArray());
        tag.AddRange(Field30("東京事変", 932)[..30]);
        tag.AddRange(Field30("周杰倫", 950)[..30]);
        tag.AddRange(new byte[30]);
        tag.AddRange("2004"u8.ToArray());
        tag.AddRange(new byte[30]);
        tag.Add(0xFF);
        using (var stream = new FileStream(path, FileMode.Append)) stream.Write(tag.ToArray());
        var values = TagFile.Read(path).Values;
        Assert.Equal("東京事変", values[Field.Title]);
        Assert.Equal("周杰倫", values[Field.Artist]);
        Assert.Equal("2004", values[Field.Year]);
    }

    public static TheoryData<string> Writable =>
    [
        "formats/tone.flac", "formats/tone.mp3", "formats/tone.mp2", "formats/tone.ogg", "formats/tone.opus",
        "formats/tone_aac.m4a", "formats/tone_alac.m4a", "formats/tone.m4b", "formats/tone.wav", "formats/tone.aiff",
        "formats/tone.wv", "formats/tone.wma",
    ];

    [Theory]
    [MemberData(nameof(Writable))]
    public void Every_field_saves_and_reads_back(string fixture)
    {
        var path = Copy(fixture);
        var before = TagFile.Read(path);
        var changes = new Dictionary<Field, string>
        {
            [Field.Title] = "Écrit 書き", [Field.Artist] = "Björk Ø", [Field.AlbumArtist] = "Various",
            [Field.Album] = "Ünïcode ✓", [Field.Track] = "7", [Field.Disc] = "2", [Field.DiscTotal] = "3",
            [Field.Year] = "1997-05-21", [Field.Genre] = "Electronic", [Field.Composer] = "Someone",
            [Field.Comment] = "LUFS Target : -18",
        };
        if (!before.NoTrackTotal) changes[Field.TrackTotal] = "12";
        TagFile.Save(path, changes, null);
        var after = TagFile.Read(path).Values;
        foreach (var (field, value) in changes)
            Assert.True(value == after[field], $"{fixture} {field}: {after[field]}");
    }

    [Fact]
    public void A_save_writes_only_what_changed_and_clears_with_an_empty_value()
    {
        var path = Copy("formats/tone.flac");
        TagFile.Save(path, new Dictionary<Field, string> { [Field.Album] = "Kept", [Field.Genre] = "Rock" }, null);
        TagFile.Save(path, new Dictionary<Field, string> { [Field.Genre] = "" }, null);
        var values = TagFile.Read(path).Values;
        Assert.Equal("Kept", values[Field.Album]);
        Assert.Equal("", values[Field.Genre]);
        using var file = TagLib.File.Create(path);
        Assert.Equal(TagLib.TagTypes.Xiph, file.TagTypesOnDisk & ~TagLib.TagTypes.FlacMetadata);  // no ID3 added
    }

    [Fact]
    public void A_wma_comment_is_the_description_the_python_app_reads()
    {
        var path = Copy("formats/tone.wma");
        TagFile.Save(path, new Dictionary<Field, string> { [Field.Comment] = "Live take" }, null);
        Assert.Equal("Live take", TagFile.Read(path).Values[Field.Comment]);
        var python = PythonApp.Run("""
            import sys
            from refrain.core.tags import TaggedFile
            print(TaggedFile(sys.stdin.read()).fields().comment)
            """, path);
        Assert.Equal("Live take", python.Trim());
    }

    [Fact]
    public void An_mp3_keeps_its_id3_version_and_gets_no_id3v1()
    {
        var path = Copy("formats/tone.mp3");
        byte version;
        using (var file = TagLib.File.Create(path))
        {
            var id3 = (TagLib.Id3v2.Tag)file.GetTag(TagLib.TagTypes.Id3v2, true);
            id3.Version = 3;
            id3.Title = "v2.3";
            file.RemoveTags(TagLib.TagTypes.Id3v1);
            file.Save();
            version = id3.Version;
        }
        TagFile.Save(path, new Dictionary<Field, string> { [Field.Title] = "Still v2.3" }, null);
        using var after = TagLib.File.Create(path);
        Assert.Equal(version, ((TagLib.Id3v2.Tag)after.GetTag(TagLib.TagTypes.Id3v2)).Version);
        Assert.False(after.TagTypesOnDisk.HasFlag(TagLib.TagTypes.Id3v1));
        Assert.Equal("Still v2.3", after.Tag.Title);
    }

    [Fact]
    public void Numbers_must_be_whole()
    {
        var path = Copy("formats/tone.flac");
        var error = Assert.Throws<TagSaveException>(() =>
            TagFile.Save(path, new Dictionary<Field, string> { [Field.Track] = "three" }, null));
        Assert.Contains("Track #", error.Message);
    }

    [Fact]
    public void A_cover_is_replaced_and_removed_and_other_pictures_stay()
    {
        var path = Copy("formats/tone.flac");
        var front = File.ReadAllBytes(Path.Combine(Setup.Fixtures, "edge", "front.jpg"));
        var back = File.ReadAllBytes(Path.Combine(Setup.Fixtures, "edge", "back.jpg"));
        using (var file = TagLib.File.Create(path))
        {
            file.Tag.Pictures = [new TagLib.Picture(new TagLib.ByteVector(back)) { Type = TagLib.PictureType.BackCover, MimeType = "image/jpeg" }];
            file.Save();
        }
        Assert.Equal("image/jpeg", TagFile.ImageType(front));
        TagFile.Save(path, new Dictionary<Field, string>(), new TagFile.CoverChange(new CoverImage(front, "image/jpeg")));
        Assert.Equal(front, TagFile.Read(path).Cover!.Data);
        TagFile.Save(path, new Dictionary<Field, string>(), new TagFile.CoverChange(null));
        Assert.Null(TagFile.Read(path).Cover);
        using var after = TagLib.File.Create(path);
        Assert.Equal(TagLib.PictureType.BackCover, Assert.Single(after.Tag.Pictures).Type);
    }

    [Fact]
    public void The_format_line_reads_as_the_python_apps()
    {
        Assert.Equal($"FLAC 16-bit/44.1 kHz · 4:44 · {32.0:N1} MB",
            TagFile.FormatLine(".flac", true, 16, 900, 44100, 284, 32 * 1024 * 1024));
        Assert.Equal($"MP3 320 kbps/44.1 kHz · 3:12 · {7.4:N1} MB",
            TagFile.FormatLine(".mp3", false, 16, 320, 44100, 192, (long)(7.4 * 1024 * 1024)));
        var flac = TagFile.Read(Copy("formats/tone.flac")).Details;
        Assert.StartsWith("FLAC ", flac.FormatLine);
        Assert.NotNull(flac.BitDepth);
    }

    static FileTags Tags(params (Field, string)[] values)
    {
        var all = Enum.GetValues<Field>().ToDictionary(f => f, _ => "");
        foreach (var (f, v) in values) all[f] = v;
        return new FileTags("x.flac", all, null, new FileDetails("", 0, null, null, null, null, null, null, null, null, null, null), null);
    }

    [Fact]
    public void One_file_saves_the_fields_whose_text_changed()
    {
        var file = Tags((Field.Title, "Song"), (Field.Track, "3"), (Field.Genre, "  "));
        var texts = file.Values.ToDictionary();
        texts[Field.Title] = "Song";
        texts[Field.Track] = "03";  // the same number
        texts[Field.Album] = "New";
        Assert.Equal(new Dictionary<Field, string> { [Field.Album] = "New" }, TagEdits.SingleChanges(file, texts));
    }

    [Fact]
    public void Several_files_save_the_shared_fields_edited_or_cleared()
    {
        var a = Tags((Field.Artist, "A"), (Field.Album, "Same"), (Field.Genre, "Rock"));
        var b = Tags((Field.Artist, "B"), (Field.Album, "Same"), (Field.Genre, "Pop"));
        var common = TagEdits.Common([a, b]);
        Assert.Null(common[Field.Artist]);
        Assert.Equal("Same", common[Field.Album]);
        Assert.DoesNotContain(Field.Title, common.Keys);
        var texts = Enum.GetValues<Field>().ToDictionary(f => f, f => common.GetValueOrDefault(f) ?? "");
        texts[Field.Album] = "Same";  // touched, unchanged
        texts[Field.Artist] = "";  // a "(multiple values)" field emptied by hand: left alone
        texts[Field.Year] = "2001";
        var changes = TagEdits.MultiChanges(texts, new HashSet<Field> { Field.Album, Field.Artist, Field.Year },
            new HashSet<Field> { Field.Genre }, common);
        Assert.Equal(new Dictionary<Field, string> { [Field.Year] = "2001", [Field.Genre] = "" }, changes);
    }

    [Fact]
    public void Text_previews_decode_as_their_writers_meant()
    {
        Assert.Equal(("é", "UTF-8 (BOM)"), Preview.Decode([0xEF, 0xBB, 0xBF, 0xC3, 0xA9]));
        Assert.Equal(("é", "UTF-16 LE"), Preview.Decode([0xFF, 0xFE, 0xE9, 0x00]));
        Assert.Equal(("é", "UTF-8"), Preview.Decode([0xC3, 0xA9]));
        Assert.Equal(("café", "Windows-1252"), Preview.Decode([.. "caf"u8, 0xE9]));
        Assert.Equal(("█", "CP437 (DOS)"), Preview.Decode([0xDB], dos: true));
        Assert.Equal("UTF-8", Preview.Decode([.. "ab"u8, 0xC3], cut: true).Encoding);  // cut mid-character
        Assert.Equal("Windows-1252", Preview.Decode([.. "ab"u8, 0xC3]).Encoding);  // a whole file ending in "Ã"
        foreach (var name in Directory.GetFiles(Path.Combine(Setup.Fixtures, "preview")))
        {
            var preview = Preview.ReadText(name);
            Assert.DoesNotContain('\r', preview.Text);
            Assert.False(preview.Truncated);
        }
        Assert.Equal(Preview.Kind.Image, Preview.Of("Front.JPG"));
        Assert.Equal(Preview.Kind.None, Preview.Of("song.flac"));
    }

    [Fact]
    public void A_long_text_is_cut_and_wrapped()
    {
        var path = Path.Combine(temp.Path, "long.log");
        File.WriteAllText(path, new string('x', 5000) + "\n" + new string('y', Preview.TextLimit));
        var preview = Preview.ReadText(path);
        Assert.True(preview.Truncated);
        Assert.True(preview.Wrap);
        Assert.All(preview.Text.Split('\n'), line => Assert.True(line.Length <= 2000));
    }
}
