"""The ReplayGain tool's background work (SPEC §10 B, §30): the recap (every file and what the run does to it)
and the job, which runs core.replaygain one album at a time with one rsgain process at below-normal priority.

The job holds a lock on its folder tree for the whole run (owner "the ReplayGain tool"), taken on the caller's
thread so a conflict is refused at once. Log lines and progress reach the UI as queued signals, at most 10 times
a second. Cancel stops the running rsgain and the albums after it; nothing is leveled then (core rules).
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from music_desk.core import replaygain, rsgain
from music_desk.core.cancel import CancelToken
from music_desk.core.path_locks import PathLocks
from music_desk.core.replaygain import DiscographyResult, Recap, ReplayGainOptions, RunLog

log = logging.getLogger(__name__)

OWNER = "the ReplayGain tool"
LINE_INTERVAL = 0.1  # seconds between batches of log lines


@dataclass
class Finished:
    token: int
    folder: Path
    options: ReplayGainOptions
    discography: bool
    album_logs: list[RunLog] = field(default_factory=list)  # each album's report, as written to its log
    result: DiscographyResult | None = None  # discography mode
    cancelled: bool = False
    error: str | None = None  # the job itself failed (not an album: those are in their logs)
    nothing: bool = False  # no audio file rsgain can scan

    @property
    def log_path(self) -> Path:
        """The log with this run's summary: the folder's replaygain.log (the artist folder for a discography)."""
        return self.folder / self.options.log_file


@dataclass
class Recapped:
    token: int
    recap: Recap | None = None
    error: str | None = None  # the folders couldn't be read


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
    recapped = Signal(object)  # Recapped

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

    def run(self, folder: str | Path, options: ReplayGainOptions, discography: bool) -> int:
        """Start the job under a lock on the folder tree. Raises LockedError at once if a job holds it."""
        folder = Path(folder)
        lock = self.locks.acquire([folder], OWNER)
        self._token += 1
        token, cancel = self._token, CancelToken()
        self._cancel = cancel
        self.running = True

        def job() -> None:
            reporter = _SignalReporter(self)
            finished = Finished(token, folder, options, discography)
            try:
                if discography:
                    result = replaygain.process_discography(
                        folder, options, scanner=self.scanner, exe=self.exe, cancel=cancel, reporter=reporter,
                        on_album=finished.album_logs.append)
                    finished.result = result
                    finished.nothing = result is None
                    finished.cancelled = bool(result and result.cancelled)
                else:
                    reporter.progress(0, 1, folder.name)
                    run_log = replaygain.process_album(folder, options, scanner=self.scanner, exe=self.exe,
                                                       cancel=cancel, reporter=reporter)
                    if run_log is None:
                        finished.nothing = True
                    else:
                        finished.album_logs.append(run_log)
                        finished.cancelled = run_log.scan == "CANCELLED"
                    reporter.progress(1, 1, "done")
            except Exception as e:  # noqa: BLE001 - shown in the report
                log.exception("ReplayGain job in %s failed", folder)
                finished.error = f"{type(e).__name__}: {e}"
            finally:
                reporter.flush()
                self.locks.release(lock)
                self.running = False
            log.info("ReplayGain tool in %s: %d albums%s", folder, len(finished.album_logs),
                     ", cancelled" if finished.cancelled else "")
            self.finished.emit(finished)

        if not self._submit(job):
            self.locks.release(lock)
            self.running = False
        return token

    def recap(self, folder: str | Path, options: ReplayGainOptions, discography: bool) -> int:
        """Read what a run on `folder` would do (SPEC §30), on the worker: the folders may be slow to list."""
        self._token += 1
        token, folder = self._token, Path(folder)

        def read() -> None:
            result = Recapped(token)
            try:
                result.recap = replaygain.recap(folder, options, discography)
            except Exception as e:  # noqa: BLE001 - shown in the status bar
                log.exception("ReplayGain recap of %s failed", folder)
                result.error = f"{type(e).__name__}: {e}"
            self.recapped.emit(result)

        self._submit(read)
        return token

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
