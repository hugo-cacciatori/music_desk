"""Tag reading and writing for every format, through one adapter per tag family (SPEC §1, §7, §17).

Replaces the script's `load_audio`, `get_tag`, `set_tag`, `set_comment` and `find_txxx` (FLAC/MP3 only) and its
TinyTag read, keeping their semantics: keys match ignoring case, ReplayGain keys are written in uppercase, and
`rename_fields()` returns what TinyTag 2.0.0 would.

Writes stay in memory until `save()`, as with mutagen.
"""

import base64
import struct
from dataclasses import dataclass
from dataclasses import fields as dataclass_fields
from pathlib import Path

from mutagen.apev2 import BINARY, APEValue
from mutagen.asf import ASFByteArrayAttribute  # pyright: ignore[reportPrivateImportUsage]
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, COMM, TALB, TCOM, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, TXXX, USLT, Encoding
from mutagen.mp4 import AtomDataType, MP4Cover, MP4FreeForm

from music_desk.core.formats import FormatInfo, TagFamily, format_for, open_audio

NUMBER_FIELDS = ("track", "track_total", "disc", "disc_total")
TEXT_FIELDS = ("title", "artist", "albumartist", "album", "year", "genre", "composer", "comment", "lyrics")
# The fields rename masks can use (the script's NUMBER_FIELDS + TEXT_FIELDS).
RENAME_TEXT_FIELDS = ("title", "artist", "albumartist", "album", "year", "genre", "composer")

FRONT_COVER = 3
REPLAYGAIN_PREFIX = "replaygain_"  # every ReplayGain tag name starts so (ignoring case), in every family


class TagError(Exception):
    """A tag can't be read or written (no tag support, or a field the format doesn't have)."""


@dataclass(frozen=True)
class Fields:
    """The editable fields of SPEC §7. None means missing."""

    title: str | None = None
    artist: str | None = None
    albumartist: str | None = None
    album: str | None = None
    track: int | None = None
    track_total: int | None = None
    disc: int | None = None
    disc_total: int | None = None
    year: str | None = None
    genre: str | None = None
    composer: str | None = None
    comment: str | None = None
    lyrics: str | None = None


FIELD_NAMES = tuple(f.name for f in dataclass_fields(Fields))


@dataclass(frozen=True)
class Cover:
    data: bytes
    mime: str


@dataclass(frozen=True)
class StreamInfo:
    codec: str
    duration: float
    bitrate: int | None
    sample_rate: int | None
    bits_per_sample: int | None
    channels: int | None


# ---------------------------------------------------------------------------
# Legacy text in ID3 frames marked Latin-1 (SPEC §27)
# ---------------------------------------------------------------------------

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


def _id3_text(frame, value) -> str:
    """A value of an ID3 text frame, as str; Latin-1 frames go through decode_latin1_text."""
    text = str(value)
    return decode_latin1_text(text) if frame.encoding == Encoding.LATIN1 else text


def parse_int(text) -> int | None:
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return None


def parse_pair(text) -> tuple[int | None, int | None]:
    """'3/12' -> (3, 12); '3' -> (3, None)."""
    if text is None:
        return None, None
    number, _, total = str(text).partition("/")
    return parse_int(number), parse_int(total) if total else None


def format_pair(number: int | None, total: int | None) -> str | None:
    if number is None and total is None:
        return None
    return f"{'' if number is None else number}/{total}" if total is not None else str(number)


def guess_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    return "image/jpeg"


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------

