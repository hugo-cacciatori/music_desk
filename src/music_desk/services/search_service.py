"""Library search off the UI thread (SPEC §6): one worker with its own read connection. Only the latest query
matters: a new one interrupts the one running (sqlite3's interrupt), and a stale result is never emitted.
"""

import logging
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from music_desk.core.index.search import (
    FILES,
    LIMIT,
    SearchQuery,
    SearchRow,
    parse_query,
    result_rows,
    text_search,
)
from music_desk.core.index.store import connect_read

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchResults:
    token: int
    query: SearchQuery
    rows: list[SearchRow]  # the first `limit` matches, in natural folder and name order
    total: int
    error: str | None = None
    keys: list[tuple] | None = None  # the rows' sort keys, from the `prepare` function given to the service


class SearchService(QObject):
    results = Signal(object)  # SearchResults, for the latest request only

    def __init__(self, db_path: str | Path, parent: QObject | None = None, *,
                 prepare: Callable[[list[SearchRow]], list[tuple]] | None = None) -> None:
        """`prepare` computes what the view needs per row (its sort keys) on the worker thread."""
        super().__init__(parent)
        self._db_path = db_path
        self._prepare = prepare
        self._pool = ThreadPoolExecutor(1, thread_name_prefix="search")
        self._lock = threading.Lock()
        self._token = 0
        self._conn: sqlite3.Connection | None = None  # the worker's own, opened on first use
        self._closed = False

    @property
    def token(self) -> int:
        return self._token

    def search(self, text: str, *, mode: str = FILES, error_keys: set[str] | frozenset[str] = frozenset(),
               limit: int = LIMIT) -> int:
        """`mode`: File or Lyrics search (SPEC §6)."""
        with self._lock:
            self._token += 1
            token = self._token
            if self._conn is not None:
                self._conn.interrupt()  # the running query is stale now
        if not self._closed:
            self._pool.submit(self._run, token, text, mode, frozenset(error_keys), limit)
        return token

    def cancel(self) -> None:
        """Forget the running query (the search was closed)."""
        self.search("")

    def _run(self, token: int, text: str, mode: str, error_keys: frozenset[str], limit: int) -> None:
        if token != self._token or self._closed:
            return
        query = parse_query(text)
        try:
            if self._conn is None:
                self._conn = connect_read(self._db_path)
            if query.empty:
                found, rows = None, []
            else:
                found = text_search(self._conn, query, error_keys=error_keys, mode=mode, limit=limit)
                rows = result_rows(self._conn, found.ids, query, cancelled=lambda: token != self._token, mode=mode)
            if token != self._token:
                return
            keys = self._prepare(rows) if self._prepare else None
            result = SearchResults(token, query, rows, found.total if found else 0, keys=keys)
        except sqlite3.OperationalError as e:
            if token != self._token:
                return  # interrupted by a newer query
            log.warning("Search failed for %r: %s", text, e)
            result = SearchResults(token, query, [], 0, f"The search couldn't run: {e}")
        except Exception as e:  # noqa: BLE001 - the search box must never take the app down
            log.exception("Search failed for %r", text)
            result = SearchResults(token, query, [], 0, f"The search couldn't run: {e}")
        if token == self._token and not self._closed:
            self.results.emit(result)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with self._lock:
            if self._conn is not None:
                self._conn.interrupt()
        self._pool.submit(self._close_conn)
        self._pool.shutdown(wait=True)

    def _close_conn(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
