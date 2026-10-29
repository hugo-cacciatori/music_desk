"""Tag reading and writing for every format, through one adapter per tag family (SPEC §1, §7, §17).

Replaces the script's `load_audio`, `get_tag`, `set_tag`, `set_comment` and `find_txxx` (FLAC/MP3 only) and its
TinyTag read, keeping their semantics: keys match ignoring case, ReplayGain keys are written in uppercase, and
`rename_fields()` returns what TinyTag 2.0.0 would.

Writes stay in memory until `save()`, as with mutagen. One module per tag family; this package exports what the
rest of the app uses.
"""
from refrain.core.tags.asf import (
    pack_asf_picture,
    unpack_asf_picture,
)
from refrain.core.tags.fields import (
    ALBUM_GAIN,
    ALBUM_PEAK,
    FIELD_NAMES,
    FRONT_COVER,
    NUMBER_FIELDS,
    RENAME_TEXT_FIELDS,
    REPLAYGAIN_KEYS,
    REPLAYGAIN_PREFIX,
    TEXT_FIELDS,
    TRACK_GAIN,
    TRACK_PEAK,
    Cover,
    Fields,
    StreamInfo,
    TagError,
    format_pair,
    guess_mime,
    parse_int,
    parse_pair,
)
from refrain.core.tags.legacy_text import (
    decode_latin1_text,
)
from refrain.core.tags.tagged_file import (
    ADAPTERS,
    TaggedFile,
    load,
)

__all__ = [
    "ALBUM_GAIN",
    "ALBUM_PEAK",
    "REPLAYGAIN_KEYS",
    "TRACK_GAIN",
    "TRACK_PEAK",
    "ADAPTERS",
    "Cover",
    "FIELD_NAMES",
    "FRONT_COVER",
    "Fields",
    "NUMBER_FIELDS",
    "RENAME_TEXT_FIELDS",
    "REPLAYGAIN_PREFIX",
    "StreamInfo",
    "TEXT_FIELDS",
    "TagError",
    "TaggedFile",
    "decode_latin1_text",
    "format_pair",
    "guess_mime",
    "load",
    "pack_asf_picture",
    "parse_int",
    "parse_pair",
    "unpack_asf_picture",
]
