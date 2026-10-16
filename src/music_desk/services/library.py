"""The library service: owns the index writer, the indexer, the watcher and the lyrics service, and runs root
changes and online checks off the UI thread (Ground rule 2). Results come back to the UI as queued signals."""

import logging
import sqlite3
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from music_desk.core.index import lyrics_import, roots
from music_desk.core.index.roots import AddResult, Root
from music_desk.core.index.store import DbWriter, connect_read
from music_desk.core.lyrics import LrclibClient
from music_desk.core.path_locks import PathLocks
from music_desk.services.indexer import Indexer
from music_desk.services.lyrics_service import LyricsService
from music_desk.services.watcher import Watcher

log = logging.getLogger(__name__)

ONLINE_CHECK_MS = 30_000  # unplugged drives are noticed, and reconnect, within this delay


class LibraryService(QObject):
    rootsChanged = Signal()
    addFinished = Signal(object)  # list[AddResult], one per path of an add_folders call
    failed = Signal(str)  # a plain message for the status bar
    notice = Signal(str)  # an informational message for the status bar

    lyrics_offline_default = False  # the UI tests set it, so a library they build never reaches LRCLIB

    def __init__(self, db_path: str | Path, parent: QObject | None = None, *, online_check_ms: int = ONLINE_CHECK_MS,
                 watcher_options: dict | None = None, lyrics_client: LrclibClient | None = None,
                 lyrics_offline: bool | None = None, lyrics_options: dict | None = None):
        super().__init__(parent)
        self.db_path = Path(db_path)
        self.writer = DbWriter(db_path).start()
        self.conn: sqlite3.Connection = connect_read(db_path)  # the UI thread's read-only connection
        self.indexer = Indexer(self.writer, self)
        self.locks = PathLocks()  # shared by jobs and the tag writer (SPEC §16)
        self.watcher = Watcher(self.indexer.scan_path, self, **(watcher_options or {}))
        offline = self.lyrics_offline_default if lyrics_offline is None else lyrics_offline
        self.lyrics = LyricsService(db_path, self.writer, self, client=lyrics_client, offline=offline,
                                    **(lyrics_options or {}))
        self.indexer.idle.connect(self.lyrics.start)  # the first time the tags are indexed, then new tracks
        self.indexer.scanFinished.connect(lambda _report: self.lyrics.wake())
        self.watcher.unwatched.connect(
            lambda _root_id, reason: self.failed.emit(
                f"Live updates are off for a library folder ({reason}). Its folders are refreshed when opened."))
        # One file-system worker: probing an unplugged network drive can hang for seconds.
        self._fs = ThreadPoolExecutor(1, thread_name_prefix="library-fs")
        self._timer = QTimer(self)
        self._timer.setInterval(online_check_ms)
        self._timer.timeout.connect(self.refresh_online)
        self._timer.start()
        self._closed = False
        self.refresh_online()  # roots unplugged since the last run show as offline at once
        self._run(self._sync_watches)  # after the online check: same worker, so in order
        self.rescan_all()  # the startup rescan

    def rescan_all(self) -> int:
        """Queue an incremental rescan of every root (startup, File › Rescan library); an offline root is skipped
        by the scanner. Returns the number of roots."""
        current = self.roots()
        for root in current:
            self.indexer.scan_root(root.id)
        return len(current)

    @property
    def closed(self) -> bool:
        return self._closed

    def roots(self) -> list[Root]:
        if self._closed:  # a signal queued before close() may still be delivered
            return []
        return roots.list_roots(self.conn)

    # -- actions (they return at once; results arrive as signals) --

    def add_folders(self, paths: list[str | Path]) -> Future:
        return self._run(self._add_all, [str(p) for p in paths])

    def remove_root(self, root_id: int) -> Future:
        self.indexer.cancel_root(root_id)
        return self._run(self._remove, root_id)

    def refresh_online(self) -> Future:
        return self._run(self._check_online, self.roots())

    def folder_opened(self, entry_id: int) -> None:
        """Revalidate a folder the browser shows (stale-while-revalidate, and the fallback where watching fails):
        its indexed content is shown at once, then reconciled ahead of other indexing."""
        self.indexer.request_folder(entry_id)

    def close(self) -> None:
        """Stop the timer, finish pending work, flush the writer and close the connections."""
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        self._fs.shutdown(wait=True)
        self.watcher.stop()
        self.lyrics.stop()
        self.indexer.stop()
        self.conn.close()
        self.writer.stop()  # last: only a writing connection can checkpoint and remove the WAL files

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
        added = [r for r in results if r.status in (roots.AddStatus.ADDED, roots.AddStatus.MERGED)]
        for result in added:
            self._import_lyrics_cache(result.path)  # before the scan, whose folder totals then count it
            self.indexer.scan_root(result.root_id)
        if added:
            self._sync_watches()
            self.rootsChanged.emit()
        self.addFinished.emit(results)

    def _import_lyrics_cache(self, folder: str) -> None:
        """seek_lyrics' lyrics_cache.json at the top of a new root (SPEC §9): read, never changed."""
        report = lyrics_import.import_cache(self.writer, folder)
        if report is None:
            return
        if report.imported:
            self.lyrics.wake()
        (self.failed if report.error else self.notice).emit(report.message(folder))

    def _remove(self, root_id: int) -> None:
        if roots.remove_root(self.writer, root_id):
            log.info("removed root %s from the library", root_id)
            self._sync_watches()
            self.rootsChanged.emit()

    def _check_online(self, current: list[Root]) -> None:
        changed = roots.check_online(self.writer, current)
        for root in changed:
            log.info("root %s is %s", root.path, "back online" if root.online else "offline")
            if root.online:
                self.indexer.scan_root(root.id)  # catch up with what changed while it was away
        if changed:
            self._sync_watches()
            self.rootsChanged.emit()

    def _sync_watches(self) -> None:
        """Watch the online roots (worker thread: it reads the roots on its own connection)."""
        conn = connect_read(self.db_path)
        try:
            current = roots.list_roots(conn)
        finally:
            conn.close()
        self.watcher.sync({root.id: root.path for root in current if root.online})
