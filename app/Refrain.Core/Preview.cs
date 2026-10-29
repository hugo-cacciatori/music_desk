using System.Text;

namespace Refrain.Core;

/// <summary>The first bytes of a text file, decoded: its text, the encoding's name (shown, so a garbled preview
/// explains itself), the file's size, whether it was cut, and whether to word-wrap it.</summary>
public sealed record TextPreview(string Text, string Encoding, long Size, bool Truncated, bool Wrap);

/// <summary>File preview (SPEC §20): which files the side panel shows read-only, and reading text of unknown
/// encoding. Rip logs (EAC writes UTF-16), cue sheets, notes in Windows-1252 and NFO files in the DOS code page are
/// each decoded the way their writer meant; a garbled byte becomes U+FFFD, never an error.</summary>
public static class Preview
{
    public static readonly IReadOnlySet<string> TextTypes = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        { ".txt", ".log", ".cue", ".nfo", ".sfv", ".md5", ".ffp", ".accurip", ".m3u", ".m3u8", ".lrc" };

    public static readonly IReadOnlySet<string> ImageTypes = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        { ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp" };

    public const int TextLimit = 512 * 1024;  // bytes shown; the rest is one Open away (measured in M9, SPEC §20)
    const int WrapFrom = 300;  // a longer line turns word wrap on
    const int BreakAt = 2000;  // longer lines are cut into pieces of this length
    public const long ImageLimit = 50 * 1024 * 1024;  // larger images aren't decoded

    static readonly Encoding StrictUtf8 = new UTF8Encoding(false, throwOnInvalidBytes: true);

    static Preview() => Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);

    public enum Kind { None, Text, Image }

    public static Kind Of(string path)
    {
        var ext = Path.GetExtension(path);
        return TextTypes.Contains(ext) ? Kind.Text : ImageTypes.Contains(ext) ? Kind.Image : Kind.None;
    }

    /// <summary>(text, encoding name): a BOM first, then UTF-8 if the bytes are valid, else the legacy code page.
    /// When the bytes were cut from a longer file, a UTF-8 character split at the end still counts as UTF-8.</summary>
    public static (string Text, string Encoding) Decode(byte[] data, bool dos = false, bool cut = false)
    {
        if (data.AsSpan().StartsWith((byte[])[0xEF, 0xBB, 0xBF])) return (Encoding.UTF8.GetString(data, 3, data.Length - 3), "UTF-8 (BOM)");
        if (data.AsSpan().StartsWith((byte[])[0xFF, 0xFE])) return (Encoding.Unicode.GetString(data, 2, data.Length - 2), "UTF-16 LE");
        if (data.AsSpan().StartsWith((byte[])[0xFE, 0xFF])) return (Encoding.BigEndianUnicode.GetString(data, 2, data.Length - 2), "UTF-16 BE");
        try
        {
            return (StrictUtf8.GetString(data), "UTF-8");
        }
        catch (DecoderFallbackException)
        {
            if (cut && ValidUntilCut(data)) return (Encoding.UTF8.GetString(data), "UTF-8");
        }
        return dos
            ? (Encoding.GetEncoding(437).GetString(data), "CP437 (DOS)")
            : (Encoding.GetEncoding(1252).GetString(data), "Windows-1252");
    }

    /// <summary>Valid UTF-8 but for a character the cut split at the very end.</summary>
    static bool ValidUntilCut(byte[] data)
    {
        for (var back = 1; back <= 3 && back <= data.Length; back++)
        {
            var lead = data[^back];
            if (lead < 0x80) return false;
            if (lead < 0xC0) continue;  // a continuation byte: look further back for the lead
            var needed = lead >= 0xF0 ? 4 : lead >= 0xE0 ? 3 : 2;
            if (needed <= back) return false;
            try
            {
                StrictUtf8.GetString(data, 0, data.Length - back);
                return true;
            }
            catch (DecoderFallbackException)
            {
                return false;
            }
        }
        return false;
    }

    /// <summary>The first <paramref name="limit"/> bytes of a text file, decoded, line endings made LF, lines over
    /// BreakAt cut into pieces. Read shared, so Explorer can still rename or delete the file. Throws IOException.</summary>
    public static TextPreview ReadText(string path, int limit = TextLimit)
    {
        byte[] data;
        long size;
        using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
        {
            size = stream.Length;
            data = new byte[Math.Min(limit, size)];
            stream.ReadExactly(data);
        }
        var truncated = size > data.Length;
        var (text, encoding) = Decode(data, Path.GetExtension(path).Equals(".nfo", StringComparison.OrdinalIgnoreCase), truncated);
        var lines = text.Replace("\r\n", "\n").Split('\n');
        var longest = lines.Max(l => l.Length);
        if (longest > BreakAt)
            lines = lines.SelectMany(l => l.Length <= BreakAt
                ? [l]
                : Enumerable.Range(0, (l.Length + BreakAt - 1) / BreakAt).Select(i => l.Substring(i * BreakAt, Math.Min(BreakAt, l.Length - i * BreakAt))))
                .ToArray();
        return new TextPreview(string.Join("\n", lines), encoding, size, truncated, longest > WrapFrom);
    }
}
