"""Trimming one track (SPEC §23): a FLAC copy of the part between a new start and a new end, written into a
subfolder of the track's folder. The original is never touched. No Qt.

A trim is a split of one track into two or three parts where only one is kept, so it cuts and proves through the
split's code (flac's `--skip`/`--until`, then the copy's length and the MD5 of its audio against the same range
of the original). The removed parts are not written anywhere.

The run writes "<name>.partial" first, proves it, then renames it and writes the tags. On a failure or a Stop it
deletes that .partial, and the subfolder too if this run created it and it is still empty (Ground rule 1).
"""

import hashlib
import math
import os
import re
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from music_desk.core import cue, split
from music_desk.core.cancel import CancelToken
from music_desk.core.cue import CueError, SourceInfo
from music_desk.core.flac import Cancelled, FlacError
from music_desk.core.inline_rename import RESERVED_NAMES

DEFAULT_SUBFOLDER = "lite"
OWNER = "the Trim tool"
NUDGE_MS, PAGE_MS = 10, 1000  # Up / Down, Page Up / Page Down in the time fields
# Lyrics are never copied, synced or not (SPEC §23): the app's lyrics come from LRCLIB, and synced lyrics would no
# longer match once the start moves.
LYRICS_KEYS = frozenset({"lyrics", "unsyncedlyrics", "syncedlyrics"})

_TIME = re.compile(r"^(\d+):([0-5]?\d)(?:\.(\d{1,3}))?$")
_FOLDER_FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


# ---------------------------------------------------------------------------
# Times
# ---------------------------------------------------------------------------

def parse_time(text: str) -> int | None:
    """`m:ss.mmm` (minutes may pass 59; `m:ss` and fewer decimals are fine) in milliseconds, or None."""
    match = _TIME.match(text.strip())
    if match is None:
        return None
    minutes, seconds, fraction = match.groups()
    return (int(minutes) * 60 + int(seconds)) * 1000 + int((fraction or "0").ljust(3, "0"))


def format_time(ms: int) -> str:
    seconds, millis = divmod(ms, 1000)
    return f"{seconds // 60}:{seconds % 60:02d}.{millis:03d}"


def to_samples(ms: int, rate: int) -> int:
    """sample = round(seconds · rate), halves rounded up, in integers so it is exact."""
    return (ms * rate * 2 + 1000) // 2000


def to_ms(samples: int, rate: int) -> int:
    return (samples * 2000 + rate) // (2 * rate)


def length_ms(info: SourceInfo) -> int:
    """The track's length as the page shows it, and the End field's starting value."""
    return to_ms(info.samples, info.rate)


# ---------------------------------------------------------------------------
# The source
# ---------------------------------------------------------------------------

def unsupported_reason(path: str | Path) -> str | None:
    """Why trimming can't read this file, from its extension alone (the buttons' tooltip), or None."""
    suffix = Path(path).suffix.lower()
    if suffix in cue.SUPPORTED:
        return None
    return f"{suffix[1:].upper() or 'This file type'} isn't supported: trimming reads FLAC, WAV and AIFF."


def read_source(path: str | Path) -> SourceInfo:
    """What the trim needs of the track. Raises CueError with the reason (another format, 32-bit float WAV),
    OSError, or mutagen's error for an unreadable file."""
    reason = unsupported_reason(path)
    if reason:
        raise CueError(reason)
    return cue.read_source(path)


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------

@dataclass
class TrimPlan:
    """What a trim would write, or why it can't: everything the Trim page shows and checks. Nothing on disk
    changes while planning."""

    source: Path
    info: SourceInfo
    start: int = 0  # first kept sample
    end: int = 0  # one past the last kept sample
    subfolder: str = DEFAULT_SUBFOLDER
    name: str = ""  # the copy's file name in the subfolder
    note: str | None = None  # why the name was numbered
    # (field, why): field is "start", "end", "subfolder", or None for the whole edit ("Nothing to trim").
    problems: list[tuple[str | None, str]] = field(default_factory=list)

    @property
    def folder(self) -> Path:
        return self.source.parent / self.subfolder

    @property
    def path(self) -> Path:
        return self.folder / self.name

    @property
    def shown_path(self) -> str:
        """The copy's path from the track's folder, as the page shows it: `lite\\03 Song.flac`."""
        return f"{self.subfolder}\\{self.name}"

    @property
    def length(self) -> int:
        return self.end - self.start

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def refusal(self) -> str | None:
        return self.problems[0][1] if self.problems else None

    def problem(self, name: str | None) -> str | None:
        """The first problem of one field, for marking it on the page."""
        return next((why for where, why in self.problems if where == name), None)

    @property
    def summary(self) -> str:
        """'Keeps 3:41.200. Removes 0:02.350 at the start and 5:12.000 at the end.'"""
        rate = self.info.rate
        keeps = f"Keeps {format_time(to_ms(self.length, rate))}."
        removed = [f"{format_time(to_ms(n, rate))} at the {where}"
                   for n, where in ((self.start, "start"), (self.info.samples - self.end, "end")) if n > 0]
        return f"{keeps} Removes {' and '.join(removed)}." if removed else keeps


