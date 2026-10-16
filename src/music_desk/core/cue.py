"""Cue sheets and the split plan (SPEC §22): read a single-file album's .cue, and work out each track's samples,
tags and file name before anything is written. No Qt, and nothing here writes; the job (with flac) does that.

A cue sheet gives times in CD frames (1/75 s). Each track runs from its INDEX 01 to the next track's INDEX 01, so a
gap (INDEX 00) stays at the end of the track before it, as EAC's "append gaps" does; every sample of the source
lands in exactly one track.
"""

import os
import re
import struct
import wave
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from music_desk.core import masks, preview
from music_desk.core.tags import TaggedFile

FRAMES_PER_SECOND = 75
HIDDEN_TITLE = "Hidden track"
FALLBACK_NAME = "Track"  # a track no mask fits is named "03 Track.flac"
HIDDEN_KEEP_SECONDS = 2  # audio before track 1 shorter than this (or silent) joins track 1
MAX_PATH = 259  # characters in a full path that Windows tools reliably open (MAX_PATH less its terminator)
SUPPORTED = {".flac": "FLAC", ".wav": "WAV", ".aif": "AIFF", ".aiff": "AIFF"}
# The encoding picker (SPEC §22): (codec, label). None is Auto: a BOM, then UTF-8, then Windows-1252.
ENCODINGS: tuple[tuple[str | None, str], ...] = (
    (None, "Auto"), ("utf-8", "UTF-8"), ("cp1252", "Windows-1252 (Western)"), ("cp1251", "Windows-1251 (Cyrillic)"),
    ("shift_jis", "Shift-JIS (Japanese)"), ("gbk", "GBK (Chinese, simplified)"),
    ("big5", "Big5 (Chinese, traditional)"), ("euc_kr", "EUC-KR (Korean)"))

_TIME = re.compile(r"^(\d+):(\d{1,2}):(\d{1,2})$")


class CueError(ValueError):
    """A sheet the split can't use; the message says why, in words for the user."""


# ---------------------------------------------------------------------------
# The sheet
# ---------------------------------------------------------------------------

@dataclass
class CueTrack:
    number: int
    file: str  # the FILE line it falls under
    title: str = ""
    performer: str = ""
    songwriter: str = ""
    isrc: str = ""
    index00: int | None = None  # in CD frames
    index01: int | None = None


@dataclass
class CueSheet:
    path: Path | None
    encoding: str  # its name, as the preview shows it
    fell_back: bool = False  # Auto had to use Windows-1252: the titles may be garbled
    title: str = ""
    performer: str = ""
    date: str = ""
    genre: str = ""
    disc: int | None = None
    disc_total: int | None = None
    files: list[str] = field(default_factory=list)  # every FILE name, in order
    tracks: list[CueTrack] = field(default_factory=list)  # audio tracks only
    data_tracks: int = 0  # MODE1/2352 and other non-audio tracks, skipped
    warnings: list[str] = field(default_factory=list)


def frames_from_time(text: str) -> int:
    """'mm:ss:ff' -> CD frames. Raises CueError."""
    match = _TIME.match(text.strip())
    if not match:
        raise CueError(f"{text!r} isn't a cue time (mm:ss:ff)")
    minutes, seconds, frames = map(int, match.groups())
    if seconds >= 60 or frames >= FRAMES_PER_SECOND:
        raise CueError(f"{text!r} isn't a cue time (mm:ss:ff)")
    return (minutes * 60 + seconds) * FRAMES_PER_SECOND + frames


def samples_from_frames(frames: int, rate: int) -> int:
    """CD frames -> samples at `rate`, rounded half up: exact at 44.1 kHz (588 a frame) and 48 kHz (640)."""
    return (2 * frames * rate + FRAMES_PER_SECOND) // (2 * FRAMES_PER_SECOND)


def _value(rest: str) -> str:
    """A cue value: the text between quotes, or the rest of the line."""
    rest = rest.strip()
    if rest.startswith('"'):
        end = rest.find('"', 1)
        return rest[1:end] if end > 0 else rest[1:]
    return rest


def _file_name(rest: str) -> str:
    """FILE "name with spaces.flac" WAVE -> the name (the type is the last word)."""
    rest = rest.strip()
    if rest.startswith('"'):
        return _value(rest)
    return rest.rsplit(None, 1)[0] if " " in rest else rest


