"""The library service: owns the index writer and runs root changes and online checks off the UI thread
(Ground rule 2). Results come back to the UI as queued signals."""

import logging
import sqlite3
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from music_desk.core.index import roots
from music_desk.core.index.roots import AddResult, Root
from music_desk.core.index.store import DbWriter, connect_read

log = logging.getLogger(__name__)

ONLINE_CHECK_MS = 30_000  # unplugged drives are noticed, and reconnect, within this delay


class LibraryService(QObject):
    rootsChanged = Signal()
    addFinished = Signal(object)  # list[AddResult], one per path of an add_folders call
    failed = Signal(str)  # a plain message for the status bar

    def __init__(self, db_path: str | Path, parent: QObject | None = None, *, online_check_ms: int = ONLINE_CHECK_MS):
        super().__init__(parent)
        self.writer = DbWriter(db_path).start()
        self.conn: sqlite3.Connection = connect_read(db_path)  # the UI thread's read-only connection
        # One file-system worker: probing an unplugged network drive can hang for seconds.
        self._fs = ThreadPoolExecutor(1, thread_name_prefix="library-fs")
        self._timer = QTimer(self)
        self._timer.setInterval(online_check_ms)
        self._timer.timeout.connect(self.refresh_online)
        self._timer.start()
        self._closed = False
        self.refresh_online()  # roots unplugged since the last run show as offline at once

    def roots(self) -> list[Root]:
        return roots.list_roots(self.conn)

    # -- actions (they return at once; results arrive as signals) --

    def add_folders(self, paths: list[str | Path]) -> Future:
        return self._run(self._add_all, [str(p) for p in paths])

    def remove_root(self, root_id: int) -> Future:
        return self._run(self._remove, root_id)

    def refresh_online(self) -> Future:
        return self._run(self._check_online, self.roots())

    def close(self) -> None:
        """Stop the timer, finish pending work, flush the writer and close the connections."""
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        self._fs.shutdown(wait=True)
        self.writer.stop()
        self.conn.close()

    # -- worker side --

    def _run(self, fn, *args) -> Future:
        def guarded():
            try:
                fn(*args)
            except Exception as e:  # noqa: BLE001 - reported to the user, the app keeps running
                log.exception("library task failed")
                self.failed.emit(f"Library update failed: {e}")
        return self._fs.submit(guarded)

    def _add_all(self, paths: list[str]) -> None:
        results: list[AddResult] = []
        for path in paths:
            result = roots.add_root(self.writer, path)
            log.info("add root %s: %s", path, result.status.value)
            results.append(result)
        if any(r.status in (roots.AddStatus.ADDED, roots.AddStatus.MERGED) for r in results):
            self.rootsChanged.emit()
        self.addFinished.emit(results)

    def _remove(self, root_id: int) -> None:
        if roots.remove_root(self.writer, root_id):
            log.info("removed root %s from the library", root_id)
            self.rootsChanged.emit()

    def _check_online(self, current: list[Root]) -> None:
        changed = roots.check_online(self.writer, current)
        for root in changed:
            log.info("root %s is %s", root.path, "back online" if root.online else "offline")
        if changed:
            self.rootsChanged.emit()
