"""Cutting a planned album into tracks, and proving nothing was lost (SPEC §22). No Qt.

Each track is written to "<its name>.partial" first. The proof then decodes every track:
  1. each track's sample count must equal its planned length (a cut in the wrong place);
  2. the MD5 of all the tracks' audio, end to end, must equal the source's (its FLAC STREAMINFO MD5, or the hash
     of its decoded audio).
On a failure or a Stop, this run's .partial files are deleted, and only those (Ground rule 1). Renaming them to
their final names and writing the tags come after, in the job.
"""

import hashlib
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from music_desk.core.cancel import CancelToken
from music_desk.core.cue import AlbumPlan, PlannedTrack
from music_desk.core.flac import Cancelled, FlacError

PARTIAL = ".partial"


def partial_path(track: PlannedTrack) -> Path:
    return track.path.with_name(track.name + PARTIAL)


@dataclass
class CutResult:
    ok: bool = False
    cancelled: bool = False
    error: str | None = None  # flac's message, a failed proof, or why the album wasn't started
    partials: list[Path] = field(default_factory=list)  # the verified tracks, in order, when ok
    lengths: list[int] = field(default_factory=list)  # samples per track, as decoded
    md5: str | None = None  # of the tracks end to end
    expected_md5: str | None = None  # of the source


def is_silent(runner, cancel: CancelToken | None = None) -> Callable[[Path, int, int], bool]:
    """`plan_album`'s silence check: decode just that range and look for any non-zero byte."""
    def check(source: Path, start: int, end: int) -> bool:
        return all(not chunk.strip(b"\0") for chunk in runner.decode_range(source, start, end, cancel))
    return check


def cut_album(plan: AlbumPlan, runner, cancel: CancelToken | None = None,
              free_space: Callable[[Path], int] = lambda folder: shutil.disk_usage(folder).free) -> CutResult:
    """Write and prove this album's tracks as .partial files. Nothing else in the folder is touched."""
    if plan.refusal is not None or plan.source is None or plan.info is None:
        return CutResult(error=plan.refusal or "The album wasn't planned.")
    folder = plan.cue.parent
    needed = plan.source.stat().st_size
    free = free_space(folder)
    if free < needed:
        return CutResult(error=f"Not enough free space: the tracks need about {_mb(needed)}, and the drive has "
                               f"{_mb(free)} free.")
    taken = [partial_path(t).name for t in plan.tracks if partial_path(t).exists()]
    if taken:
        return CutResult(error=f"{', '.join(taken)} already exist{'s' if len(taken) == 1 else ''}, maybe left by an "
                               "earlier split. Delete them in File Explorer, then split again.")

    written: list[Path] = []
    try:
        for track in plan.tracks:
            output = partial_path(track)
            written.append(output)  # recorded first: a flac killed mid-way may leave it behind
            end = None if track.end == plan.info.samples else track.end
            runner.encode(plan.source, track.start, end, output, cancel)
        result = prove(plan, written, runner, cancel)
    except Cancelled:
        _delete(written)
        return CutResult(cancelled=True, error="Stopped.")
    except FlacError as e:
        _delete(written)
        return CutResult(error=f"flac: {e}")
    if not result.ok:
        _delete(written)
    return result


def prove(plan: AlbumPlan, partials: list[Path], runner, cancel: CancelToken | None = None) -> CutResult:
    """Decode the written tracks: each must have its planned length, and together the source's MD5."""
    info = plan.info
    frame_bytes = info.channels * ((info.bits + 7) // 8)
    digest = hashlib.md5()
    lengths, problems = [], []
    for track, path in zip(plan.tracks, partials, strict=True):
        size = _hash(runner.decode(path, cancel), digest)
        lengths.append(size // frame_bytes)
        if size % frame_bytes or size // frame_bytes != track.length:
            problems.append(f"track {track.number} has {size // frame_bytes:,} samples, but the cut planned "
                            f"{track.length:,}")
    md5 = digest.hexdigest()
    expected = info.md5
    if not expected:  # a WAV or AIFF, or a FLAC written without an MD5: hash the source's own audio
        whole = hashlib.md5()
        _hash(runner.decode_range(plan.source, 0, None, cancel), whole)
        expected = whole.hexdigest()
    if md5 != expected:
        problems.append(f"the tracks' audio doesn't match the original's (MD5 {md5[:8]}… instead of "
                        f"{expected[:8]}…)")
    result = CutResult(ok=not problems, partials=list(partials), lengths=lengths, md5=md5, expected_md5=expected)
    if problems:
        result.error = "The proof failed: " + "; ".join(problems) + "."
    return result


def _hash(chunks: Iterable[bytes], digest) -> int:
    size = 0
    for chunk in chunks:
        digest.update(chunk)
        size += len(chunk)
    return size


def _delete(paths: Iterable[Path]) -> None:
    """This run's own .partial files (Ground rule 1's one permanent delete)."""
    for path in paths:
        if path.name.endswith(PARTIAL):
            path.unlink(missing_ok=True)


def _mb(size: int) -> str:
    return f"{size / 1024 / 1024:,.0f} MB"
