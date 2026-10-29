"""The ReplayGain tool's background work (SPEC §10 B, §30): the recap (every file and what the run does to it)
and the job, which runs core.replaygain one album at a time with one rsgain process at below-normal priority.

A job is one of five kinds: the tool's album and discography, and the right-click menu's (SPEC §31) several
folders each as an album, selected tracks as an album, and tracks each on its own.

The job holds a lock on its folder trees for the whole run (owner "ReplayGain"), taken on the caller's
thread so a conflict is refused at once. Log lines and progress reach the UI as queued signals, at most 10 times
a second. Cancel stops the running rsgain and the albums after it; nothing is leveled then (core rules).
"""

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from refrain.core import replaygain, rsgain
from refrain.core.cancel import CancelToken
from refrain.core.path_locks import PathLocks
from refrain.core.replaygain import DiscographyResult, LooseResult, ReplayGainOptions, RunLog

log = logging.getLogger(__name__)

OWNER = "ReplayGain"
ALBUM, DISCOGRAPHY = "album", "discography"  # one folder
ALBUMS = "albums"  # several folders, each as an album, in turn
TRACKS = "tracks"  # tracks of one folder, as an album: the folder's other files aren't measured nor tagged
LOOSE = "loose"  # tracks, each on its own, with no log (core.replaygain.process_loose_tracks)
LINE_INTERVAL = 0.1  # seconds between batches of log lines


@dataclass
class Finished:
    token: int
    folder: Path  # the folder shown (the tracks' folder; the first of several)
    options: ReplayGainOptions
    discography: bool
    kind: str = ALBUM
    paths: list[Path] = field(default_factory=list)  # what the run was asked for: folders, or tracks
    album_logs: list[RunLog] = field(default_factory=list)  # each album's report, as written to its log
    result: DiscographyResult | None = None  # discography mode
    loose: LooseResult | None = None  # tracks each on its own
    cancelled: bool = False
    error: str | None = None  # the job itself failed (not an album: those are in their logs)
    nothing: bool = False  # no audio file rsgain can scan

    @property
    def log_path(self) -> Path | None:
        """The log with this run's summary: the folder's replaygain.log (the artist folder for a discography).
        None when there is no single one: several folders each have theirs, loose tracks have none."""
        if self.kind == LOOSE or (self.kind == ALBUMS and len(self.album_logs) != 1):
            return None
        if self.kind == ALBUMS:
            return self.album_logs[0].folder / self.options.log_file
        return self.folder / self.options.log_file

    @property
    def folders(self) -> list[Path]:
        """The folders whose files may have changed (the index catches up on them)."""
        if self.kind in (TRACKS, LOOSE):
            return sorted({path.parent for path in self.paths})
        return sorted(set(self.paths)) or [self.folder]


class _SignalReporter:
    """core.replaygain.Reporter → the runner's signals, with log lines batched."""

    def __init__(self, runner: "ReplayGainRunner") -> None:
        self._runner = runner
        self._lines: list[str] = []
        self._last = 0.0
        self._lock = threading.Lock()  # rsgain's reader thread and the job thread both report

    def _line(self, text: str) -> None:
        with self._lock:
            self._lines.append(text)
            if time.monotonic() - self._last < LINE_INTERVAL:
                return
            lines, self._lines, self._last = self._lines, [], time.monotonic()
        self._runner.lines.emit(lines)

    def flush(self) -> None:
        with self._lock:
            lines, self._lines = self._lines, []
        if lines:
            self._runner.lines.emit(lines)

    def info(self, message: str) -> None:
        self._line(message)

    def warn(self, message: str) -> None:
        self._line(f"WARNING  {message}")

    def error(self, message: str) -> None:
        self._line(f"ERROR  {message}")

    def progress(self, done: int, total: int, label: str) -> None:
        self.flush()
        self._runner.progress.emit(done, total, label)


