"""What the Metadata tab shows and saves for one file (SPEC §7): a snapshot of its tags, stream details,
loudness values and cover, and the write of the changed fields. Plain Python: the UI calls these on a worker
thread, never on its own."""

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from music_desk.core import masks
from music_desk.core.formats import format_for
from music_desk.core.fsops import open_shared
from music_desk.core.tags import Fields, TaggedFile

FOLDER_COVERS = ("folder.jpg", "cover.jpg", "front.jpg")  # the folder images used when no cover is embedded
RG_KEYS = ("replaygain_track_gain", "replaygain_track_peak", "replaygain_album_gain", "replaygain_album_peak")
LUFS_PREFIX = "LUFS Target"
LOSSLESS_EXTENSIONS = frozenset({".flac", ".wav", ".aif", ".aiff", ".aifc", ".wv", ".tta", ".ape", ".tak", ".ofr",
                                 ".ofs", ".dsf", ".dff"})  # the ReplayGain tool's comment (SPEC §10B)


@dataclass(frozen=True)
class Snapshot:
    path: Path
    size: int
    mtime: float
    editable: bool  # the format has tags (raw .aac doesn't)
    family: str
    container: str
    fields: Fields
    codec: str
    duration: float
    bitrate: int | None
    sample_rate: int | None
    bits_per_sample: int | None
    channels: int | None
    replaygain: dict[str, str | None]
    cover: bytes | None
    cover_source: str  # "embedded", the folder image's file name, or "" for none
    id3_version: str | None  # "2.3" / "2.4" for ID3 files
    suggested_name: str | None = None  # the name the rename masks give, when it differs (SPEC §7's hint)

    @property
    def format_line(self) -> str:
        """e.g. "FLAC 16-bit/44.1 kHz · 4:44 · 32 MB"."""
        quality = []
        if self.lossless and self.bits_per_sample:
            quality.append(f"{self.bits_per_sample}-bit")
        elif not self.lossless and self.bitrate:
            quality.append(f"{round(self.bitrate / 1000)} kbps")
        if self.sample_rate:
            quality.append(f"{self.sample_rate / 1000:g} kHz")
        head = self.path.suffix.lstrip(".").upper()
        line = [f"{head} {'/'.join(quality)}" if quality else head]
        if self.duration:
            total = round(self.duration)
            minutes, seconds = divmod(total, 60)
            line.append(f"{minutes // 60}:{minutes % 60:02d}:{seconds:02d}" if minutes >= 60
                        else f"{minutes}:{seconds:02d}")
        line.append(_size(self.size))
        return " · ".join(line)

    @property
    def lossless(self) -> bool:
        """Bit depth means something only for lossless audio (mutagen reports 16 for AAC too)."""
        ext = self.path.suffix.lower()
        return ext in LOSSLESS_EXTENSIONS or self.codec in ("ALAC", "FLAC") or (ext == ".oga" and "FLAC" in self.codec)

    @property
    def lufs_comment(self) -> str | None:
        comment = self.fields.comment
        return comment if comment and comment.startswith(LUFS_PREFIX) else None


def _size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit in ("B", "KB") or value >= 100 else f"{value:.1f} {unit}"
        value /= 1024
    return ""


def suggested_name(tagged: TaggedFile, name_masks: Sequence[str] = masks.DEFAULT_MASKS,
                   replacement: str = masks.DEFAULT_REPLACEMENT) -> str | None:
    """What the user's file name masks (SPEC §21) would name this file, if that differs from its name. None
    while the masks are invalid: the File names card says so."""
    if not tagged.info.tags or masks.validate_masks(name_masks, replacement):
        return None
    name = masks.name_for(tagged.rename_fields(), name_masks, replacement, tagged.path.suffix)
    return None if name is None or name == tagged.path.name else name


def folder_cover(folder: Path) -> tuple[bytes, str] | None:
    """The first folder image (folder.jpg, cover.jpg, front.jpg), matched ignoring case."""
    try:
        names = {name.casefold(): name for name in os.listdir(folder)}
    except OSError:
        return None
    for wanted in FOLDER_COVERS:
        name = names.get(wanted)
        if name is not None:
            try:
                with open_shared(folder / name) as image:
                    return image.read(), name
            except OSError:
                continue
    return None