class _Adapter:
    """Base adapter. `read` returns raw values: ints for numbers (0 kept), strings as stored."""

    family: TagFamily

    def read(self, audio) -> dict:
        raise NotImplementedError

    def write_text(self, audio, name: str, value: str | None) -> None:
        raise NotImplementedError

    def write_pair(self, audio, name: str, number: int | None, total: int | None) -> None:
        """name is 'track' or 'disc'."""
        raise NotImplementedError

    def get_replaygain(self, audio, key: str) -> str | None:
        raise NotImplementedError

    def set_replaygain(self, audio, key: str, value: str) -> None:
        raise NotImplementedError

    def set_comment(self, audio, text: str) -> None:
        self.write_text(audio, "comment", text)

    def replaygain_name(self, key: str) -> str:
        """The ReplayGain name a tag key carries (MP4 wraps it in a freeform key)."""
        return key

    def remove_replaygain(self, audio) -> int:
        """Delete every REPLAYGAIN_* tag (any case: rsgain's four values, and any other a tool wrote, such as a
        reference loudness). Returns how many keys went."""
        if audio.tags is None:
            return 0
        keys = [k for k in list(audio.tags.keys()) if self.replaygain_name(k).lower().startswith(REPLAYGAIN_PREFIX)]
        for key in keys:
            del audio.tags[key]
        return len(keys)

    def cover(self, audio) -> Cover | None:
        raise NotImplementedError

    def set_cover(self, audio, cover: Cover | None) -> None:
        """Replace the front cover, or remove it with None. Other pictures stay."""
        raise NotImplementedError

    def save(self, audio) -> None:
        audio.save()

    @staticmethod
    def ensure_tags(audio) -> None:
        if audio.tags is None:
            audio.add_tags()


class _VorbisAdapter(_Adapter):
    family = TagFamily.VORBIS
    KEYS = {"title": "TITLE", "artist": "ARTIST", "albumartist": "ALBUMARTIST", "album": "ALBUM", "year": "DATE",
            "genre": "GENRE", "composer": "COMPOSER", "comment": "COMMENT", "lyrics": "LYRICS"}
    PAIRS = {"track": ("TRACKNUMBER", "TRACKTOTAL", "TOTALTRACKS"), "disc": ("DISCNUMBER", "DISCTOTAL", "TOTALDISCS")}

    @staticmethod
    def first(tags, *keys) -> str | None:
        for key in keys:
            values = tags.get(key)  # VComment keys match ignoring case
            if values:
                return values[0]
        return None

    def read(self, audio) -> dict:
        tags = audio.tags
        if tags is None:
            return {}
        values: dict[str, str | int | None] = {name: self.first(tags, key) for name, key in self.KEYS.items()}
        if values["lyrics"] is None:
            values["lyrics"] = self.first(tags, "UNSYNCEDLYRICS")
        for name, (number_key, total_key, legacy_total_key) in self.PAIRS.items():
            number, pair_total = parse_pair(self.first(tags, number_key))
            total = parse_int(self.first(tags, total_key, legacy_total_key))
            values[name] = number
            values[f"{name}_total"] = total if total is not None else pair_total
        return values

    def delete(self, audio, *keys) -> None:
        for key in keys:
            if key in audio.tags:
                del audio.tags[key]

    def write_text(self, audio, name, value) -> None:
        self.ensure_tags(audio)
        key = self.KEYS[name]
        if name == "lyrics":
            self.delete(audio, "UNSYNCEDLYRICS")  # else it would show through as the fallback
        if value is None:
            self.delete(audio, key)
        else:
            audio.tags[key] = value

    def write_pair(self, audio, name, number, total) -> None:
        self.ensure_tags(audio)
        number_key, total_key, legacy_total_key = self.PAIRS[name]
        self.delete(audio, number_key, total_key, legacy_total_key)
        if number is not None:
            audio.tags[number_key] = str(number)
        if total is not None:
            audio.tags[total_key] = str(total)

    def get_replaygain(self, audio, key) -> str | None:
        return self.first(audio.tags, key) if audio.tags is not None else None

    def set_replaygain(self, audio, key, value) -> None:
        self.ensure_tags(audio)
        audio.tags[key.upper()] = value  # replaces every case variant

    def pictures(self, audio) -> list[Picture]:
        if isinstance(audio, FLAC):
            return list(audio.pictures)
        if audio.tags is None:
            return []
        pictures = []
        for encoded in audio.tags.get("METADATA_BLOCK_PICTURE", []):
            try:
                pictures.append(Picture(base64.b64decode(encoded)))
            except Exception:  # noqa: BLE001 - a corrupt block is ignored, as players do
                continue
        return pictures

    def set_pictures(self, audio, pictures: list[Picture]) -> None:
        if isinstance(audio, FLAC):
            audio.clear_pictures()
            for picture in pictures:
                audio.add_picture(picture)
            return
        self.ensure_tags(audio)
        self.delete(audio, "METADATA_BLOCK_PICTURE")
        if pictures:
            audio.tags["METADATA_BLOCK_PICTURE"] = [base64.b64encode(p.write()).decode("ascii") for p in pictures]

    def cover(self, audio) -> Cover | None:
        pictures = self.pictures(audio)
        chosen = next((p for p in pictures if p.type == FRONT_COVER), pictures[0] if pictures else None)
        return Cover(chosen.data, chosen.mime or guess_mime(chosen.data)) if chosen else None

    def set_cover(self, audio, cover) -> None:
        pictures = self.pictures(audio)
        fronts = [p for p in pictures if p.type == FRONT_COVER]
        # Removing takes away what cover() shows: the front pictures, else the first picture.
        removed = fronts if fronts or cover is not None else pictures[:1]
        kept = [p for p in pictures if p not in removed]
        if cover is not None:
            picture = Picture()
            picture.type = FRONT_COVER
            picture.mime = cover.mime
            picture.data = cover.data
            kept.insert(0, picture)
        self.set_pictures(audio, kept)


