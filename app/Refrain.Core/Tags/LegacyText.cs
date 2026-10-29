using System.Text;

namespace Refrain.Core.Tags;

/// <summary>Legacy text in ID3 frames marked Latin-1, and ID3v1 (SPEC §27): UTF-8, Shift-JIS, GBK or Big5 read back
/// when they fit. Ported from the Python app's <c>core/tags/legacy_text.py</c>.</summary>
public static class LegacyText
{
    const int Id3v1Field = 30;  // bytes: an ID3v1 value this long may end in half a character
    const double MinCommon = 0.8;  // real text in these codecs is almost all common characters; a Western misreading isn't

    static readonly Encoding Latin1 = Encoding.Latin1;
    static readonly Encoding StrictUtf8 = new UTF8Encoding(false, throwOnInvalidBytes: true);

    // In this order when two fit equally well. Not EUC-KR: its hangul bytes are also common GBK hanzi (SPEC §27).
    static readonly (string Name, Encoding Codec)[] Codecs;

    static LegacyText()
    {
        Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);
        Encoding Strict(int page) =>
            Encoding.GetEncoding(page, EncoderFallback.ExceptionFallback, DecoderFallback.ExceptionFallback);
        Codecs = [("cp932", Strict(932)), ("gbk", Strict(936)), ("big5", Strict(950))];
    }

    static bool IsCjk(char c) =>
        c is >= '぀' and <= 'ヿ' or >= 'ㇰ' and <= 'ㇿ' or >= '㐀' and <= '䶿'
            or >= '一' and <= '鿿' or >= '豈' and <= '﫿' or >= 'ｦ' and <= 'ﾟ'
            or >= '가' and <= '힣' or >= '㄰' and <= '㆏';

    /// <summary>Characters a CJK title or name holds: ASCII, CJK, their punctuation and a few common symbols.</summary>
    static bool IsClean(char c) =>
        c is >= ' ' and < '\u007F' or '\t' or '\r' or '\n' or '·' or '×' or '÷' or '°' or '±'
        || IsCjk(c)
        || c is >= '　' and <= '〿' or >= '！' and <= '･' or >= '￠' and <= '￦'
            or >= '‐' and <= '※' or >= 'Ⅰ' and <= 'ⅿ' or >= '①' and <= '⓿'
            or >= '■' and <= '⛿';

    /// <summary>Whether <paramref name="c"/> is in the common part of the codec (JIS level 1 and kana, GB2312 level
    /// 1, Big5's frequent characters).</summary>
    static bool IsCommon(char c, string name, Encoding codec)
    {
        if (name == "cp932" && c is >= '぀' and <= 'ヿ') return true;
        if (c is >= '｡' and <= 'ﾟ') return false;  // half-width katakana: Latin-1 accents in Shift-JIS
        byte[] raw;
        try
        {
            raw = codec.GetBytes([c]);
        }
        catch (EncoderFallbackException)
        {
            return false;
        }
        if (raw.Length != 2) return false;
        var code = raw[0] << 8 | raw[1];
        return name switch
        {
            "cp932" => code is >= 0x889F and <= 0x9872,
            "gbk" => code is >= 0xB0A1 and <= 0xD7FE && raw[1] >= 0xA1,  // GB2312 level 1 only
            _ => code is >= 0xA440 and <= 0xC67E,
        };
    }

    static string? Decode(byte[] raw, Encoding codec)
    {
        try
        {
            return codec.GetString(raw);
        }
        catch (DecoderFallbackException)
        {
            if (raw.Length != Id3v1Field || raw[^1] < 0x80) return null;
        }
        try
        {
            return codec.GetString(raw, 0, raw.Length - 1);  // ID3v1's 30 bytes may cut the last character in half
        }
        catch (DecoderFallbackException)
        {
            return null;
        }
    }

    /// <summary>Text read as Latin-1, read the most likely way: ASCII as is; else its bytes as UTF-8 when valid; else
    /// as Shift-JIS, GBK or Big5 when that gives clean CJK text made mostly of common characters; else unchanged.</summary>
    public static string Fix(string text)
    {
        if (text.All(c => c < 0x80)) return text;
        if (text.Any(c => c > 0xFF)) return text;  // not Latin-1 text after all
        var raw = Latin1.GetBytes(text);
        try
        {
            return StrictUtf8.GetString(raw);
        }
        catch (DecoderFallbackException)
        {
        }
        var best = text;
        var bestRate = 0.0;
        var letters = text.Any(c => c < 0x80 && char.IsLetter(c));
        foreach (var (name, codec) in Codecs)
        {
            var decoded = Decode(raw, codec);
            if (decoded is null || !decoded.All(IsClean)) continue;
            var cjk = decoded.Where(IsCjk).ToList();
            // One CJK character beside Latin letters is how "Àgua" or "Ärzte" misread; real text has more.
            if (cjk.Count == 0 || (cjk.Count < 2 && letters)) continue;
            var rate = cjk.Count(c => IsCommon(c, name, codec)) / (double)cjk.Count;
            if (rate >= MinCommon && rate > bestRate)
            {
                best = decoded;
                bestRate = rate;
            }
        }
        return best;
    }
}
