"""Cutting a planned album into tracks, and proving nothing was lost (SPEC §22). No Qt.

Each ticked track is written to "<its name>.partial" first. The proof then decodes every written track:
  1. its sample count must equal its planned length (a cut in the wrong place);
  2. the MD5 of its audio must equal the MD5 of the same range of the source, decoded by flac.
With every track ticked, the MD5 of all the tracks end to end must also equal the source's: its FLAC STREAMINFO
MD5, or, without one, the ranges' hashes end to end (every sample is in exactly one track).
On a failure or a Stop, this run's .partial files are deleted, and only those (Ground rule 1). Renaming them to
their final names and writing the tags come after, in the job.
"""

import hashlib
import math
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from refrain.core.cancel import CancelToken
from refrain.core.cue import AlbumPlan, PlannedTrack
from refrain.core.flac import Cancelled, FlacError
from refrain.core.playlists import PreviewSheet, PreviewTrack

PARTIAL = ".partial"


def partial_path(track: PlannedTrack) -> Path:
    return track.out_path.with_name(track.name + PARTIAL)


@dataclass
class CutResult:
    ok: bool = False
    cancelled: bool = False
    error: str | None = None  # flac's message, a failed proof, or why the album wasn't started
    partials: list[Path] = field(default_factory=list)  # the verified (ticked) tracks, in order, when ok
    lengths: list[int] = field(default_factory=list)  # samples per written track, as decoded
    track_md5s: list[str] = field(default_factory=list)  # per written track, equal to its range of the source
    md5: str | None = None  # of the tracks end to end, when every track was written
    expected_md5: str | None = None  # of the source


def is_silent(runner, cancel: CancelToken | None = None) -> Callable[[Path, int, int], bool]:
    """`plan_album`'s silence check: decode just that range and look for any non-zero byte."""
    def check(source: Path, start: int, end: int) -> bool:
        return all(not chunk.strip(b"\0") for chunk in runner.decode_range(source, start, end, cancel))
    return check


def cut_album(plan: AlbumPlan, runner, cancel: CancelToken | None = None,
              free_space: Callable[[Path], int] = lambda folder: shutil.disk_usage(folder).free,
              on_track: Callable[[int, int, PlannedTrack], None] | None = None) -> CutResult:
    """Write and prove this album's ticked tracks as .partial files. Nothing else in the folder is touched.
    `on_track(done, total, track)` is called before each track is written."""
    if plan.refusal is not None or plan.source is None or plan.info is None:
        return CutResult(error=plan.refusal or "The album wasn't planned.")
    if not plan.written:
        return CutResult(error="No track is ticked.")
    folder = plan.folder
    share = sum(t.length for t in plan.written) / max(plan.info.samples, 1)  # the ticked tracks' part
    needed = math.ceil(plan.source.stat().st_size * share)
    free = free_space(folder)
    if free < needed:
        return CutResult(error=f"Not enough free space: the tracks need about {megabytes(needed)}, and the drive has "
                               f"{megabytes(free)} free.")
    taken = [partial_path(t).name for t in plan.written if partial_path(t).exists()]
    if taken:
        return CutResult(error=f"{', '.join(taken)} already exist{'s' if len(taken) == 1 else ''}, maybe left by an "
                               "earlier split. Delete them in File Explorer, then split again.")

    written: list[Path] = []
    try:
        for done, track in enumerate(plan.written):
            if on_track is not None:
                on_track(done, len(plan.written), track)
            output = partial_path(track)
            written.append(output)  # recorded first: a flac killed mid-way may leave it behind
            end = None if track.end == plan.info.samples else track.end
            runner.encode(plan.source, track.start, end, output, cancel)
        result = prove(plan, written, runner, cancel)
    except Cancelled:
        delete_partials(written)
        return CutResult(cancelled=True, error="Stopped.")
    except FlacError as e:
        delete_partials(written)
        return CutResult(error=f"flac: {e}")
    if not result.ok:
        delete_partials(written)
    return result