class _ID3Adapter(_Adapter):
    family = TagFamily.ID3
    FRAMES = {"title": TIT2, "artist": TPE1, "albumartist": TPE2, "album": TALB, "year": TDRC, "genre": TCON,
              "composer": TCOM}
    PAIRS = {"track": TRCK, "disc": TPOS}

    def read(self, audio) -> dict:
        tags = audio.tags
        if tags is None:
            return {}
        values = {}
        for name, frame_class in self.FRAMES.items():
            frame = tags.get(frame_class.__name__)
            if frame is None or not frame.text:
                values[name] = None
            elif frame_class is TCON:
                values[name] = _id3_text(frame, frame.genres[0]) if frame.genres else None
            else:
                values[name] = _id3_text(frame, frame.text[0])
        comments = [f for f in tags.getall("COMM") if f.desc == ""]
        comments.sort(key=lambda f: f.lang != "eng")
        values["comment"] = _id3_text(comments[0], comments[0].text[0]) if comments and comments[0].text else None
        lyrics = [_id3_text(f, f.text) for f in tags.getall("USLT") if str(f.text).strip()]
        values["lyrics"] = lyrics[0] if lyrics else None
        for name, frame_class in self.PAIRS.items():
            frame = tags.get(frame_class.__name__)
            values[name], values[f"{name}_total"] = parse_pair(frame.text[0] if frame and frame.text else None)
        return values

    def write_text(self, audio, name, value) -> None:
        self.ensure_tags(audio)
        tags = audio.tags
        if name == "comment":
            for frame in [f for f in tags.getall("COMM") if f.desc == ""]:
                tags.delall(frame.HashKey)
            if value is not None:
                tags.add(COMM(encoding=3, lang="eng", desc="", text=[value]))
        elif name == "lyrics":
            for frame in [f for f in tags.getall("USLT") if f.desc == ""]:
                tags.delall(frame.HashKey)
            if value is not None:
                tags.add(USLT(encoding=3, lang="eng", desc="", text=value))
        else:
            frame_class = self.FRAMES[name]
            tags.delall(frame_class.__name__)
            if value is not None:
                tags.add(frame_class(encoding=3, text=[value]))

    def write_pair(self, audio, name, number, total) -> None:
        self.ensure_tags(audio)
        frame_class = self.PAIRS[name]
        audio.tags.delall(frame_class.__name__)
        text = format_pair(number, total)
        if text is not None:
            audio.tags.add(frame_class(encoding=3, text=[text]))

    @staticmethod
    def find_txxx(audio, name) -> list:  # of TXXX frames (their `text` is set at run time)
        if audio.tags is None:
            return []
        return [frame for frame in audio.tags.getall("TXXX") if frame.desc.lower() == name.lower()]

    def remove_replaygain(self, audio) -> int:
        if audio.tags is None:
            return 0
        frames = [f for f in audio.tags.getall("TXXX") if f.desc.lower().startswith(REPLAYGAIN_PREFIX)]
        for frame in frames:
            audio.tags.delall(frame.HashKey)
        return len(frames)

    def remove_txxx(self, audio, name) -> None:
        for frame in self.find_txxx(audio, name):
            audio.tags.delall(frame.HashKey)

    def get_replaygain(self, audio, key) -> str | None:
        frames = self.find_txxx(audio, key)
        return frames[0].text[0] if frames else None

    def set_replaygain(self, audio, key, value) -> None:
        self.ensure_tags(audio)
        self.remove_txxx(audio, key)
        audio.tags.add(TXXX(encoding=3, desc=key.upper(), text=[value]))

    def set_comment(self, audio, text) -> None:
        self.ensure_tags(audio)
        self.remove_txxx(audio, "comment")  # written by older versions of the script
        self.write_text(audio, "comment", text)

    def cover(self, audio) -> Cover | None:
        pictures = audio.tags.getall("APIC") if audio.tags is not None else []
        chosen = next((p for p in pictures if p.type == FRONT_COVER), pictures[0] if pictures else None)
        return Cover(chosen.data, chosen.mime or guess_mime(chosen.data)) if chosen else None

    def set_cover(self, audio, cover) -> None:
        self.ensure_tags(audio)
        pictures = audio.tags.getall("APIC")
        fronts = [p for p in pictures if p.type == FRONT_COVER]
        for frame in fronts if fronts or cover is not None else pictures[:1]:
            audio.tags.delall(frame.HashKey)
        if cover is not None:
            # APIC frames are keyed by description: don't collide with a picture that stays.
            desc = "" if "APIC:" not in audio.tags else "Front Cover"
            audio.tags.add(APIC(encoding=3, mime=cover.mime, type=FRONT_COVER, desc=desc, data=cover.data))

    def save(self, audio) -> None:
        """Keep the file's ID3 version (v2.3 stays v2.3); new or v2.2 tags become v2.4."""
        version = audio.tags.version[1] if audio.tags is not None and audio.tags.version[1] in (3, 4) else 4
        audio.save(v2_version=version)


