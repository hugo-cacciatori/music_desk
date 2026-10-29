"""Legacy text in ID3 frames marked Latin-1 (SPEC §27): UTF-8, Shift-JIS, GBK or Big5 read back when they fit."""


from mutagen.id3 import Encoding

# In this order when two fit equally well. Not EUC-KR: its hangul bytes are also common GBK hanzi, so Korean
# can't be told from Chinese (SPEC §27).
LEGACY_CODECS = ("cp932", "gbk", "big5")
ID3V1_FIELD = 30  # bytes: an ID3v1 value this long may end in half a character
MIN_COMMON = 0.8  # real text in these codecs is almost all common characters; a Western misreading is not


def _is_cjk(ch: str) -> bool:
    """Kana and ideographs (and hangul, which UTF-8 gives): the characters a legacy guess must produce."""
    o = ord(ch)
    return (0x3040 <= o <= 0x30FF or 0x31F0 <= o <= 0x31FF or 0x3400 <= o <= 0x4DBF or 0x4E00 <= o <= 0x9FFF
            or 0xF900 <= o <= 0xFAFF or 0xFF66 <= o <= 0xFF9F or 0xAC00 <= o <= 0xD7A3 or 0x3130 <= o <= 0x318F)


def _is_clean(ch: str) -> bool:
    """Characters a CJK title or name holds: ASCII, CJK, their punctuation and a few common symbols."""
    o = ord(ch)
    return ((0x20 <= o < 0x7F or ch in "\t\r\n·×÷°±") or _is_cjk(ch)
            or 0x3000 <= o <= 0x303F or 0xFF01 <= o <= 0xFF65 or 0xFFE0 <= o <= 0xFFE6 or 0x2010 <= o <= 0x203B
            or 0x2160 <= o <= 0x217F or 0x2460 <= o <= 0x24FF or 0x25A0 <= o <= 0x26FF)


def _is_common(ch: str, codec: str) -> bool:
    """Whether `ch` is in the common part of `codec` (JIS level 1 and kana, GB2312 level 1, Big5's frequent
    characters): real text is nearly all common, a Western string misread is mostly not."""
    o = ord(ch)
    if codec == "cp932" and (0x3040 <= o <= 0x30FF):
        return True
    if 0xFF61 <= o <= 0xFF9F:  # half-width katakana: what Latin-1 accents become in Shift-JIS
        return False
    try:
        raw = ch.encode({"gbk": "gb2312"}.get(codec, codec))
    except UnicodeEncodeError:
        return False
    if len(raw) != 2:
        return False
    code = raw[0] << 8 | raw[1]
    return {"cp932": 0x889F <= code <= 0x9872, "gbk": 0xB0A1 <= code <= 0xD7FE,
            "big5": 0xA440 <= code <= 0xC67E}[codec]


def _legacy_decode(raw: bytes, codec: str) -> str | None:
    try:
        return raw.decode(codec)
    except UnicodeDecodeError:
        if len(raw) != ID3V1_FIELD or raw[-1] < 0x80:
            return None
    try:
        return raw[:-1].decode(codec)  # ID3v1's 30 bytes may cut the last character in half
    except UnicodeDecodeError:
        return None


def decode_latin1_text(text: str) -> str:
    """Text from an ID3 frame marked Latin-1 (or ID3v1), as mutagen decoded it, read the most likely way (SPEC §27):
    ASCII as is; else its bytes as UTF-8 when valid; else as Shift-JIS, GBK or Big5 when that gives clean
    CJK text made mostly of common characters; else Latin-1, unchanged."""
    if text.isascii():
        return text
    try:
        raw = text.encode("latin-1")
    except UnicodeEncodeError:
        return text  # not Latin-1 text after all
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    best, best_rate = text, 0.0
    letters = any(ch.isascii() and ch.isalpha() for ch in text)
    for codec in LEGACY_CODECS:
        decoded = _legacy_decode(raw, codec)
        if decoded is None or not all(_is_clean(ch) for ch in decoded):
            continue
        cjk = [ch for ch in decoded if _is_cjk(ch)]
        # One CJK character beside Latin letters is how "Àgua" or "Ärzte" misread; real text has more.
        if not cjk or (len(cjk) < 2 and letters):
            continue
        rate = sum(_is_common(ch, codec) for ch in cjk) / len(cjk)
        if rate >= MIN_COMMON and rate > best_rate:
            best, best_rate = decoded, rate
    return best


def id3_text(frame, value) -> str:
    """A value of an ID3 text frame, as str; Latin-1 frames go through decode_latin1_text."""
    text = str(value)
    return decode_latin1_text(text) if frame.encoding == Encoding.LATIN1 else text
