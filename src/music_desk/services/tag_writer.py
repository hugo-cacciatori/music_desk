"""TagWriter (SPEC §7, §16): every tag write of the app goes through here, one at a time, on its own thread.

A write holds a lock on its file while it runs, and is refused (with the owner's name) when a job holds a lock
covering the file. After a successful write, `on_written(path)` lets the index catch up with that one file.
"""

import logging
import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from music_desk.core import lyrics, metadata
from music_desk.core.lyrics import EmbedResult
from music_desk.core.metadata import Snapshot
from music_desk.core.path_locks import LockedError, PathLocks
from music_desk.services.tag_io import Loaded, decode_cover

log = logging.getLogger(__name__)

SAVE_OWNER = "a tag save"


@dataclass(frozen=True)
class BatchWritten:
    """The result of write_many: the files saved (fresh snapshots) and the ones that weren't, with why."""

    token: int
    saved: list[Snapshot]
    failed: list[tuple[str, str]]  # (path, message)


@dataclass(frozen=True)
class LyricsEmbedded:
    """The result of embed_lyrics: per file, the core result, or None with the reason it failed."""

    token: int
    results: list[tuple[str, EmbedResult | None, str | None]]  # (path, result, error)

    @property
    def written(self) -> list[str]:
        return [path for path, result, _error in self.results if result is EmbedResult.WRITTEN]


class TagWriter(QObject):
    written = Signal(object)  # Loaded: the fresh snapshot, or an error
    batchWritten = Signal(object)  # BatchWritten
    lyricsEmbedded = Signal(object)  # LyricsEmbedded
    locksChanged = Signal()  # a job took or released a lock: locked files turn read-only, or editable again

    def __init__(self, locks: PathLocks, on_written: Callable[[str], None] | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.locks = locks
        self.on_written = on_written or (lambda _path: None)
        self._pool = ThreadPoolExecutor(1, thread_name_prefix="tag-writer")
        self._token = 0
        self._token_lock = threading.Lock()  # embed_lyrics is also called from the auto-save worker
        self._closed = False
        self._listener = self.locksChanged.emit  # called from any thread; queued to the UI
        locks.add_listener(self._listener)

    def locked_by(self, path: str | Path) -> str | None:
        """The job holding a lock on this file, if any (the writer's own short save locks don't count)."""
        owner = self.locks.owner(path)
        return None if owner in (None, SAVE_OWNER) else owner

    def _next_token(self) -> int:
        with self._token_lock:
            self._token += 1
            return self._token

    def write(self, path: str | Path, changes: dict) -> int:
        """Queue a write of the changed fields; the result comes as `written` with the returned token."""
        token = self._next_token()
        if self._closed:
            self.written.emit(Loaded(token, None, None, "The app is closing."))
            return token
        self._pool.submit(self._write, token, str(path), dict(changes))
        return token

    def write_many(self, items: list[tuple[str | Path, dict]]) -> int:
        """Queue writes to several files (multi-file editing); one `batchWritten` reports them all. A file that
        fails (locked, read-only…) doesn't stop the others."""
        token = self._next_token()
        if self._closed:
            self.batchWritten.emit(BatchWritten(token, [], [(str(p), "The app is closing.") for p, _c in items]))
            return token
        self._pool.submit(self._write_many, token, [(str(p), dict(c)) for p, c in items])
        return token

    def embed_lyrics(self, items: list[tuple[str | Path, dict]], *, only_if_empty: bool) -> int:
        """Queue writes of LRCLIB entries into the files' Lyrics tags (core.lyrics.embed_lyrics: synced lyrics
        first, nothing written when the tag already holds them). Any thread; one `lyricsEmbedded` reports them."""
        token = self._next_token()
        if self._closed:
            self.lyricsEmbedded.emit(LyricsEmbedded(token, [(str(p), None, "The app is closing.") for p, _e in items]))
            return token
        try:
            self._pool.submit(self._embed_many, token, [(str(p), dict(e)) for p, e in items], only_if_empty)
        except RuntimeError:  # closed meanwhile (the auto-save worker)
            self.lyricsEmbedded.emit(LyricsEmbedded(token, [(str(p), None, "The app is closing.") for p, _e in items]))
        return token

    def _embed_many(self, token: int, items: list[tuple[str, dict]], only_if_empty: bool) -> None:
        results = []
        for path, entry in items:
            name = os.path.basename(path)
            try:
                with self.locks.lock([path], SAVE_OWNER):
                    result = lyrics.embed_lyrics(path, entry, only_if_empty=only_if_empty)
            except LockedError as e:
                results.append((path, None, f"{name} is in use by {e.owner}. Try again when it has finished."))
                continue
            except Exception as e:  # noqa: BLE001 - reported; the other files go on
                log.warning("saving lyrics into %s failed: %s", path, e)
                results.append((path, None, f"{name}: {type(e).__name__}: {e}"))
                continue
            results.append((path, result, None))
            if result is EmbedResult.WRITTEN:
                log.info("saved LRCLIB lyrics to %s", path)
                try:
                    self.on_written(path)
                except Exception:  # noqa: BLE001 - the file is saved; the index catches up at the next scan
                    log.exception("index update after saving %s failed", path)
        self.lyricsEmbedded.emit(LyricsEmbedded(token, results))

    def _write(self, token: int, path: str, changes: dict) -> None:
        snapshot, error = self._write_one(path, changes)
        cover = decode_cover(snapshot.cover) if snapshot else None
        self.written.emit(Loaded(token, snapshot, cover, error))

    def _write_many(self, token: int, items: list[tuple[str, dict]]) -> None:
        saved, failed = [], []
        for path, changes in items:
            snapshot, error = self._write_one(path, changes)
            if snapshot is None:
                failed.append((path, error))
            else:
                saved.append(snapshot)
        self.batchWritten.emit(BatchWritten(token, saved, failed))

    def _write_one(self, path: str, changes: dict) -> tuple[Snapshot | None, str | None]:
        name = os.path.basename(path)
        try:
            with self.locks.lock([path], SAVE_OWNER):
                snapshot = metadata.write_fields(path, changes)
        except LockedError as e:
            return None, f"{name} is in use by {e.owner}. Try again when it has finished."
        except Exception as e:  # noqa: BLE001 - shown to the user; the fields stay dirty
            log.warning("saving tags of %s failed: %s", path, e)
            return None, f"{name}: {type(e).__name__}: {e}"
        log.info("saved %s to %s", ", ".join(sorted(changes)) or "nothing", path)
        try:
            self.on_written(path)
        except Exception:  # noqa: BLE001 - the file is saved; the index catches up at the next scan
            log.exception("index update after saving %s failed", path)
        return snapshot, None

    def close(self) -> None:
        """Finish the writes already queued, then stop."""
        if not self._closed:
            self._closed = True
            self.locks.remove_listener(self._listener)  # the locks outlive this window
            self._pool.shutdown(wait=True)
