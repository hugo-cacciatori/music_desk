"""The supported audio formats (SPEC §1): the single source of truth for extensions, tag families and capabilities.

Every extension here counts as audio everywhere: browsing, columns, indexing, lyrics and the Rename tool.
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen import FileType
from mutagen.aac import AAC
from mutagen.aiff import AIFF
from mutagen.asf import ASF
from mutagen.dsdiff import DSDIFF
from mutagen.dsf import DSF
from mutagen.flac import FLAC
from mutagen.monkeysaudio import MonkeysAudio
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.musepack import Musepack
from mutagen.oggflac import OggFLAC
from mutagen.oggopus import OggOpus
from mutagen.oggspeex import OggSpeex
from mutagen.oggvorbis import OggVorbis
from mutagen.optimfrog import OptimFROG
from mutagen.tak import TAK
from mutagen.trueaudio import TrueAudio
from mutagen.wave import WAVE
from mutagen.wavpack import WavPack


class TagFamily(StrEnum):
    VORBIS = "Vorbis"
    ID3 = "ID3"
    MP4 = "MP4"
    APEV2 = "APEv2"
    ASF = "ASF"
    NONE = "none"


@dataclass(frozen=True)
class FormatInfo:
    extensions: tuple[str, ...]
    container: str
    family: TagFamily
    # Candidate mutagen classes; mutagen picks by content when there are several (.ogg/.oga).
    mutagen_classes: tuple[type[FileType], ...]
    tags: bool  # tags, lyrics and cover are editable
    replaygain: bool  # rsgain can measure and tag it

    @property
    def cover(self) -> bool:
        return self.tags


FORMATS: tuple[FormatInfo, ...] = (
    FormatInfo((".flac",), "FLAC", TagFamily.VORBIS, (FLAC,), True, True),
    FormatInfo((".ogg", ".oga"), "Ogg Vorbis, Ogg FLAC", TagFamily.VORBIS, (OggVorbis, OggFLAC), True, True),
    FormatInfo((".opus",), "Ogg Opus", TagFamily.VORBIS, (OggOpus,), True, True),
    FormatInfo((".spx",), "Ogg Speex", TagFamily.VORBIS, (OggSpeex,), True, True),
    FormatInfo((".mp3", ".mp2"), "MPEG audio", TagFamily.ID3, (MP3,), True, True),
    FormatInfo((".wav",), "RIFF WAVE + ID3 chunk", TagFamily.ID3, (WAVE,), True, True),
    FormatInfo((".aif", ".aiff", ".aifc"), "AIFF + ID3 chunk", TagFamily.ID3, (AIFF,), True, True),
    FormatInfo((".m4a", ".mp4"), "MP4 (AAC, ALAC)", TagFamily.MP4, (MP4,), True, True),
    FormatInfo((".m4b",), "MP4 audiobook", TagFamily.MP4, (MP4,), True, False),
    FormatInfo((".ape",), "Monkey's Audio", TagFamily.APEV2, (MonkeysAudio,), True, True),
    FormatInfo((".wv",), "WavPack", TagFamily.APEV2, (WavPack,), True, True),
    FormatInfo((".mpc",), "Musepack", TagFamily.APEV2, (Musepack,), True, True),
    FormatInfo((".tak",), "TAK", TagFamily.APEV2, (TAK,), True, True),
    FormatInfo((".ofr", ".ofs"), "OptimFROG", TagFamily.APEV2, (OptimFROG,), True, False),
    FormatInfo((".tta",), "True Audio", TagFamily.ID3, (TrueAudio,), True, False),
    FormatInfo((".wma",), "ASF", TagFamily.ASF, (ASF,), True, True),
    FormatInfo((".dsf",), "DSF", TagFamily.ID3, (DSF,), True, False),
    FormatInfo((".dff",), "DSDIFF + ID3 chunk", TagFamily.ID3, (DSDIFF,), True, False),
    FormatInfo((".aac",), "raw ADTS", TagFamily.NONE, (AAC,), False, False),
)

_BY_EXTENSION: dict[str, FormatInfo] = {ext: info for info in FORMATS for ext in info.extensions}

AUDIO_EXTENSIONS: frozenset[str] = frozenset(_BY_EXTENSION)
REPLAYGAIN_EXTENSIONS: frozenset[str] = frozenset(ext for ext, info in _BY_EXTENSION.items() if info.replaygain)


def format_for(path: str | Path) -> FormatInfo | None:
    """The format of a file, by extension (ignoring case), or None if it isn't audio."""
    return _BY_EXTENSION.get(Path(path).suffix.lower())


def is_audio(path: str | Path) -> bool:
    return format_for(path) is not None


def open_audio(path: str | Path) -> FileType:
    """Open an audio file with its format's mutagen class. Raises ValueError for non-audio or unreadable files."""
    info = format_for(path)
    if info is None:
        raise ValueError(f"{Path(path).name}: not an audio file")
    audio = MutagenFile(path, options=list(info.mutagen_classes))
    if audio is None:
        raise ValueError(f"{Path(path).name}: not a valid {info.container} file")
    return audio