def _number(text: str) -> int | None:
    text = text.strip().split("/")[0]
    return int(text) if text.isdigit() else None


def parse_cue(text: str, encoding: str = "UTF-8", path: Path | None = None) -> CueSheet:
    """The sheet's album fields, files and audio tracks. Unknown lines are ignored, as players do."""
    sheet = CueSheet(path, encoding)
    track: CueTrack | None = None
    in_data_track = False
    current_file = ""
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        keyword, _, rest = line.partition(" ")
        keyword = keyword.upper()
        if keyword == "REM":
            key, _, value = rest.strip().partition(" ")
            key, value = key.upper(), _value(value)
            if track is None and not in_data_track:
                if key == "DATE":
                    sheet.date = value
                elif key == "GENRE":
                    sheet.genre = value
                elif key == "DISCNUMBER":
                    sheet.disc = _number(value)
                elif key == "TOTALDISCS":
                    sheet.disc_total = _number(value)
        elif keyword == "FILE":
            current_file = _file_name(rest)
            sheet.files.append(current_file)
        elif keyword == "TRACK":
            parts = rest.split()
            in_data_track = len(parts) < 2 or parts[1].upper() != "AUDIO"
            if in_data_track:
                sheet.data_tracks += 1
                track = None
                continue
            if not parts[0].isdigit():
                raise CueError(f"line {number}: {line!r} has no track number")
            track = CueTrack(int(parts[0]), current_file)
            sheet.tracks.append(track)
        elif in_data_track:
            continue
        elif keyword in ("TITLE", "PERFORMER", "SONGWRITER"):
            target = track if track is not None else sheet
            attribute = {"TITLE": "title", "PERFORMER": "performer", "SONGWRITER": "songwriter"}[keyword]
            if target is sheet and attribute == "songwriter":
                continue  # an album songwriter has no field of its own
            setattr(target, attribute, _value(rest))
        elif keyword == "ISRC" and track is not None:
            track.isrc = _value(rest)
        elif keyword == "INDEX" and track is not None:
            parts = rest.split()
            if len(parts) != 2 or not parts[0].isdigit():
                raise CueError(f"line {number}: {line!r} isn't an INDEX line")
            frames = frames_from_time(parts[1])
            if int(parts[0]) == 0:
                track.index00 = frames
            elif int(parts[0]) == 1:
                track.index01 = frames
    for t in sheet.tracks:
        if t.index01 is None:
            raise CueError(f"track {t.number} has no INDEX 01, so its start is unknown")
    return sheet


def read_cue_sheet(path: str | Path, encoding: str | None = None) -> CueSheet:
    """Read a .cue. `encoding` None is Auto (SPEC §22): a BOM, then UTF-8, then Windows-1252. Raises OSError,
    CueError."""
    path = Path(path)
    data = path.read_bytes()
    if encoding is None:
        text, name = preview.decode(data)
        sheet = parse_cue(text, name, path)
        sheet.fell_back = name == "Windows-1252"
        return sheet
    label = dict(ENCODINGS).get(encoding, encoding)
    codec = "utf-8-sig" if encoding == "utf-8" else encoding  # a BOM is dropped
    return parse_cue(data.decode(codec, errors="replace"), label, path)


# ---------------------------------------------------------------------------
# The source audio
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceInfo:
    format: str  # "FLAC", "WAV" or "AIFF"
    rate: int
    samples: int  # per channel
    channels: int
    bits: int
    md5: str | None = None  # the FLAC STREAMINFO MD5, when set
    tags: dict = field(default_factory=dict)  # album-level rename fields the sheet may lack
    cover: bytes | None = None  # the embedded front cover, copied into every track


