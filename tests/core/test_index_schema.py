"""core/index/schema.py: tables, constraints and migrations (SPEC §2)."""

import sqlite3

import pytest

from music_desk.core.index import schema
from music_desk.core.index.schema import SCHEMA_VERSION, SchemaError, configure, migrate
from music_desk.core.index.store import path_of

TABLES = {"roots", "entries", "tracks", "lyrics", "folder_stats", "search"}


@pytest.fixture
def conn(tmp_path):
    conn = sqlite3.connect(tmp_path / "library.db", autocommit=True)
    configure(conn, write=True)
    migrate(conn)
    yield conn
    conn.close()


def add_root(conn, path=r"C:\Music") -> tuple[int, int]:
    root = conn.execute("INSERT INTO roots (path, added_at) VALUES (?, '2026-10-15')", (path,)).lastrowid
    top = conn.execute("INSERT INTO entries (root_id, parent_id, name, is_dir) VALUES (?, NULL, ?, 1)",
                       (root, path)).lastrowid
    return root, top


def add_entry(conn, root, parent, name, is_dir=False) -> int:
    return conn.execute("INSERT INTO entries (root_id, parent_id, name, is_dir) VALUES (?, ?, ?, ?)",
                        (root, parent, name, int(is_dir))).lastrowid


def test_fresh_database(conn):
    assert schema.version(conn) == SCHEMA_VERSION == 1
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'index')")}
    assert TABLES <= names
    assert {"entries_parent", "entries_root", "entries_top", "tracks_lyrics_key"} <= names
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migrating_again_changes_nothing(conn):
    before = conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    migrate(conn)
    assert conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall() == before


def test_newer_database_is_refused_untouched(tmp_path):
    path = tmp_path / "library.db"
    conn = sqlite3.connect(path, autocommit=True)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.close()
    before = path.read_bytes()
    conn = sqlite3.connect(path, autocommit=True)
    with pytest.raises(SchemaError, match="newer"):
        migrate(conn)
    conn.close()
    assert path.read_bytes() == before


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(schema, "MIGRATIONS", ["CREATE TABLE a (x); CREATE TABLE a (x);"])
    conn = sqlite3.connect(tmp_path / "library.db", autocommit=True)
    with pytest.raises(sqlite3.OperationalError):
        migrate(conn)
    assert schema.version(conn) == 0
    assert conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0
    conn.close()


def test_siblings_differing_in_case_are_refused(conn):
    root, top = add_root(conn)
    add_entry(conn, root, top, "Album")
    with pytest.raises(sqlite3.IntegrityError):
        add_entry(conn, root, top, "ALBUM")


def test_one_top_entry_per_root(conn):
    root, _top = add_root(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO entries (root_id, parent_id, name, is_dir) VALUES (?, NULL, 'again', 1)", (root,))


def test_roots_differing_in_case_are_refused(conn):
    add_root(conn, r"C:\Music")
    with pytest.raises(sqlite3.IntegrityError):
        add_root(conn, r"c:\music")


def test_deleting_a_root_cascades(conn):
    root, top = add_root(conn)
    folder = add_entry(conn, root, top, "Album", is_dir=True)
    track = add_entry(conn, root, folder, "01.flac")
    conn.execute("INSERT INTO tracks (entry_id, title) VALUES (?, 'One')", (track,))
    conn.execute("INSERT INTO folder_stats (entry_id, n_tracks) VALUES (?, 1)", (folder,))
    conn.execute("DELETE FROM roots WHERE id = ?", (root,))
    for table in ("entries", "tracks", "folder_stats"):
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_paths_are_rebuilt_from_parents(conn):
    root, top = add_root(conn, r"C:\Music")
    artist = add_entry(conn, root, top, "Artist", is_dir=True)
    album = add_entry(conn, root, artist, "Album", is_dir=True)
    track = add_entry(conn, root, album, "01 One.flac")
    assert path_of(conn, top).as_posix() == "C:/Music"
    assert str(path_of(conn, track)) == r"C:\Music\Artist\Album\01 One.flac"
    conn.execute("UPDATE entries SET name = 'Renamed' WHERE id = ?", (artist,))
    assert str(path_of(conn, track)) == r"C:\Music\Renamed\Album\01 One.flac"
    with pytest.raises(KeyError):
        path_of(conn, 9999)


def test_search_ignores_accents(conn):
    conn.execute("INSERT INTO search (entry_id, name, artist) VALUES (7, 'x.flac', 'Sigur Rós')")
    assert conn.execute("SELECT entry_id FROM search WHERE search MATCH 'sigur ros'").fetchall() == [(7,)]
    assert conn.execute("SELECT entry_id FROM search WHERE search MATCH 'artist:rós'").fetchall() == [(7,)]