def prove(plan: AlbumPlan, partials: list[Path], runner, cancel: CancelToken | None = None) -> CutResult:
    """Decode the written tracks: each must have its planned length and its range's MD5; with every track
    written, together they must also have the source's MD5."""
    source, info = plan.image
    frame_bytes = info.channels * ((info.bits + 7) // 8)
    tracks_digest, source_digest = hashlib.md5(), hashlib.md5()
    lengths, track_md5s, problems = [], [], []
    for track, path in zip(plan.written, partials, strict=True):
        own, part = hashlib.md5(), hashlib.md5()
        size = hash_chunks(runner.decode(path, cancel), own, tracks_digest)
        end = None if track.end == info.samples else track.end
        hash_chunks(runner.decode_range(source, track.start, end, cancel), part, source_digest)
        lengths.append(size // frame_bytes)
        track_md5s.append(own.hexdigest())
        if size % frame_bytes or size // frame_bytes != track.length:
            problems.append(f"track {track.number} has {size // frame_bytes:,} samples, but the cut planned "
                            f"{track.length:,}")
        elif own.hexdigest() != part.hexdigest():
            problems.append(f"track {track.number}'s audio doesn't match its part of the original (MD5 "
                            f"{own.hexdigest()[:8]}… instead of {part.hexdigest()[:8]}…)")
    md5 = expected = None
    if plan.all_ticked:
        md5 = tracks_digest.hexdigest()
        # A WAV or AIFF, or a FLAC written without an MD5: the source's ranges end to end are its whole audio.
        expected = info.md5 or source_digest.hexdigest()
        if md5 != expected:
            problems.append(f"the tracks' audio doesn't match the original's (MD5 {md5[:8]}… instead of "
                            f"{expected[:8]}…)")
    result = CutResult(ok=not problems, partials=list(partials), lengths=lengths, track_md5s=track_md5s, md5=md5,
                       expected_md5=expected)
    if problems:
        result.error = "The proof failed: " + "; ".join(problems) + "."
    return result


def preview_sheet(plan: AlbumPlan) -> PreviewSheet:
    """Preview in player on the Split page (SPEC §22): every track the split would write, ticked or not, each
    starting where the split cuts it (a gap belongs to the track before), with its title and performer. A hidden
    track becomes track 1 ("Hidden track"), since a cue sheet can't number a track 0; the plan needs a source."""
    source, info = plan.image
    sheet, tags = plan.sheet, info.tags
    tracks = [PreviewTrack(t.start, str(t.fields.get("title", "")), str(t.fields.get("artist", "")))
              for t in plan.tracks]
    title = (sheet.title if sheet else "") or tags.get("album", "")
    performer = (sheet.performer if sheet else "") or tags.get("albumartist", "")
    return PreviewSheet(source, info.rate, info.samples, tracks, title, performer)


@dataclass
class Finished:
    """One album after its proof: the tracks under their real names. The image and its .cue are never touched."""

    tracks: list[Path] = field(default_factory=list)
    error: str | None = None  # a final name was taken meanwhile: nothing was renamed, the .partial files are gone
    warnings: list[str] = field(default_factory=list)


def finish_album(plan: AlbumPlan, cut: CutResult) -> Finished:
    """Give the proved tracks their names (checked free again) and write their tags and cover. A split is just a
    split (Hugo, 2026-10-17): nothing else is changed, and nothing is deleted. Nothing is ever overwritten: if a
    name was taken since the plan, no track is renamed."""
    taken = [t.name for t in plan.written if t.out_path.exists()]
    if taken:
        delete_partials(cut.partials)
        return Finished(error="Nothing is ever overwritten, and these names were taken meanwhile: "
                              + ", ".join(taken) + ".")
    finished = Finished()
    for track, partial in zip(plan.written, cut.partials, strict=True):
        partial.rename(track.out_path)
        finished.tracks.append(track.out_path)
    for track in plan.written:
        try:
            write_tags(track, plan.image[1].cover)
        except Exception as e:  # noqa: BLE001 - the audio is proved and in place; say which tags are missing
            finished.warnings.append(f"{track.name}: the tags couldn't be written ({type(e).__name__}: {e})")
    return finished


def write_tags(track: PlannedTrack, cover: bytes | None) -> None:
    """The sheet's fields, the ISRC (a Vorbis comment) and the source's front cover, and nothing else (SPEC §22).
    flac carries a FLAC image's tags and pictures into each cut; they described the whole file, so they go first."""
    from mutagen.flac import FLAC

    from refrain.core.tags import TaggedFile

    audio = FLAC(str(track.out_path))
    if audio.tags is not None:
        audio.tags.clear()  # pyright: ignore[reportAttributeAccessIssue] - a VCFLACDict, a list of pairs
    audio.clear_pictures()
    audio.save()
    tagged = TaggedFile(track.out_path)
    numbers = ("track", "track_total", "disc", "disc_total")
    tagged.set_fields({name: track.fields[name] if name in numbers else str(track.fields[name])
                       for name in track.fields})
    if cover:
        tagged.set_cover(cover)
    tagged.save()
    if track.isrc:
        audio = FLAC(str(track.out_path))
        audio["ISRC"] = track.isrc
        audio.save()


def hash_chunks(chunks: Iterable[bytes], *digests) -> int:
    """Feed decoded PCM to the digests; returns its size in bytes. Shared with the trim's proof."""
    size = 0
    for chunk in chunks:
        for digest in digests:
            digest.update(chunk)
        size += len(chunk)
    return size


def delete_partials(paths: Iterable[Path]) -> None:
    """This run's own .partial files (Ground rule 1's one permanent delete), the split's or the trim's."""
    for path in paths:
        if path.name.endswith(PARTIAL):
            path.unlink(missing_ok=True)


def megabytes(size: int) -> str:
    return f"{size / 1024 / 1024:,.0f} MB"