class _MP4Adapter(_Adapter):
    family = TagFamily.MP4
    KEYS = {"title": "\xa9nam", "artist": "\xa9ART", "albumartist": "aART", "album": "\xa9alb", "year": "\xa9day",
            "genre": "\xa9gen", "composer": "\xa9wrt", "comment": "\xa9cmt", "lyrics": "\xa9lyr"}
    PAIRS = {"track": "trkn", "disc": "disk"}
    FREEFORM = "----:com.apple.iTunes:"

    def read(self, audio) -> dict:
        tags = audio.tags
        if tags is None:
            return {}
        values: dict[str, str | int | None] = {name: (str(tags[key][0]) if tags.get(key) else None)
                                               for name, key in self.KEYS.items()}
        for name, key in self.PAIRS.items():
            pair = tags.get(key)
            values[name], values[f"{name}_total"] = (pair[0][0], pair[0][1]) if pair else (None, None)
        return values

    def write_text(self, audio, name, value) -> None:
        self.ensure_tags(audio)
        key = self.KEYS[name]
        if value is None:
            audio.tags.pop(key, None)
        else:
            audio.tags[key] = [value]

    def write_pair(self, audio, name, number, total) -> None:
        self.ensure_tags(audio)
        key = self.PAIRS[name]
        if number is None and total is None:
            audio.tags.pop(key, None)
        else:
            audio.tags[key] = [(number or 0, total or 0)]

    def freeform_keys(self, audio, key) -> list[str]:
        wanted = (self.FREEFORM + key).lower()
        return [k for k in (audio.tags or {}) if k.lower() == wanted]

    def replaygain_name(self, key: str) -> str:
        return key[len(self.FREEFORM):] if key.startswith(self.FREEFORM) else ""

    def get_replaygain(self, audio, key) -> str | None:
        keys = self.freeform_keys(audio, key)
        return bytes(audio.tags[keys[0]][0]).decode("utf-8") if keys else None

    def set_replaygain(self, audio, key, value) -> None:
        self.ensure_tags(audio)
        for existing in self.freeform_keys(audio, key):
            del audio.tags[existing]
        audio.tags[self.FREEFORM + key.upper()] = [MP4FreeForm(value.encode("utf-8"), dataformat=AtomDataType.UTF8)]

    def cover(self, audio) -> Cover | None:
        covers = audio.tags.get("covr") if audio.tags is not None else None
        if not covers:
            return None
        mime = "image/png" if covers[0].imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
        return Cover(bytes(covers[0]), mime)

    def set_cover(self, audio, cover) -> None:
        # MP4 has no picture types: the first covr image is the front cover.
        self.ensure_tags(audio)
        covers = list(audio.tags.get("covr", []))[1:]
        if cover is not None:
            image_format = MP4Cover.FORMAT_PNG if cover.mime == "image/png" else MP4Cover.FORMAT_JPEG
            covers.insert(0, MP4Cover(cover.data, imageformat=image_format))
        if covers:
            audio.tags["covr"] = covers
        else:
            audio.tags.pop("covr", None)