def plan_trim(source: str | Path, info: SourceInfo, start: str, end: str, subfolder: str,
              existing: Iterable[str] | None = None) -> TrimPlan:
    """Check the page's values and work out the copy. `start` and `end` are the fields' text; `existing` is the
    subfolder's listing (None: read it, and nothing when it doesn't exist yet)."""
    source = Path(source)
    plan = TrimPlan(source, info, subfolder=subfolder)
    total_ms = length_ms(info)
    start_ms, end_ms = parse_time(start), parse_time(end)
    if start_ms is None:
        plan.problems.append(("start", "Type the start as m:ss.mmm, for example 0:02.350."))
    elif start_ms >= total_ms:
        plan.problems.append(("start", f"The start is past the track's length ({format_time(total_ms)})."))
    if end_ms is None:
        plan.problems.append(("end", "Type the end as m:ss.mmm, for example 3:43.550."))
    elif end_ms > total_ms:
        plan.problems.append(("end", f"The end is past the track's length ({format_time(total_ms)})."))
    if not plan.problems:
        assert start_ms is not None and end_ms is not None
        plan.start = to_samples(start_ms, info.rate)
        # The length as shown is rounded to the millisecond: an End left at it means the very last sample.
        plan.end = info.samples if end_ms == total_ms else min(to_samples(end_ms, info.rate), info.samples)
        if plan.start >= plan.end:
            plan.problems.append(("end", "The start must be before the end."))
        elif plan.start == 0 and plan.end == info.samples:
            plan.problems.append((None, "Nothing to trim: move a handle."))

    why = check_subfolder(source.parent, subfolder)
    if why:
        plan.problems.append(("subfolder", why))
        return plan
    if existing is None:
        try:
            existing = os.listdir(plan.folder)
        except OSError:
            existing = []
    wanted = source.with_suffix(".flac").name
    plan.name = cue.free_name(wanted, {n.casefold() for n in existing})
    if plan.name != wanted:
        plan.note = f"{wanted} is already in {subfolder}, so the copy is named {plan.name}."
    for partial in (plan.path, plan.path.with_name(plan.name + split.PARTIAL)):
        if len(str(partial)) > cue.MAX_PATH:
            plan.problems.append(("subfolder", f"The copy's path would have {len(str(partial))} characters, longer "
                                               f"than Windows tools can open ({cue.MAX_PATH}). Shorten the "
                                               "subfolder's name."))
            break
    return plan


def check_subfolder(folder: Path, name: str) -> str | None:
    """Why `name` can't be the subfolder of `folder` (None if it can). An existing folder of that name is fine."""
    if not name.strip():
        return "Type the subfolder's name."
    if _FOLDER_FORBIDDEN.search(name):
        return 'A folder name can\'t contain any of \\ / : * ? " < > |.'
    if name[-1] in " .":
        return "A folder name can't end with a dot or a space."
    if name.split(".")[0].strip().upper() in RESERVED_NAMES:
        return f"{name} is a name Windows reserves."
    target = folder / name
    if target.exists() and not target.is_dir():
        return f"{name} is a file in this folder, not a folder."
    return None


# ---------------------------------------------------------------------------
# The cut and its proof
# ---------------------------------------------------------------------------

@dataclass
class TrimResult:
    ok: bool = False
    cancelled: bool = False
    error: str | None = None  # flac's message, a failed proof, or why the trim wasn't started
    partial: Path | None = None  # the proved copy, when ok
    created_folder: bool = False  # this run made the subfolder (it is removed on a failure while empty)
    samples: int = 0  # in the copy, as decoded
    md5: str | None = None  # of the copy's audio, equal to its range of the original when ok


def partial_path(plan: TrimPlan) -> Path:
    return plan.path.with_name(plan.name + split.PARTIAL)


def cut_trim(plan: TrimPlan, runner, cancel: CancelToken | None = None,
             free_space: Callable[[Path], int] = lambda folder: shutil.disk_usage(folder).free) -> TrimResult:
    """Write and prove the kept range as `<name>.partial` in the subfolder, creating it if missing. Nothing else
    is touched; on a failure or a Stop, the folder is left as it was."""
    if not plan.ok:
        return TrimResult(error=plan.refusal)
    needed = math.ceil(plan.source.stat().st_size * plan.length / max(plan.info.samples, 1))
    free = free_space(plan.source.parent)
    if free < needed:
        return TrimResult(error=f"Not enough free space: the copy needs about {split.megabytes(needed)}, and the "
                                f"drive has {split.megabytes(free)} free.")
    output = partial_path(plan)
    if output.exists():
        return TrimResult(error=f"{plan.subfolder}\\{output.name} already exists, maybe left by an earlier trim. "
                                "Delete it in File Explorer, then trim again.")
    result = TrimResult(created_folder=not plan.folder.exists())
    try:
        plan.folder.mkdir(exist_ok=True)
    except OSError as e:
        return TrimResult(error=f"The subfolder {plan.subfolder} can't be created ({e.strerror or e}).")
    try:
        end = None if plan.end == plan.info.samples else plan.end
        runner.encode(plan.source, plan.start, end, output, cancel)
        problem = prove(plan, output, runner, result, cancel)
    except Cancelled:
        problem, result.cancelled = "Stopped.", True
    except FlacError as e:
        problem = f"flac: {e}"
    if problem:
        result.error = problem
        undo(plan, output, result.created_folder)
        return result
    result.ok, result.partial = True, output
    return result


