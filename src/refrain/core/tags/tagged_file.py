"""`TaggedFile`: an audio file opened for tag reading and editing, through its family's adapter."""

from pathlib import Path

from refrain.core.formats import FormatInfo, TagFamily, format_for, open_audio
from refrain.core.tags.adapter import Adapter
from refrain.core.tags.apev2 import APEv2Adapter
from refrain.core.tags.asf import ASFAdapter
from refrain.core.tags.fields import (
    FIELD_NAMES,
    NUMBER_FIELDS,
    RENAME_TEXT_FIELDS,
    TEXT_FIELDS,
    Cover,
    Fields,
    StreamInfo,
    TagError,
    guess_mime,
)
from refrain.core.tags.id3 import ID3Adapter
from refrain.core.tags.mp4 import MP4Adapter
from refrain.core.tags.vorbis import VorbisAdapter


class NoTagsAdapter(Adapter):
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


ADAPTERS: dict[TagFamily, Adapter] = {
    adapter.family: adapter
    for adapter in (VorbisAdapter(), ID3Adapter(), MP4Adapter(), APEv2Adapter(), ASFAdapter(), NoTagsAdapter())
}


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