class _APEv2Adapter(_Adapter):
    family = TagFamily.APEV2
    KEYS = {"title": "Title", "artist": "Artist", "albumartist": "Album Artist", "album": "Album", "year": "Year",
            "genre": "Genre", "composer": "Composer", "comment": "Comment", "lyrics": "Lyrics"}
    PAIRS = {"track": "Track", "disc": "Disc"}
    COVER_KEY = "Cover Art (Front)"

    def read(self, audio) -> dict:
        tags = audio.tags  # APEv2 keys match ignoring case
        if tags is None:
            return {}
        values: dict[str, str | int | None] = {name: (str(tags[key]) if key in tags else None)
                                               for name, key in self.KEYS.items()}
        for name, key in self.PAIRS.items():
            values[name], values[f"{name}_total"] = parse_pair(str(tags[key]) if key in tags else None)
        return values

    def put(self, audio, key, value) -> None:
        self.ensure_tags(audio)
        if key in audio.tags:
            del audio.tags[key]
        if value is not None:
            audio.tags[key] = value

    def write_text(self, audio, name, value) -> None:
        self.put(audio, self.KEYS[name], value)

    def write_pair(self, audio, name, number, total) -> None:
        self.put(audio, self.PAIRS[name], format_pair(number, total))

    def get_replaygain(self, audio, key) -> str | None:
        return str(audio.tags[key]) if audio.tags is not None and key in audio.tags else None

    def set_replaygain(self, audio, key, value) -> None:
        self.put(audio, key.upper(), value)

    def cover(self, audio) -> Cover | None:
        if audio.tags is None or self.COVER_KEY not in audio.tags:
            return None
        _, _, data = audio.tags[self.COVER_KEY].value.partition(b"\0")
        return Cover(data, guess_mime(data))

    def set_cover(self, audio, cover) -> None:
        if cover is None:
            self.put(audio, self.COVER_KEY, None)
            return
        name = b"cover.png" if cover.mime == "image/png" else b"cover.jpg"
        self.put(audio, self.COVER_KEY, APEValue(name + b"\0" + cover.data, BINARY))


def pack_asf_picture(cover: Cover) -> bytes:
    """WM/Picture: type, size, MIME and description as null-terminated UTF-16LE, then the data."""
    return (struct.pack("<bI", FRONT_COVER, len(cover.data)) + (cover.mime + "\0").encode("utf-16-le")
            + "\0".encode("utf-16-le") + cover.data)


def unpack_asf_picture(raw: bytes) -> tuple[int, Cover]:
    picture_type, size = struct.unpack_from("<bI", raw)
    position, strings = 5, []
    for _ in range(2):  # MIME, description
        end = position
        while raw[end:end + 2] != b"\0\0":
            end += 2
        strings.append(raw[position:end].decode("utf-16-le"))
        position = end + 2
    data = raw[position:position + size]
    return picture_type, Cover(data, strings[0] or guess_mime(data))


