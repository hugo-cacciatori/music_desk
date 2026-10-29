"""core/index/store.py: the writer thread, read-only connections and the lyrics cache store (SPEC §2, §16)."""

import sqlite3
import threading
from datetime import date

import pytest
from lrclib_fake import FakeUrlopen, SleepRecorder, no_jitter
from lyrics_helpers import DictCache

from refrain.core.index import schema
from refrain.core.index.store import DbWriter, LyricsStore, connect_read
from refrain.core.lyrics import LrclibClient, Track, cache_key, lookup


@pytest.fixture
def db(tmp_path):
    return tmp_path / "index" / "library.db"


@pytest.fixture
def writer(db):
    with DbWriter(db) as writer:
        yield writer


def test_start_creates_and_migrates(writer, db):
    assert db.exists() and writer.running
    conn = connect_read(db)
    assert schema.version(conn) == schema.SCHEMA_VERSION
    conn.close()


def test_start_error_is_raised(tmp_path):
    path = tmp_path / "library.db"
    conn = sqlite3.connect(path, autocommit=True)
    conn.execute(f"PRAGMA user_version = {schema.SCHEMA_VERSION + 1}")
    conn.close()
    writer = DbWriter(path)
    with pytest.raises(schema.SchemaError):
        writer.start()
    assert not writer.running


def test_jobs_run_in_order_on_the_writer_thread(writer):
    threads = set()

    def job(n):
        def run(conn):
            threads.add(threading.current_thread().name)
            conn.execute("INSERT INTO lyrics (key, status) VALUES (?, 'x')", (f"k{n:03d}",))
            return conn.execute("SELECT count(*) FROM lyrics").fetchone()[0]
        return run

    futures = [writer.submit(job(n)) for n in range(50)]
    assert [f.result() for f in futures] == list(range(1, 51))
    assert threads == {"DbWriter"}


def test_failing_job_rolls_back_only_itself(writer, db):
    def bad(conn):
        conn.execute("INSERT INTO lyrics (key, status) VALUES ('half', 'x')")
        raise ValueError("boom")

    first = writer.execute("INSERT INTO lyrics (key, status) VALUES ('before', 'x')")
    failed = writer.submit(bad)
    duplicate = writer.execute("INSERT INTO lyrics (key, status) VALUES ('before', 'x')")
    after = writer.execute("INSERT INTO lyrics (key, status) VALUES ('after', 'x')")
    first.result()
    with pytest.raises(ValueError, match="boom"):
        failed.result()
    with pytest.raises(sqlite3.IntegrityError):
        duplicate.result()
    after.result()
    conn = connect_read(db)
    assert [row["key"] for row in conn.execute("SELECT key FROM lyrics ORDER BY key")] == ["after", "before"]
    conn.close()


def test_executemany(writer):
    assert writer.executemany("INSERT INTO lyrics (key, status) VALUES (?, ?)",
                              [("a", "x"), ("b", "y")]).result() == 2


def test_stop_flushes_queued_jobs(db):
    writer = DbWriter(db).start()
    gate = threading.Event()
    writer.submit(lambda conn: gate.wait(5))
    futures = [writer.execute("INSERT INTO lyrics (key, status) VALUES (?, 'x')", (f"k{n}",)) for n in range(1000)]
    gate.set()
    writer.stop()
    assert all(f.done() and f.exception() is None for f in futures)
    conn = connect_read(db)
    assert conn.execute("SELECT count(*) FROM lyrics").fetchone()[0] == 1000
    conn.close()


def test_submit_after_stop_raises(db):
    writer = DbWriter(db).start()
    writer.stop()
    writer.stop()  # a second stop is harmless
    with pytest.raises(RuntimeError, match="not running"):
        writer.execute("SELECT 1")


def test_context_manager_stops(db):
    with DbWriter(db) as writer:
        assert writer.running
    assert not writer.running


def test_reader_is_read_only(writer, db):
    conn = connect_read(db)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO lyrics (key, status) VALUES ('k', 'x')")
    conn.close()


def test_reader_sees_committed_data_while_the_writer_runs(writer, db):
    conn = connect_read(db)
    assert conn.execute("SELECT count(*) FROM lyrics").fetchone()[0] == 0
    writer.execute("INSERT INTO lyrics (key, status) VALUES ('k', 'x')").result()
    assert conn.execute("SELECT status FROM lyrics WHERE key = 'k'").fetchone()["status"] == "x"
    conn.close()


def test_reader_after_the_writer_closed(db):
    with DbWriter(db) as writer:
        writer.execute("INSERT INTO lyrics (key, status) VALUES ('k', 'x')").result()
    conn = connect_read(db)
    assert conn.execute("SELECT count(*) FROM lyrics").fetchone()[0] == 1
    conn.close()


def test_reader_refuses_another_version(tmp_path):
    path = tmp_path / "old.db"
    sqlite3.connect(path).close()
    with pytest.raises(schema.SchemaError, match="expected"):
        connect_read(path)


# -- lyrics cache store --

ENTRIES = {
    "found": {"status": "found", "source": "lrclib", "lrclib_id": 7, "plain": "a\nb", "synced": None,
              "checked": "2026-10-15"},
    "instrumental": {"status": "instrumental", "lrclib_id": 9, "checked": "2026-10-15"},
    "not_found": {"status": "not_found", "checked": "2026-10-15"},
}


@pytest.mark.parametrize("name", list(ENTRIES))
def test_lyrics_store_round_trip(writer, db, name):
    conn = connect_read(db)
    store = LyricsStore(conn, writer)
    assert store.get("k") is None
    store.put("k", ENTRIES[name]).result()
    assert store.get("k") == ENTRIES[name]
    store.put("k", ENTRIES["not_found"]).result()  # replaces the earlier entry
    assert store.get("k") == ENTRIES["not_found"]
    conn.close()


def test_lookup_uses_the_store_like_a_dict_cache(writer, db):
    record = {"id": 7, "artistName": "A", "duration": 100, "plainLyrics": "x", "syncedLyrics": None}
    track = Track("A", "T", "Album", 100)
    today = date(2026, 10, 15)

    def client():
        return LrclibClient(urlopen=FakeUrlopen(record), sleep=SleepRecorder(), jitter=no_jitter)

    conn = connect_read(db)
    store, plain = LyricsStore(conn, writer), DictCache()
    assert lookup(track, store, client(), today=today) == lookup(track, plain, client(), today=today)
    writer.submit(lambda _conn: None).result()  # the put is committed
    key = cache_key("A", "T")
    assert store.get(key) == plain.get(key)
    assert lookup(track, store, None, offline=True, today=today) == plain.get(key)
    conn.close()
