"""The indexer service: runs core.index.scanner on its own thread, one scan at a time (SPEC §4, §16).

Scans are queued (a root already waiting isn't queued twice). The folder on screen can jump the queue with
`request_folder`: it is served inside a running scan, or scanned on its own when the indexer is idle. Progress
reaches the UI as queued signals, at most 10 per second.
"""

import logging
import threading
import time
from collections import deque

from PySide6.QtCore import QObject, Signal

from music_desk.core.cancel import CancelToken
from music_desk.core.index.scanner import Scanner, ScanProgress
from music_desk.core.index.store import DbWriter

log = logging.getLogger(__name__)

PROGRESS_INTERVAL = 0.1  # seconds


class Indexer(QObject):
    progress = Signal(object)  # ScanProgress (a copy)
    folderUpdated = Signal(int)  # a folder's entries and tags are up to date
    scanFinished = Signal(object)  # ScanReport
    idle = Signal()  # the queue is empty
    failed = Signal(str)

    def __init__(self, writer: DbWriter, parent: QObject | None = None, *, workers: int = 4) -> None:
        super().__init__(parent)
        self.scanner = Scanner(writer, workers=workers, on_progress=self._on_progress,
                               on_folder=self.folderUpdated.emit)
        # ("root", id), ("folder", id), ("path", (path, recursive)), ("file", path)
        self._jobs: deque[tuple[str, object]] = deque()
        self._condition = threading.Condition()
        self._current: tuple[str, object] | None = None
        self._cancel = CancelToken()
        self._stopping = False
        self._last_progress = 0.0
        self._last_phase = ""
        self._thread = threading.Thread(target=self._run, name="Indexer", daemon=True)
        self._thread.start()

    # -- requests (any thread) --

    def scan_root(self, root_id: int) -> None:
        self._queue(("root", root_id))

    def scan_folder(self, entry_id: int) -> None:
        self._queue(("folder", entry_id))

    def scan_path(self, path: str, recursive: bool = False) -> None:
        """Bring a folder up to date by path, resolved when the job runs (the watcher's changes)."""
        self._queue(("path", (path, recursive)))

    def update_file(self, path: str) -> None:
        """Re-read one file the app just wrote (a tag save) into the index, without a folder rescan."""
        self._queue(("file", path))

    def request_folder(self, entry_id: int) -> None:
        """Bring a folder up to date first: inside the running root scan, or on its own when idle."""
        with self._condition:
            if self._current and self._current[0] == "root":
                self.scanner.request_folder(entry_id)
            else:
                self._queue_locked(("folder", entry_id), front=True)

    def cancel_root(self, root_id: int) -> None:
        """Drop a root's queued scans and stop its running one (before the root is removed)."""
        with self._condition:
            self._jobs = deque(job for job in self._jobs if job != ("root", root_id))
            if self._current == ("root", root_id):
                self._cancel.cancel()

    @property
    def busy(self) -> bool:
        with self._condition:
            return self._current is not None or bool(self._jobs)

    def stop(self, timeout: float | None = 10) -> None:
        with self._condition:
            self._stopping = True
            self._jobs.clear()
            self._cancel.cancel()
            self._condition.notify()
        self._thread.join(timeout)

    # -- the thread --

    def _queue(self, job: tuple[str, int]) -> None:
        with self._condition:
            self._queue_locked(job)

    def _queue_locked(self, job: tuple[str, int], front: bool = False) -> None:
        if self._stopping or (job == self._current and job[0] == "root"):
            return
        if job in self._jobs:
            if not front:
                return
            self._jobs.remove(job)
        if front:
            self._jobs.appendleft(job)
        else:
            self._jobs.append(job)
        self._condition.notify()

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._jobs and not self._stopping:
                    self._condition.wait()
                if self._stopping:
                    return
                self._current = job = self._jobs.popleft()
                self._cancel = cancel = CancelToken()
            try:
                kind, entity = job
                if kind == "root":
                    report = self.scanner.scan_root(entity, cancel)
                elif kind == "path":
                    path, recursive = entity
                    report = self.scanner.scan_path(path, cancel, recursive=recursive)
                elif kind == "file":
                    report = self.scanner.update_file(entity)
                else:
                    report = self.scanner.scan_folder(entity, cancel)
                if report is not None:  # None: the folder or path left the library meanwhile
                    log.info("scan %s %s: %s", kind, entity, report)
                    if kind != "root" and report.folder_id is not None:
                        self.folderUpdated.emit(report.folder_id)
                    self.scanFinished.emit(report)
            except Exception as e:  # noqa: BLE001 - the indexer keeps serving later jobs
                log.exception("scan %s failed", job)
                if not cancel.cancelled:  # a cancelled scan of a removed root may fail on its last writes
                    self.failed.emit(f"Indexing failed: {e}")
            finally:
                with self._condition:
                    self._current = None
                    empty = not self._jobs
            if empty:
                self.idle.emit()

    def _on_progress(self, progress: ScanProgress) -> None:
        now = time.monotonic()
        if progress.phase == self._last_phase and progress.phase != "done" and \
                now - self._last_progress < PROGRESS_INTERVAL:
            return
        self._last_progress, self._last_phase = now, progress.phase
        self.progress.emit(ScanProgress(**vars(progress)))