class _ASFAdapter(_Adapter):
    family = TagFamily.ASF
    KEYS = {"title": "Title", "artist": "Author", "albumartist": "WM/AlbumArtist", "album": "WM/AlbumTitle",
            "year": "WM/Year", "genre": "WM/Genre", "composer": "WM/Composer", "comment": "Description",
            "lyrics": "WM/Lyrics"}

    def read(self, audio) -> dict:
        tags = audio.tags
        values: dict[str, str | int | None] = {name: (str(tags[key][0]) if tags.get(key) else None)
                                               for name, key in self.KEYS.items()}
        track = tags.get("WM/TrackNumber")
        values["track"], values["track_total"] = (parse_int(track[0].value) if track else None), None
        disc = tags.get("WM/PartOfSet")
        values["disc"], values["disc_total"] = parse_pair(str(disc[0]) if disc else None)
        return values

    def put(self, audio, key, value) -> None:
        if key in audio.tags:
            del audio.tags[key]
        if value is not None:
            audio.tags[key] = [value]

    def write_text(self, audio, name, value) -> None:
        self.put(audio, self.KEYS[name], value)

    def write_pair(self, audio, name, number, total) -> None:
        if name == "track":
            if total is not None:
                raise TagError("WMA has no standard track total field")
            self.put(audio, "WM/TrackNumber", None if number is None else str(number))
        else:
            self.put(audio, "WM/PartOfSet", format_pair(number, total))

    def matching_keys(self, audio, key) -> list[str]:
        return [k for k in audio.tags.keys() if k.lower() == key.lower()]

    def get_replaygain(self, audio, key) -> str | None:
        keys = self.matching_keys(audio, key)
        return str(audio.tags[keys[0]][0]) if keys else None

    def set_replaygain(self, audio, key, value) -> None:
        for existing in self.matching_keys(audio, key):
            del audio.tags[existing]
        audio.tags[key.upper()] = [value]

    def pictures(self, audio) -> list[tuple[int, Cover]]:
        return [unpack_asf_picture(attr.value) for attr in audio.tags.get("WM/Picture", [])]

    def cover(self, audio) -> Cover | None:
        pictures = self.pictures(audio)
        chosen = next((c for t, c in pictures if t == FRONT_COVER), pictures[0][1] if pictures else None)
        return chosen

    def set_cover(self, audio, cover) -> None:
        attributes = list(audio.tags.get("WM/Picture", []))
        types = [t for t, _ in self.pictures(audio)]
        has_front = FRONT_COVER in types
        if has_front or cover is not None:
            kept = [a for a, t in zip(attributes, types, strict=True) if t != FRONT_COVER]
        else:
            kept = attributes[1:]
        if cover is not None:
            kept.insert(0, ASFByteArrayAttribute(pack_asf_picture(cover)))
        if "WM/Picture" in audio.tags:
            del audio.tags["WM/Picture"]
        if kept:
            audio.tags["WM/Picture"] = kept


class _NoTagsAdapter(_Adapter):
    family = TagFamily.NONE

    def read(self, audio) -> dict:
        return {}

    def refuse(self, *_args, **_kwargs) -> None:
        raise TagError("this format has no tags")

    write_text = write_pair = set_replaygain = set_comment = set_cover = refuse

    def get_replaygain(self, audio, key) -> str | None:
        return None

    def remove_replaygain(self, audio) -> int:
        return 0

    def cover(self, audio) -> Cover | None:
        return None


