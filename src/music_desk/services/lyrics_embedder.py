"""Auto-save of LRCLIB lyrics into the files' tags (SPEC §9: "Automatically save found lyrics into tags", the
script's --embed). Off by default.

When it is on, every track whose key is found on LRCLIB and whose Lyrics tag is empty gets the lyrics: tracks
already found when it is turned on (or at start), batch by batch, and each new result as it arrives. It only ever
fills an empty tag (core.lyrics.embed_lyrics with only_if_empty, which checks the file itself), and every write goes
through the TagWriter, so a job's lock refuses it. A file is tried once per session; one that failed (locked,
unreadable) is tried again at the next start.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from music_desk.core.index.lyrics_index import embed_candidates
from music_desk.core.index.store import connect_read, path_of
from music_desk.core.lyrics import FOUND
from music_desk.services.lyrics_service import LyricsService
from music_desk.services.tag_writer import LyricsEmbedded, TagWriter

log = logging.getLogger(__name__)

BATCH = 50  # files per TagWriter batch


class AutoEmbedder(QObject):
    embedded = Signal(int)  # files whose Lyrics tag a batch filled
    _queued = Signal(object, object, bool, bool)  # token, entry ids, backfill, more to come (worker → UI thread)

    def __init__(self, db_path: str | Path, lyrics: LyricsService, writer: TagWriter, parent: QObject | None = None,
                 *, enabled: bool = False, batch: int = BATCH) -> None:
        super().__init__(parent)
        self.db_path = Path(db_path)
        self._writer = writer
        self._batch = batch
        self._enabled = False
        self._closed = False
        self._attempted: set[int] = set()  # entry ids tried this session (UI thread only)
        self._pending: dict[int, tuple[bool, bool]] = {}  # token -> (backfill, more to come)
        self._backfilling = False
        self._pool = ThreadPoolExecutor(1, thread_name_prefix="lyrics-embed")
        self._queued.connect(self._on_queued)
        lyrics.entryReady.connect(self._entry_ready)
        writer.lyricsEmbedded.connect(self._written)
        self.set_enabled(enabled)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if enabled:
            self._backfill()

    def close(self) -> None:
        self._closed = True
        self._pool.shutdown(wait=True)

    # -- requests (UI thread) --

    def _entry_ready(self, key: str, entry: dict) -> None:
        if self._enabled and entry.get("status") == FOUND:
            self._submit(key, backfill=False)

    def _backfill(self) -> None:
        if not self._backfilling:
            self._backfilling = True
            self._submit(None, backfill=True)

    def _submit(self, key: str | None, *, backfill: bool) -> None:
        if self._closed:
            return
        skip = frozenset(self._attempted)
        try:
            self._pool.submit(self._find, key, skip, backfill)
        except RuntimeError:  # shut down
            pass

    # -- the worker: finds the files and hands them to the TagWriter --

    def _find(self, key: str | None, skip: frozenset[int], backfill: bool) -> None:
        if not self._enabled or self._closed:  # turned off (or closing) since this job was queued
            self._queued.emit(None, [], backfill, False)
            return
        try:
            conn = connect_read(self.db_path)
        except Exception:  # noqa: BLE001 - tried again at the next result or start
            log.exception("auto-save of lyrics could not open the index")
            self._queued.emit(None, [], backfill, False)
            return
        try:
            candidates = embed_candidates(conn, key=key, skip=skip, limit=self._batch if backfill else None)
            items, ids = [], []
            for entry_id, entry in candidates:
                try:
                    items.append((str(path_of(conn, entry_id)), entry))
                except KeyError:
                    continue
                ids.append(entry_id)
        except Exception:  # noqa: BLE001
            log.exception("auto-save of lyrics could not read the index")
            candidates, items, ids = [], [], []
        finally:
            conn.close()
        more = backfill and len(candidates) >= self._batch
        if not self._enabled:  # turned off while the index was read: write nothing
            items, ids, more = [], [], False
        token = self._writer.embed_lyrics(items, only_if_empty=True) if items else None
        self._queued.emit(token, ids, backfill, more)

    def _on_queued(self, token: int | None, ids: list[int], backfill: bool, more: bool) -> None:
        self._attempted.update(ids)
        if token is not None:
            self._pending[token] = (backfill, more)
        elif backfill:
            self._backfilling = False  # nothing left (or the index couldn't be read)

    def _written(self, result: LyricsEmbedded) -> None:
        pending = self._pending.pop(result.token, None)
        if pending is None:
            return
        backfill, more = pending
        written = result.written
        if written:
            log.info("auto-saved LRCLIB lyrics into %d files", len(written))
            self.embedded.emit(len(written))
        for path, _outcome, error in result.results:
            if error:
                log.info("auto-save of lyrics skipped %s: %s", path, error)
        if backfill:
            if more and self._enabled:
                self._submit(None, backfill=True)  # the next batch, once this one is written
            else:
                self._backfilling = False