def read_source(path: str | Path) -> SourceInfo:
    """The audio file a sheet points at. Raises CueError for a format the split doesn't read, OSError."""
    path = Path(path)
    kind = SUPPORTED.get(path.suffix.lower())
    if kind is None:
        name = path.suffix[1:].upper() or "This file type"
        raise CueError(f"{name} isn't supported: the split reads FLAC, WAV and AIFF.")
    if kind == "FLAC":
        from mutagen.flac import FLAC

        info = FLAC(str(path)).info
        md5 = f"{info.md5_signature:032x}" if info.md5_signature else None
        rate, samples, channels, bits = info.sample_rate, info.total_samples, info.channels, info.bits_per_sample
    elif kind == "WAV":
        rate, samples, channels, bits = _wav_info(path)
        md5 = None
    else:
        rate, samples, channels, bits = _aiff_info(path)
        md5 = None
    tags, cover = {}, None
    try:
        tagged = TaggedFile(path, shared=True)
        tags = {k: v for k, v in tagged.rename_fields().items() if k in ("album", "albumartist", "year", "genre")}
        front = tagged.cover()
        cover = front.data if front else None
    except Exception:  # noqa: BLE001 - a source without readable tags just gives no fallback values
        pass
    return SourceInfo(kind, rate, samples, channels, bits, md5, tags, cover)


def _wav_info(path: Path) -> tuple[int, int, int, int]:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getframerate(), w.getnframes(), w.getnchannels(), w.getsampwidth() * 8
    except wave.Error as e:
        if "unknown format: 3" in str(e) or "unknown extended format" in str(e):  # IEEE float, plain or extensible
            raise CueError("32-bit float WAV isn't supported: FLAC can't hold floating-point audio.") from None
        raise CueError(f"{path.name} can't be read as a WAV file ({e}).") from None


def _aiff_info(path: Path) -> tuple[int, int, int, int]:
    """The COMM chunk of an AIFF file (Python 3.13 has no aifc module)."""
    with open(path, "rb") as f:
        header = f.read(12)
        if header[:4] != b"FORM" or header[8:12] not in (b"AIFF", b"AIFC"):
            raise CueError(f"{path.name} isn't an AIFF file.")
        while chunk := f.read(8):
            if len(chunk) < 8:
                break
            kind, size = chunk[:4], struct.unpack(">I", chunk[4:])[0]
            if kind == b"COMM":
                data = f.read(size)
                channels, frames, bits = struct.unpack(">hIh", data[:8])
                exponent, mantissa = struct.unpack(">HQ", data[8:18])  # an 80-bit extended float
                rate = round(mantissa * 2.0 ** ((exponent & 0x7FFF) - 16383 - 63))
                return rate, frames, channels, bits
            f.seek(size + (size & 1), os.SEEK_CUR)
    raise CueError(f"{path.name} has no COMM chunk.")


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------

@dataclass
class PlannedTrack:
    number: int  # 0 for a hidden track
    start: int  # first sample
    end: int  # one past the last sample
    fields: dict  # rename fields, as TaggedFile.rename_fields gives them (the masks' input)
    isrc: str = ""
    name: str = ""  # "" when no mask fits an unticked track
    path: Path | None = None
    row: int | None = None  # its row among the sheet's audio tracks; None for a hidden track

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass
class AlbumPlan:
    cue: Path
    source: Path | None = None
    sheet: CueSheet | None = None
    info: SourceInfo | None = None
    tracks: list[PlannedTrack] = field(default_factory=list)  # every track: they all set the boundaries
    refusal: str | None = None  # why the album is left alone; None when it can be split
    warnings: list[str] = field(default_factory=list)
    ticked: frozenset[int] | None = None  # the track numbers to write (0 is a hidden track); None for all
    problems: list[tuple[int, str]] = field(default_factory=list)  # (track number, why): a path too long
    notes: list[tuple[int, str]] = field(default_factory=list)  # (track number, how its name was changed)

    def is_ticked(self, number: int) -> bool:
        return self.ticked is None or number in self.ticked

    @property
    def written(self) -> list[PlannedTrack]:
        """The ticked tracks, in order: the ones the split writes."""
        return [t for t in self.tracks if self.is_ticked(t.number)]

    @property
    def all_ticked(self) -> bool:
        return len(self.written) == len(self.tracks)


def _source_in_folder(folder: Path, name: str) -> Path | None:
    """The FILE line's audio file, matched ignoring case as Windows does."""
    wanted = name.casefold()
    try:
        return next((folder / n for n in os.listdir(folder) if n.casefold() == wanted), None)
    except OSError:
        return None


