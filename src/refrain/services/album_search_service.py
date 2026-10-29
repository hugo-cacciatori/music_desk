"""The Advanced search's worker (SPEC §26): runs one album search at a time on its own thread and read connection,
so the window and the rest of the app never wait (Ground rule 2). A newer search replaces an older one's result."""

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from refrain.core.index import album_search
from refrain.core.index.album_search import AlbumResult
from refrain.core.index.store import connect_read

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchDone:
    token: int
    search: str
    results: list[AlbumResult] = field(default_factory=list)
    error: str | None = None


class AlbumSearchService(QObject):
    done = Signal(object)  # SearchDone, for the latest request only

    def __init__(self, db_path: str | Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._db_path = db_path
        self._pool = ThreadPoolExecutor(1, thread_name_prefix="album-search")
        self._token = 0
        self._closed = False

    def run(self, search: str) -> int:
        self._token += 1
        token = self._token

        def job() -> None:
            if token != self._token:
                return  # a newer search came meanwhile
            try:
                conn = connect_read(self._db_path)
                try:
                    results = album_search.run(conn, search, cancelled=lambda: token != self._token)
                finally:
                    conn.close()
                done = SearchDone(token, search, results)
            except Exception as e:  # noqa: BLE001 - shown in the window
                log.exception("album search %s failed", search)
                done = SearchDone(token, search, error=f"{type(e).__name__}: {e}")
            if token == self._token:
                self.done.emit(done)

        if not self._closed:
            self._pool.submit(job)
        return token

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._token += 1  # whatever runs stops between folders and emits nothing
            self._pool.shutdown(wait=True)