def prove(plan: TrimPlan, copy: Path, runner, result: TrimResult, cancel: CancelToken | None = None) -> str | None:
    """The copy's sample count must be the kept range's length, and its audio's MD5 that of the same range of the
    original, decoded by flac (the split's per-track proof). Returns why it failed, or None."""
    info = plan.info
    frame_bytes = info.channels * ((info.bits + 7) // 8)
    own, part = hashlib.md5(), hashlib.md5()
    size = split.hash_chunks(runner.decode(copy, cancel), own)
    end = None if plan.end == info.samples else plan.end
    split.hash_chunks(runner.decode_range(plan.source, plan.start, end, cancel), part)
    result.samples, result.md5 = size // frame_bytes, own.hexdigest()
    if size % frame_bytes or size // frame_bytes != plan.length:
        return (f"The proof failed: the copy has {size // frame_bytes:,} samples, but the trim planned "
                f"{plan.length:,}.")
    if own.hexdigest() != part.hexdigest():
        return (f"The proof failed: the copy's audio doesn't match its part of the original (MD5 "
                f"{own.hexdigest()[:8]}… instead of {part.hexdigest()[:8]}…).")
    return None


def undo(plan: TrimPlan, partial: Path, created_folder: bool) -> None:
    """This run's .partial, and the subfolder if this run made it and nothing else is in it."""
    split.delete_partials([partial])
    if created_folder:
        try:
            plan.folder.rmdir()  # refuses a folder that isn't empty
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Finishing: the name and the tags
# ---------------------------------------------------------------------------

@dataclass
class Finished:
    path: Path | None = None
    error: str | None = None  # the name was taken meanwhile: nothing was renamed, the .partial is gone
    warnings: list[str] = field(default_factory=list)


def finish_trim(plan: TrimPlan, cut: TrimResult) -> Finished:
    """Give the proved copy its name (checked free again) and the original's tags. Nothing is ever overwritten,
    and nothing runs after it (a trim is just a trim)."""
    assert cut.ok and cut.partial is not None
    if plan.path.exists():
        undo(plan, cut.partial, cut.created_folder)
        return Finished(error=f"Nothing is ever overwritten, and {plan.shown_path} was created meanwhile.")
    cut.partial.rename(plan.path)
    finished = Finished(plan.path)
    try:
        copy_tags(plan.source, plan.path)
    except Exception as e:  # noqa: BLE001 - the audio is proved and in place; say the tags are missing
        finished.warnings.append(f"{plan.name}: the tags couldn't be copied ({type(e).__name__}: {e})")
    return finished


def copy_tags(source: Path, copy: Path) -> None:
    """The original's tags on the copy, without any lyrics (SPEC §23). A FLAC gives every Vorbis comment and every
    picture as they are, ReplayGain included; a WAV or AIFF gives the fields of §1, its ReplayGain values and its
    front cover. Whatever flac carried over itself is replaced, so the result never depends on flac's version."""
    from mutagen.flac import FLAC, VCFLACDict

    target = FLAC(str(copy))
    if target.tags is None:
        target.add_tags()
    tags = cast(VCFLACDict, target.tags)
    tags.clear()
    target.clear_pictures()
    if source.suffix.lower() == ".flac":
        original = FLAC(str(source))
        for key, value in cast(VCFLACDict | None, original.tags) or []:
            if key.casefold() not in LYRICS_KEYS:
                tags.append((key, value))
        for picture in original.pictures:
            target.add_picture(picture)
        target.save()
        return
    target.save()
    _copy_id3_tags(source, copy)


def _copy_id3_tags(source: Path, copy: Path) -> None:
    """A WAV's or AIFF's ID3 chunk, through TaggedFile: what the app reads of it is what the copy gets."""
    from music_desk.core.tags import REPLAYGAIN_PREFIX, TaggedFile

    original = TaggedFile(source, shared=True)
    target = TaggedFile(copy)
    fields = {name: value for name, value in original.raw_fields().items()
              if name != "lyrics" and value is not None}
    if fields:
        target.set_fields(fields)
    tags = getattr(original.audio, "tags", None)
    for frame in tags.getall("TXXX") if tags is not None else []:
        if frame.desc.casefold().startswith(REPLAYGAIN_PREFIX) and frame.text:
            target.set_replaygain(frame.desc, str(frame.text[0]))
    cover = original.cover()
    if cover is not None:
        target.set_cover(cover.data, cover.mime)
    target.save()