def read_snapshot(path: str | Path, name_masks: Sequence[str] = masks.DEFAULT_MASKS,
                  replacement: str = masks.DEFAULT_REPLACEMENT) -> Snapshot:
    """Everything the tab shows, with the rename hint from these masks. Raises OSError/ValueError (mutagen errors
    included) for unreadable files."""
    path = Path(path)
    stat = path.stat()
    tagged = TaggedFile(path, shared=True)
    stream = tagged.stream_info()
    embedded = tagged.cover()
    cover, source = (embedded.data, "embedded") if embedded else (folder_cover(path.parent) or (None, ""))
    tags = getattr(tagged.audio, "tags", None)
    id3_version = None
    if tagged.info.family.value == "ID3" and tags is not None and hasattr(tags, "version"):
        id3_version = f"2.{tags.version[1]}"
    suggestion = suggested_name(tagged, name_masks, replacement)
    return Snapshot(
        path=path, size=stat.st_size, mtime=stat.st_mtime, editable=tagged.info.tags,
        family=tagged.info.family.value, container=tagged.info.container, fields=tagged.fields(),
        codec=stream.codec, duration=stream.duration, bitrate=stream.bitrate, sample_rate=stream.sample_rate,
        bits_per_sample=stream.bits_per_sample, channels=stream.channels,
        replaygain={key: tagged.get_replaygain(key) for key in RG_KEYS},
        cover=cover, cover_source=source, id3_version=id3_version,
        suggested_name=suggestion,
    )


COVER = "cover"  # in a changes dict: new front-cover bytes, or None to remove the embedded front cover
LARGE_COVER = 1024 * 1024  # bytes: bigger covers get a warning, since every file carries its own copy
COVER_EXTENSIONS = (".jpg", ".jpeg", ".png")
JPEG_MAGIC, PNG_MAGIC = bytes([0xFF, 0xD8]), bytes([0x89]) + b"PNG"


def write_fields(path: str | Path, changes: dict, name_masks: Sequence[str] = masks.DEFAULT_MASKS,
                 replacement: str = masks.DEFAULT_REPLACEMENT) -> Snapshot:
    """Write only the changed fields (None clears one) and, under COVER, the embedded front cover (other
    pictures are kept), keeping everything else and the ID3 version; return the fresh snapshot."""
    if changes:
        fields = {k: v for k, v in changes.items() if k != COVER}
        tagged = TaggedFile(path)
        if fields:
            tagged.set_fields(fields)
        if COVER in changes:
            if changes[COVER] is None:
                tagged.remove_cover()
            else:
                tagged.set_cover(changes[COVER])
        tagged.save()
    return read_snapshot(path, name_masks, replacement)


def folder_picture(folder: str | Path, first_track: str | Path | None) -> tuple[bytes, str] | None:
    """The folder summary's cover: the folder image, else the first track's embedded front cover."""
    found = folder_cover(Path(folder))
    if found is not None:
        return found
    if first_track is not None:
        try:
            cover = TaggedFile(first_track, shared=True).cover()
        except (OSError, ValueError):
            return None
        if cover is not None:
            return cover.data, Path(first_track).name
    return None


def embedded_cover(snapshot: Snapshot) -> bytes | None:
    """The embedded front cover (not a folder image shown in its place)."""
    return snapshot.cover if snapshot.cover_source == "embedded" else None


def read_image(path: str | Path) -> bytes:
    """A picture chosen as the new cover. Raises ValueError for something that isn't a jpg or png."""
    from music_desk.core.fsops import open_shared

    path = Path(path)
    if path.suffix.lower() not in COVER_EXTENSIONS:
        raise ValueError(f"{path.name} isn't a JPEG or PNG picture")
    with open_shared(path) as f:
        data = f.read()
    if not (data.startswith(JPEG_MAGIC) or data.startswith(PNG_MAGIC)):
        raise ValueError(f"{path.name} isn't a valid JPEG or PNG picture")
    return data


def is_audio(path: str | Path) -> bool:
    return format_for(path) is not None
