"""Loose tracks (the right-click menu only, SPEC §31): each track alone is its own album."""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from refrain import paths
from refrain.core import rsgain
from refrain.core.cancel import CancelToken
from refrain.core.formats import REPLAYGAIN_EXTENSIONS
from refrain.core.replaygain.album import (
    Scanner,
    adjust_gain_to_prevent_clipping,
    scan_album,
    update_album_gain_metadata,
)
from refrain.core.replaygain.options import DEFAULT_OPTIONS, ReplayGainOptions
from refrain.core.replaygain.run_log import Clock, NullReporter, Reporter, RunLog
from refrain.core.text import plural

log = logging.getLogger(__name__)


@dataclass
class LooseResult:
    """A run on loose tracks: each one's RunLog, in order, and the paths left out with why."""

    options: ReplayGainOptions
    logs: list[RunLog] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)
    not_run: list[Path] = field(default_factory=list)  # after a cancel

    def result(self) -> str:
        results = {run_log.result() for run_log in self.logs}
        if self.not_run:
            return "CANCELLED"
        if not self.logs:
            return "NOTHING TO DO"
        return "OK" if results == {"OK"} else "DONE WITH ERRORS"

    def text(self) -> str:
        """The report the job's window shows: one block per track, then what was left out. Never written to a
        file: an album's replaygain.log keeps its last report."""
        lines = [f"ReplayGain, each track on its own (target {self.options.target_text} LUFS): "
                 f"{plural(len(self.logs), 'track')} processed", ""]
        text = "\n".join(lines) + "\n" + "".join(run_log.text() for run_log in self.logs)
        tail = [f"  {path}: {reason}" for path, reason in self.skipped]
        tail += [f"  {path}: not processed, cancelled" for path in self.not_run]
        if tail:
            text += "\nLeft out:\n" + "\n".join(tail) + "\n"
        return text + f"\nResult: {self.result()}\n"


def process_loose_tracks(tracks: Iterable[str | Path], options: ReplayGainOptions = DEFAULT_OPTIONS,
                         scanner: Scanner = rsgain.scan, exe: Path | None = None, cancel: CancelToken | None = None,
                         reporter: Reporter | None = None, now: Clock = datetime.now) -> LooseResult:
    """ReplayGain on tracks that belong to no album (SPEC §31): each one alone, in turn, is its own album. rsgain
    measures it at the target, the clipping fix lowers its track gain if needed, its album gain and peak become its
    own and the LUFS comment is written. Nothing is copied into subfolders and no log file is written.

    Files rsgain can't scan (by extension) and missing ones are skipped. A cancel stops after the current track
    (rsgain on one file is short), as the discography stops between albums.
    """
    exe = exe or paths.rsgain_path()
    reporter = reporter or NullReporter()
    result = LooseResult(options)
    todo: list[Path] = []
    for track in map(Path, tracks):
        if track.suffix.lower() not in REPLAYGAIN_EXTENSIONS:
            result.skipped.append((track, "skipped, format not supported by rsgain"))
        elif not track.is_file():
            result.skipped.append((track, "skipped, not found"))
        else:
            todo.append(track)
    for index, track in enumerate(todo):
        if cancel is not None and cancel.cancelled:
            result.not_run = todo[index:]
            break
        reporter.progress(index, len(todo), track.name)
        result.logs.append(_process_loose_track(track, options, scanner, exe, reporter, now))
    reporter.progress(len(todo), len(todo), "done")
    return result


def _process_loose_track(track: Path, options: ReplayGainOptions, scanner: Scanner, exe: Path,
                         reporter: Reporter, now: Clock) -> RunLog:
    folder, files = track.parent, [track.name]
    run_log = RunLog(folder, files, options, exe, reporter, now, loose=True)
    try:
        if scan_album(folder, files, run_log, scanner, exe, None):
            adjust_gain_to_prevent_clipping(folder, files, run_log)
            update_album_gain_metadata(folder, files, run_log)
    except Exception:  # noqa: BLE001 - recorded as CRASHED in the track's report
        log.exception("ReplayGain crashed on %s", track)
        run_log.crash()
    reporter.info(f"{track.name}: {run_log.result()}")
    return run_log