ADAPTERS: dict[TagFamily, _Adapter] = {
    adapter.family: adapter
    for adapter in (_VorbisAdapter(), _ID3Adapter(), _MP4Adapter(), _APEv2Adapter(), _ASFAdapter(), _NoTagsAdapter())
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class TaggedFile:
    """An audio file opened for tag reading and editing."""

    def __init__(self, path: str | Path, *, shared: bool = False) -> None:
        self.path = Path(path)
        info = format_for(self.path)
        if info is None:
            raise ValueError(f"{self.path.name}: not an audio file")
        self.info: FormatInfo = info
        self.audio = open_audio(self.path, shared=shared)  # shared: for reads only (see open_audio)
        self._adapter = ADAPTERS[info.family]

    def _wrap(self, error: Exception) -> TagError:
        return TagError(f"{self.path.name}: {error}")

    # -- fields --

    def raw_fields(self) -> dict:
        """Every field as stored, None when missing (number totals of 0 kept, as TinyTag does)."""
        values = self._adapter.read(self.audio)
        return {name: values.get(name) for name in FIELD_NAMES}

    def fields(self) -> Fields:
        values = self.raw_fields()
        for name in ("track_total", "disc_total"):
            if values[name] == 0:  # MP4 stores "no total" as 0
                values[name] = None
        return Fields(**values)

    def rename_fields(self) -> dict:
        """The fields rename masks can use, as the script's `read_fields` got them from TinyTag 2.0.0 (before
        sanitizing): numbers when present, text when not blank, kept as stored (not stripped)."""
        values = self.raw_fields()
        result = {name: values[name] for name in NUMBER_FIELDS if values[name] is not None}
        for name in RENAME_TEXT_FIELDS:
            value = values[name]
            if value is not None and str(value).strip():
                result[name] = str(value)
        return result

    def set_fields(self, changes: dict) -> None:
        """Change only the named fields; None clears one. Everything else in the file is left untouched."""
        unknown = set(changes) - set(FIELD_NAMES)
        if unknown:
            raise ValueError(f"unknown field(s): {', '.join(sorted(unknown))}")
        if not self.info.tags:
            raise self._wrap(TagError("this format has no tags"))
        current = self.raw_fields()
        try:
            for name, value in changes.items():
                if name in TEXT_FIELDS:
                    self._adapter.write_text(self.audio, name, value)
            for pair in ("track", "disc"):
                if pair in changes or f"{pair}_total" in changes:
                    number = changes.get(pair, current[pair])
                    total = changes.get(f"{pair}_total", current[f"{pair}_total"])
                    self._adapter.write_pair(self.audio, pair, number, total or None)
        except TagError as e:
            raise self._wrap(e) from None

    # -- ReplayGain and comment --

    def get_replaygain(self, key: str) -> str | None:
        """A ReplayGain value such as 'replaygain_track_gain', matched ignoring case."""
        return self._adapter.get_replaygain(self.audio, key)

    def set_replaygain(self, key: str, value: str) -> None:
        """Replace every case variant of the key, written in uppercase."""
        try:
            self._adapter.set_replaygain(self.audio, key, value)
        except TagError as e:
            raise self._wrap(e) from None

    def set_comment(self, text: str) -> None:
        try:
            self._adapter.set_comment(self.audio, text)
        except TagError as e:
            raise self._wrap(e) from None

    def remove_replaygain(self) -> int:
        """Delete the ReplayGain values (every REPLAYGAIN_* tag); the comment is the caller's business
        (core.replaygain.remove_replaygain). Returns how many tags went; save() writes it."""
        return self._adapter.remove_replaygain(self.audio)

    # -- cover --

    def cover(self) -> Cover | None:
        """The front cover, else the first embedded picture."""
        return self._adapter.cover(self.audio)

    def set_cover(self, data: bytes, mime: str | None = None) -> None:
        try:
            self._adapter.set_cover(self.audio, Cover(data, mime or guess_mime(data)))
        except TagError as e:
            raise self._wrap(e) from None

    def remove_cover(self) -> None:
        try:
            self._adapter.set_cover(self.audio, None)
        except TagError as e:
            raise self._wrap(e) from None

    # -- stream and saving --

    def stream_info(self) -> StreamInfo:
        info = self.audio.info
        codec = getattr(info, "codec_description", None) or self.info.container
        return StreamInfo(
            codec=codec,
            duration=float(getattr(info, "length", 0) or 0),
            bitrate=getattr(info, "bitrate", None) or None,
            sample_rate=getattr(info, "sample_rate", None) or None,
            bits_per_sample=getattr(info, "bits_per_sample", None) or None,
            channels=getattr(info, "channels", None) or None,
        )

    def save(self) -> None:
        self._adapter.save(self.audio)


def load(path: str | Path) -> TaggedFile:
    return TaggedFile(path)