class ReplayGainRunner(QObject):
    lines = Signal(object)  # list[str]: log lines, batched
    progress = Signal(int, int, str)  # albums done, albums in all, the album now running
    finished = Signal(object)  # Finished

    def __init__(self, locks: PathLocks, parent: QObject | None = None, *, scanner=rsgain.scan,
                 exe: Path | None = None) -> None:
        super().__init__(parent)
        self.locks = locks
        self.scanner = scanner  # replaced in tests by a fake that tags as rsgain would
        self.exe = exe
        self._pool = ThreadPoolExecutor(1, thread_name_prefix="replaygain-tool")
        self._token = 0
        self._cancel = CancelToken()
        self._closed = False
        self.running = False

    def start(self, kind: str, paths: list[Path], options: ReplayGainOptions) -> int:
        """Start a job of `kind` on `paths` (folders; tracks for TRACKS and LOOSE), under a lock on their folder
        trees (a track's folder). Raises LockedError at once if a job holds one."""
        tracks = kind in (TRACKS, LOOSE)
        lock = self.locks.acquire(sorted({path.parent for path in paths}) if tracks else paths, OWNER)
        self._token += 1
        token, cancel = self._token, CancelToken()
        self._cancel = cancel
        self.running = True
        folder = paths[0].parent if tracks else paths[0]

        def job() -> None:
            reporter = _SignalReporter(self)
            finished = Finished(token, folder, options, kind == DISCOGRAPHY, kind, list(paths))
            try:
                self._job(finished, cancel, reporter)
            except Exception as e:  # noqa: BLE001 - shown in the report
                log.exception("ReplayGain job in %s failed", folder)
                finished.error = f"{type(e).__name__}: {e}"
            finally:
                reporter.flush()
                self.locks.release(lock)
                self.running = False
            log.info("ReplayGain (%s) in %s: %d albums%s", kind, folder, len(finished.album_logs),
                     ", cancelled" if finished.cancelled else "")
            self.finished.emit(finished)

        if not self._submit(job):
            self.locks.release(lock)
            self.running = False
        return token

    def _job(self, finished: Finished, cancel: CancelToken, reporter: "_SignalReporter") -> None:
        options, kind = finished.options, finished.kind
        if kind == DISCOGRAPHY:
            result = replaygain.process_discography(
                finished.folder, options, scanner=self.scanner, exe=self.exe, cancel=cancel, reporter=reporter,
                on_album=finished.album_logs.append)
            finished.result = result
            finished.nothing = result is None
            finished.cancelled = bool(result and result.cancelled)
            return
        if kind == LOOSE:
            loose = replaygain.process_loose_tracks(finished.paths, options, scanner=self.scanner, exe=self.exe,
                                                    cancel=cancel, reporter=reporter)
            finished.loose = loose
            finished.nothing = not loose.logs and not loose.not_run
            finished.cancelled = bool(loose.not_run)
            return
        # ALBUM and ALBUMS: each folder as an album, in turn; TRACKS: their folder, its other files excluded.
        albums: list[tuple[Path, list[str]]]
        if kind == TRACKS:
            chosen = {path.name.casefold() for path in finished.paths}
            albums = [(finished.folder, [name for name in os.listdir(finished.folder)
                                         if name.casefold() not in chosen])]
        else:
            albums = [(path, []) for path in finished.paths]
        for index, (folder, exclude) in enumerate(albums):
            if cancel.cancelled:
                finished.cancelled = True
                break
            reporter.progress(index, len(albums), folder.name)
            run_log = replaygain.process_album(folder, options, scanner=self.scanner, exe=self.exe, cancel=cancel,
                                               reporter=reporter, exclude=exclude)
            if run_log is not None:
                finished.album_logs.append(run_log)
                finished.cancelled = run_log.scan == "CANCELLED"
        reporter.progress(len(albums), len(albums), "done")
        finished.nothing = not finished.album_logs and not finished.cancelled

    def cancel(self) -> None:
        """Stop the job: the running rsgain is killed, and the albums after it aren't processed."""
        self._cancel.cancel()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._cancel.cancel()
            self._pool.shutdown(wait=True)

    def _submit(self, fn) -> bool:
        if self._closed:
            return False
        try:
            self._pool.submit(fn)
        except RuntimeError:
            return False
        return True
