"""What a scan reads from disk (SPEC §4): a folder's entries, without following links, and an audio file's
`tracks` row. Neither ever writes."""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from refrain.core.lyrics import cache_key, track_from_fields
from refrain.core.tags import REPLAYGAIN_KEYS, TaggedFile

log = logging.getLogger(__name__)

TRACK_COLUMNS = (
    "format", "tag_family", "title", "artist", "albumartist", "album", "track", "track_total", "disc", "disc_total",
    "date", "genre", "composer", "comment", "has_lyrics_tag", "has_cover", "duration", "bitrate", "sample_rate",
    "bit_depth", "channels", "rg_track_gain", "rg_track_peak", "rg_album_gain", "rg_album_peak", "lyrics_key",
    "read_size", "read_mtime", "read_error",
)


@dataclass
class Child:
    name: str
    is_dir: bool
    ext: str | None
    size: int | None
    mtime: float


def list_folder(path: str) -> list[Child]:
    """The folder's entries, without following junctions or symbolic links. Raises OSError if it can't be listed."""
    children = []
    with os.scandir(path) as entries:
        for entry in entries:
            try:
                if entry.is_symlink() or entry.is_junction():
                    log.debug("not following link %s", entry.path)
                    continue
                is_dir = entry.is_dir(follow_symlinks=False)
                stat = entry.stat(follow_symlinks=False)  # cached by scandir on Windows
            except OSError as e:
                log.warning("skipping %s: %s", entry.path, e)
                continue
            ext = None if is_dir else os.path.splitext(entry.name)[1].lower()
            children.append(Child(entry.name, is_dir, ext, None if is_dir else stat.st_size, stat.st_mtime))
    return children


def read_file(path: Path, size: int | None, mtime: float | None) -> tuple[dict, str | None]:
    """A `tracks` row for an audio file, and the lyrics text for the search index: always None, since the files'
    own lyrics tags are no longer shown or searched (M12, SPEC §26). Never raises."""
    row: dict[str, Any] = dict.fromkeys(TRACK_COLUMNS)
    row.update(format=path.suffix.lstrip(".").upper(), read_size=size, read_mtime=mtime,
               has_lyrics_tag=0, has_cover=0)
    try:
        tagged = TaggedFile(path, shared=True)  # never blocks a delete or rename of this file (SPEC §7)
        fields = tagged.fields()
        stream = tagged.stream_info()
        track = track_from_fields(fields, path.name, stream.duration)
        row.update(
            tag_family=tagged.info.family.value, title=fields.title, artist=fields.artist,
            albumartist=fields.albumartist, album=fields.album, track=fields.track, track_total=fields.track_total,
            disc=fields.disc, disc_total=fields.disc_total, date=fields.year, genre=fields.genre,
            composer=fields.composer, comment=fields.comment,
            has_cover=int(tagged.cover() is not None), duration=stream.duration, bitrate=stream.bitrate,
            sample_rate=stream.sample_rate, bit_depth=stream.bits_per_sample, channels=stream.channels,
            lyrics_key=cache_key(track.artist, track.title) if track.artist and track.title else None,
        )
        for key in REPLAYGAIN_KEYS:
            row["rg_" + key.removeprefix("replaygain_")] = tagged.get_replaygain(key)
        return row, None
    except Exception as e:  # noqa: BLE001 - a broken file is indexed with its error, the scan goes on
        row["read_error"] = f"{type(e).__name__}: {e}"
        return row, None
