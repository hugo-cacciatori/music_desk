"""Access to the index: one writer thread that owns the only write connection, and read-only connections
(SPEC §16). Plain Python; the Qt service that reports the writer's progress comes with the indexer.
"""

import logging
import queue
import sqlite3
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import Future
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from music_desk.core.index import schema
from music_desk.core.lyrics import FOUND, INSTRUMENTAL, NOT_FOUND

log = logging.getLogger(__name__)

_STOP = object()


class DbWriter:
    """Runs submitted jobs one at a time, each in its own transaction, on a dedicated thread.

    A failing job is rolled back and its exception goes to its future; the writer keeps going. stop() finishes
    the jobs already queued before closing, so nothing submitted is lost at shutdown.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._accepting = False

    # -- lifecycle --

    def start(self) -> "DbWriter":
        """Open the database (created if missing), switch it to WAL and migrate it. Errors are raised here."""
        ready: Future = Future()
        self._thread = threading.Thread(target=self._run, args=(ready,), name="DbWriter", daemon=True)
        self._thread.start()
        ready.result()
        with self._lock:
            self._accepting = True
        return self

    def stop(self, timeout: float | None = None) -> None:
        with self._lock:
            if not self._accepting:
                return
            self._accepting = False
            self._queue.put(_STOP)
        self._thread.join(timeout)

    def __enter__(self) -> "DbWriter":
        return self if self._accepting else self.start()

    def __exit__(self, *_exc) -> None:
        self.stop()

    @property
    def running(self) -> bool:
        return self._accepting

    # -- jobs --

    def submit(self, job: Callable[[sqlite3.Connection], Any]) -> Future:
        """Queue job(conn); the future gets its return value, or its exception after a rollback."""
        future: Future = Future()
        with self._lock:
            if not self._accepting:
                raise RuntimeError("the index writer is not running")
            self._queue.put((job, future))
        return future

    def execute(self, sql: str, params: Iterable = ()) -> Future:
        """The future gets the cursor's lastrowid."""
        return self.submit(lambda conn: conn.execute(sql, tuple(params)).lastrowid)

    def executemany(self, sql: str, rows: Iterable[Iterable]) -> Future:
        """The future gets the number of rows changed."""
        rows = [tuple(row) for row in rows]
        return self.submit(lambda conn: conn.executemany(sql, rows).rowcount)

    # -- the thread --

    def _run(self, ready: Future) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, autocommit=True)
            schema.configure(conn, write=True)
            schema.migrate(conn)
        except BaseException as e:  # noqa: BLE001 - handed to start()'s caller
            ready.set_exception(e)
            return
        ready.set_result(None)
        try:
            while True:
                item = self._queue.get()
                if item is _STOP:
                    break
                job, future = item
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    result = job(conn)
                    conn.execute("COMMIT")
                except BaseException as e:  # noqa: BLE001 - one failing job must not stop the writer
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    log.debug("index write failed: %s", e)
                    future.set_exception(e)
                else:
                    future.set_result(result)
        finally:
            conn.close()


def connect_read(path: str | Path) -> sqlite3.Connection:
    """A read-only connection for one thread (the UI's, or a worker's). Refuses an index at another version."""
    uri = Path(path).absolute().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, autocommit=True)
    try:
        schema.configure(conn, write=False)
        conn.execute("PRAGMA query_only = ON")
        found = schema.version(conn)
        if found != schema.SCHEMA_VERSION:
            raise schema.SchemaError(f"the index is at schema version {found}, expected {schema.SCHEMA_VERSION}")
    except BaseException:
        conn.close()
        raise
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def consistent_read(conn: sqlite3.Connection):
    """Several queries that must see one state of the index (WAL gives each statement the newest commit, so a
    removal between two of them would otherwise show half of it)."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN")
    try:
        yield conn
    finally:
        conn.execute("COMMIT")


def path_of(conn: sqlite3.Connection, entry_id: int) -> Path:
    """An entry's full path, rebuilt from its parents (the top entry's name is the root path)."""
    rows = conn.execute(
        """
        WITH RECURSIVE up(id, parent_id, name, depth) AS (
            SELECT id, parent_id, name, 0 FROM entries WHERE id = ?
            UNION ALL
            SELECT e.id, e.parent_id, e.name, up.depth + 1 FROM entries e JOIN up ON e.id = up.parent_id
        )
        SELECT name FROM up ORDER BY depth DESC
        """,
        (entry_id,),
    ).fetchall()
    if not rows:
        raise KeyError(entry_id)
    return Path(*(row[0] for row in rows))


# The keys of each status's cache entry, as seek_lyrics.fetch_lyrics builds them.
LYRICS_KEYS = {
    FOUND: ("status", "source", "lrclib_id", "plain", "synced", "checked"),
    INSTRUMENTAL: ("status", "lrclib_id", "checked"),
    NOT_FOUND: ("status", "checked"),
}
LYRICS_COLUMNS = ("status", "source", "lrclib_id", "plain", "synced", "checked", "error")


class LyricsStore:
    """core.lyrics.LyricsCache over the `lyrics` table. Reads use the given read connection, so a store belongs
    to the thread that opened that connection; writes go through the writer."""

    def __init__(self, read_conn: sqlite3.Connection, writer: DbWriter) -> None:
        self._conn = read_conn
        self._writer = writer

    def get(self, key: str) -> dict | None:
        return read_lyrics(self._conn, key)

    def put(self, key: str, entry: dict) -> Future:
        return self._writer.submit(lambda conn: upsert_lyrics(conn, key, entry))


def read_lyrics(conn: sqlite3.Connection, key: str) -> dict | None:
    """One cache entry, in the script's shape for its status."""
    row = conn.execute(f"SELECT {', '.join(LYRICS_COLUMNS)} FROM lyrics WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    values = dict(zip(LYRICS_COLUMNS, tuple(row), strict=True))
    keys = LYRICS_KEYS.get(values["status"])
    if keys is None:  # an unexpected status (e.g. an imported entry): keep what is set
        return {k: v for k, v in values.items() if v is not None}
    return {k: values[k] for k in keys}


def upsert_lyrics(conn: sqlite3.Connection, key: str, entry: dict) -> None:
    """Write one cache entry (inside a writer job); the keys an entry doesn't have are cleared."""
    updates = ", ".join(f"{c} = excluded.{c}" for c in LYRICS_COLUMNS)
    conn.execute(
        f"INSERT INTO lyrics (key, {', '.join(LYRICS_COLUMNS)}) VALUES (?, {', '.join('?' * len(LYRICS_COLUMNS))}) "
        f"ON CONFLICT(key) DO UPDATE SET {updates}",
        [key, *(entry.get(column) for column in LYRICS_COLUMNS)],
    )