def track_fields(sheet: CueSheet, track: CueTrack | None, number: int, total: int, fallback: dict) -> dict:
    """A track's rename fields from the sheet (SPEC §22), with the source's album values where the sheet has
    none. Numbers as numbers, text only when not blank: what TaggedFile.rename_fields would give back once
    written, so the masks name a split track as Rename would name it."""
    text = {
        "title": track.title if track else HIDDEN_TITLE,
        "artist": (track.performer if track else "") or sheet.performer or fallback.get("albumartist", ""),
        "albumartist": sheet.performer or fallback.get("albumartist", ""),
        "album": sheet.title or fallback.get("album", ""),
        "year": sheet.date or fallback.get("year", ""),
        "genre": sheet.genre or fallback.get("genre", ""),
        "composer": track.songwriter if track else "",
    }
    result: dict = {"track": number, "track_total": total}
    if sheet.disc is not None:
        result["disc"] = sheet.disc
    if sheet.disc_total is not None:
        result["disc_total"] = sheet.disc_total
    result.update({name: value for name, value in text.items() if value and value.strip()})
    return result


def plan_album(cue: str | Path, name_masks: Sequence[str], replacement: str, encoding: str | None = None,
               is_silent: Callable[[Path, int, int], bool] | None = None,
               read_info: Callable[[Path], SourceInfo] = read_source) -> AlbumPlan:
    """plan_sheet() for a .cue on disk."""
    cue = Path(cue)
    try:
        sheet = read_cue_sheet(cue, encoding)
    except (OSError, CueError) as e:
        return AlbumPlan(cue, refusal=f"The cue sheet can't be read: {e}")
    return plan_sheet(sheet, cue, name_masks, replacement, is_silent=is_silent, read_info=read_info)


def plan_sheet(sheet: CueSheet, cue: str | Path, name_masks: Sequence[str], replacement: str, *,
               ticked: Iterable[int] | None = None, is_silent: Callable[[Path, int, int], bool] | None = None,
               read_info: Callable[[Path], SourceInfo] = read_source) -> AlbumPlan:
    """What splitting this sheet would write, or why it can't be split. Nothing on disk changes. `cue` is where
    the sheet lives (its folder holds the source); the sheet itself may hold the Split page's unsaved edits.

    `ticked` are the track numbers to write (None: all). Unticked tracks still set the boundaries, and the tags'
    track total is the sheet's, whatever is ticked. `is_silent(source, start, end)` decodes that range (the
    job's flac); without it, audio before track 1 that is 2 s or longer is kept as a hidden track (no sample is
    lost either way)."""
    cue = Path(cue)
    plan = AlbumPlan(cue, ticked=frozenset(ticked) if ticked is not None else None)
    plan.sheet = sheet
    if sheet.fell_back:
        plan.warnings.append("If the titles look wrong, pick the cue sheet's encoding.")
    if sheet.data_tracks:
        plan.warnings.append(f"{sheet.data_tracks} data track{'s' if sheet.data_tracks > 1 else ''} skipped.")
    names = {n.casefold() for n in sheet.files}
    if len(names) != 1:
        plan.refusal = ("Not a single-file album: the sheet names several files." if names
                        else "Not a single-file album: the sheet names no file.")
        return plan
    source = _source_in_folder(cue.parent, sheet.files[0])
    if source is None:
        plan.refusal = f"Not a single-file album: {sheet.files[0]} isn't in the folder."
        return plan
    plan.source = source
    if len(sheet.tracks) < 2:
        plan.refusal = "The sheet has fewer than two audio tracks."
        return plan
    try:
        info = read_info(source)
    except CueError as e:
        plan.refusal = str(e)
        return plan
    except Exception as e:  # noqa: BLE001 - an unreadable source: say so, leave the album alone
        plan.refusal = f"{source.name} can't be read ({type(e).__name__}: {e})."
        return plan
    plan.info = info

    starts = [samples_from_frames(t.index01, info.rate) for t in sheet.tracks]
    for i in range(1, len(starts)):
        if starts[i] <= starts[i - 1]:
            plan.refusal = (f"Track {sheet.tracks[i].number} doesn't start after track {sheet.tracks[i - 1].number}: "
                            "the times must increase.")
            return plan
    if starts[-1] >= info.samples:
        late = next(t for t, s in zip(sheet.tracks, starts, strict=True) if s >= info.samples)
        plan.refusal = (f"Track {late.number} starts after the end of the audio "
                        f"({_clock(starts[-1], info.rate)} of {_clock(info.samples, info.rate)}).")
        return plan

    total = len(sheet.tracks)
    ends = starts[1:] + [info.samples]
    tracks = [PlannedTrack(t.number, s, e, track_fields(sheet, t, t.number, total, info.tags), t.isrc, row=row)
              for row, (t, s, e) in enumerate(zip(sheet.tracks, starts, ends, strict=True))]
    if starts[0] > 0:
        audible = starts[0] >= HIDDEN_KEEP_SECONDS * info.rate and not (
            is_silent is not None and is_silent(source, 0, starts[0]))
        if audible:
            tracks.insert(0, PlannedTrack(0, 0, starts[0], track_fields(sheet, None, 0, total, info.tags)))
        else:
            tracks[0].start = 0  # a short or silent pregap joins track 1
    plan.tracks = tracks

    problem = name_tracks(plan, name_masks, replacement)
    if problem:
        plan.refusal = problem
    return plan


