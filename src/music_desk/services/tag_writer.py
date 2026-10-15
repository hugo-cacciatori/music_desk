"""TagWriter (SPEC §7, §16): every tag write of the app goes through here, one at a time, on its own thread.

A write holds a lock on its file while it runs, and is refused (with the owner's name) when a job holds a lock
covering the file. After a successful write, `on_written(path)` lets the index catch up with that one file.
"""

import logging
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from music_desk.core import metadata
from music_desk.core.path_locks import LockedError, PathLocks
from music_desk.services.tag_io import Loaded, decode_cover

log = logging.getLogger(__name__)

SAVE_OWNER = "a tag save"


class TagWriter(QObject):
    written = Signal(object)  # Loaded: the fresh snapshot, or an error
    locksChanged = Signal()  # a job took or released a lock: locked files turn read-only, or editable again

    def __init__(self, locks: PathLocks, on_written: Callable[[str], None] | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.locks = locks
        self.on_written = on_written or (lambda _path: None)
        self._pool = ThreadPoolExecutor(1, thread_name_prefix="tag-writer")
        self._token = 0
        self._closed = False
        self._listener = self.locksChanged.emit  # called from any thread; queued to the UI
        locks.add_listener(self._listener)

    def locked_by(self, path: str | Path) -> str | None:
        """The job holding a lock on this file, if any (the writer's own short save locks don't count)."""
        owner = self.locks.owner(path)
        return None if owner in (None, SAVE_OWNER) else owner

    def write(self, path: str | Path, changes: dict) -> int:
        """Queue a write of the changed fields; the result comes as `written` with the returned token."""
        self._token += 1
        token = self._token
        if self._closed:
            self.written.emit(Loaded(token, None, None, "The app is closing."))
            return token
        self._pool.submit(self._write, token, str(path), dict(changes))
        return token

    def _write(self, token: int, path: str, changes: dict) -> None:
        name = os.path.basename(path)
        try:
            with self.locks.lock([path], SAVE_OWNER):
                snapshot = metadata.write_fields(path, changes)
        except LockedError as e:
            self.written.emit(Loaded(token, None, None,
                                     f"{name} is in use by {e.owner}. Try again when it has finished."))
            return
        except Exception as e:  # noqa: BLE001 - shown to the user; the fields stay dirty
            log.warning("saving tags of %s failed: %s", path, e)
            self.written.emit(Loaded(token, None, None, f"{type(e).__name__}: {e}"))
            return
        log.info("saved %s to %s", ", ".join(sorted(changes)) or "nothing", path)
        try:
            self.on_written(path)
        except Exception:  # noqa: BLE001 - the file is saved; the index catches up at the next scan
            log.exception("index update after saving %s failed", path)
        self.written.emit(Loaded(token, snapshot, decode_cover(snapshot.cover)))

    def close(self) -> None:
        """Finish the writes already queued, then stop."""
        if not self._closed:
            self._closed = True
            self.locks.remove_listener(self._listener)  # the locks outlive this window
            self._pool.shutdown(wait=True)
