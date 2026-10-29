"""What a tag holds, family-independent: the editable fields (SPEC §7), cover, stream info, and the number
pairs' parsing."""

from dataclasses import dataclass
from dataclasses import fields as dataclass_fields

NUMBER_FIELDS = ("track", "track_total", "disc", "disc_total")
TEXT_FIELDS = ("title", "artist", "albumartist", "album", "year", "genre", "composer", "comment", "lyrics")
# The fields rename masks can use (the script's NUMBER_FIELDS + TEXT_FIELDS).
RENAME_TEXT_FIELDS = ("title", "artist", "albumartist", "album", "year", "genre", "composer")

FRONT_COVER = 3
TRACK_GAIN = "replaygain_track_gain"
TRACK_PEAK = "replaygain_track_peak"
ALBUM_GAIN = "replaygain_album_gain"
ALBUM_PEAK = "replaygain_album_peak"
REPLAYGAIN_KEYS = (TRACK_GAIN, TRACK_PEAK, ALBUM_GAIN, ALBUM_PEAK)  # what rsgain writes
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