def name_tracks(plan: AlbumPlan, name_masks: Sequence[str], replacement: str,
                existing: Iterable[str] | None = None) -> str | None:
    """Give each track its file name with the shared masks (SPEC §21). Nothing is ever overwritten, and the
    split never refuses over a name (Hugo, 2026-10-17): a ticked track whose name is taken, in the folder or by
    an earlier ticked track, gets " (2)", " (3)"…, and a track no mask fits is named from its number ("03
    Track.flac"). `plan.notes` says which names changed and why. Returns why the album can't be split (a path
    too long for Windows, no track ticked), or None. Unticked tracks are named, and not checked."""
    folder = plan.cue.parent
    if existing is None:
        try:
            existing = os.listdir(folder)
        except OSError:
            existing = []
    taken = {name.casefold() for name in existing}
    planned: set[str] = set()  # the ticked tracks' names so far
    refusals = []
    plan.problems, plan.notes = [], []
    for track in plan.tracks:
        ticked = plan.is_ticked(track.number)
        name = masks.name_for(track.fields, name_masks, replacement, ".flac")
        if name is None:
            missing = masks.missing_from(name_masks[0], masks.mask_values(track.fields, replacement))
            name = f"{track.number:02d} {FALLBACK_NAME}.flac"
            if ticked:
                plan.notes.append((track.number, f"No file name mask fits (missing: {missing}), so it's named "
                                                 f"{name}."))
        if ticked:
            free = free_name(name, taken | planned)
            if free != name:
                why = "another ticked track has that name" if name.casefold() in planned else "it already exists"
                plan.notes.append((track.number, f"{name} is taken ({why}), so it's named {free}."))
                name = free
            planned.add(name.casefold())
        track.name, track.path = name, folder / name
        if ticked and len(str(track.path)) > MAX_PATH:
            refusals.append(f"Track {track.number}'s file would have a path of {len(str(track.path))} characters, "
                            f"longer than Windows tools can open ({MAX_PATH}): {name}. Shorten the masks or move "
                            "the album.")
            plan.problems.append((track.number, f"The path would have {len(str(track.path))} characters, longer "
                                                f"than Windows tools can open ({MAX_PATH})."))
    if refusals:
        return refusals[0]
    if not plan.written:
        return "No track is ticked."
    return None


def free_name(name: str, taken: set[str]) -> str:
    """`name`, or "name (2).ext", "name (3).ext"… (Explorer's style for copies): the first one not in `taken`
    (casefolded names)."""
    if name.casefold() not in taken:
        return name
    stem, ext = os.path.splitext(name)
    n = 2
    while f"{stem} ({n}){ext}".casefold() in taken:
        n += 1
    return f"{stem} ({n}){ext}"


def find_albums(folder: str | Path, recursive: bool = True) -> list[Path]:
    """The .cue sheets in a folder (and its subfolders), in Explorer's order. Each is planned; the ones that
    aren't single-file albums are listed as such and left alone (SPEC §22)."""
    from music_desk.core.fsops import natural_key

    folder = Path(folder)
    found = []
    for root, dirs, files in os.walk(folder):
        dirs.sort(key=natural_key)
        found += [Path(root) / name for name in sorted(files, key=natural_key) if name.lower().endswith(".cue")]
        if not recursive:
            break
    return found


def _clock(samples: int, rate: int) -> str:
    seconds = samples / rate
    return f"{int(seconds // 60)}:{seconds % 60:05.2f}"
