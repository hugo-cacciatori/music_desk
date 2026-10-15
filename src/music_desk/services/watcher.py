"""Live updates (SPEC §4): watchdog watches the online roots; changes are debounced and coalesced per folder,
then the indexer reconciles those folders.

A changed file brings its folder up to date (one level). A created or moved-in folder also gets its subtree
scanned. Events are flushed after `debounce` seconds without new ones, or at the latest after `max_delay`, so a
long copy still shows up while it runs.

Where watching fails (some network shares and removable drives), the root is reported through `unwatched` and
relies on revalidation on open: the browser asks the indexer to reconcile each folder it opens.
"""

import logging
import os
import threading
import time
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

log = logging.getLogger(__name__)

DEBOUNCE = 0.5  # seconds
MAX_DELAY = 5.0


class _Handler(FileSystemEventHandler):
    def __init__(self, on_change: Callable[[str, bool], None]) -> None:
        self.on_change = on_change

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type in ("opened", "closed", "closed_no_write"):
            return
        src = os.fsdecode(event.src_path)
        if event.event_type == "modified":
            # A modified folder: its own listing changed. A modified file: its folder's entry for it.
            self.on_change(src if event.is_directory else os.path.dirname(src), False)
            return
        self.on_change(os.path.dirname(src), False)
        if event.event_type == "moved":
            dest = os.fsdecode(event.dest_path)
            self.on_change(os.path.dirname(dest), False)
            if event.is_directory:
                self.on_change(dest, True)
        elif event.event_type == "created" and event.is_directory:
            self.on_change(src, True)


class Watcher(QObject):
    unwatched = Signal(int, str)  # root id, reason: this root falls back to revalidation on open

    def __init__(self, on_flush: Callable[[str, bool], None], parent: QObject | None = None, *,
                 debounce: float = DEBOUNCE, max_delay: float = MAX_DELAY,
                 observer_factory: Callable[[], Observer] = Observer) -> None:
        super().__init__(parent)
        self.on_flush = on_flush  # (folder path, recursive), called from the debounce thread
        self.debounce = debounce
        self.max_delay = max_delay
        self._observer = observer_factory()
        self._observer.start()
        self._watches: dict[int, tuple[str, object]] = {}  # root id -> (path, watch)
        self._pending: dict[str, tuple[str, bool]] = {}  # normcase path -> (path, recursive)
        self._first = self._last = 0.0
        self._condition = threading.Condition()
        self._stopping = False
        self._lock = threading.Lock()  # watch bookkeeping
        self._thread = threading.Thread(target=self._debounce_loop, name="Watcher", daemon=True)
        self._thread.start()

    # -- roots --

    def sync(self, roots: dict[int, str]) -> None:
        """Watch exactly these roots (id -> path): the online ones. Safe from any thread."""
        with self._lock:
            for root_id in [r for r in self._watches if r not in roots or self._watches[r][0] != roots[r]]:
                path, watch = self._watches.pop(root_id)
                try:
                    self._observer.unschedule(watch)
                except (KeyError, OSError) as e:
                    log.debug("unwatch %s: %s", path, e)
                log.info("stopped watching %s", path)
            for root_id, path in roots.items():
                if root_id in self._watches:
                    continue
                try:
                    watch = self._observer.schedule(_Handler(self._changed), path, recursive=True)
                except Exception as e:  # noqa: BLE001 - watching is best effort; open-time revalidation remains
                    log.warning("can't watch %s (%s): folders are revalidated when opened", path, e)
                    self.unwatched.emit(root_id, str(e))
                    continue
                self._watches[root_id] = (path, watch)
                log.info("watching %s", path)

    @property
    def watched(self) -> dict[int, str]:
        with self._lock:
            return {root_id: path for root_id, (path, _watch) in self._watches.items()}

    def stop(self) -> None:
        with self._condition:
            self._stopping = True
            self._condition.notify()
        self._observer.stop()
        self._observer.join(5)
        self._thread.join(5)

    # -- events --

    def _changed(self, path: str, recursive: bool) -> None:
        key = os.path.normcase(path)
        with self._condition:
            now = time.monotonic()
            if not self._pending:
                self._first = now
            self._last = now
            old = self._pending.get(key)
            self._pending[key] = (path, recursive or (old is not None and old[1]))
            self._condition.notify()

    def _debounce_loop(self) -> None:
        while True:
            with self._condition:
                while not self._pending and not self._stopping:
                    self._condition.wait()
                if self._stopping:
                    return
                now = time.monotonic()
                due = min(self._last + self.debounce, self._first + self.max_delay)
                if now < due:
                    self._condition.wait(due - now)
                    continue
                batch, self._pending = list(self._pending.values()), {}
            for path, recursive in _coalesce(batch):
                try:
                    self.on_flush(path, recursive)
                except Exception:  # noqa: BLE001 - one bad flush must not stop live updates
                    log.exception("watcher flush failed for %s", path)


def _coalesce(batch: list[tuple[str, bool]]) -> list[tuple[str, bool]]:
    """Drop folders already covered by a recursive scan of one of their ancestors; shallow paths first."""
    recursive = [os.path.normcase(p) for p, rec in batch if rec]

    def covered(path: str) -> bool:
        key = os.path.normcase(path)
        return any(key != r and key.startswith(r.rstrip("\\/") + os.sep) for r in recursive)

    kept = [(p, rec) for p, rec in batch if not covered(p)]
    return sorted(kept, key=lambda item: (item[0].count(os.sep), os.path.normcase(item[0])))
