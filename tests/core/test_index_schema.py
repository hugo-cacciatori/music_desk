"""core/index/schema.py: tables, constraints and migrations (SPEC §2)."""

import sqlite3

import pytest

from refrain.core.index import schema
from refrain.core.index.schema import SCHEMA_VERSION, SchemaError, configure, migrate
from refrain.core.index.store import path_of

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
    assert schema.version(conn) == SCHEMA_VERSION == 5
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
    conn.execute("INSERT INTO search (rowid, name, artist) VALUES (7, 'x.flac', 'Sigur Rós')")
    assert conn.execute("SELECT rowid FROM search WHERE search MATCH 'sigur ros'").fetchall() == [(7,)]
    assert conn.execute("SELECT rowid FROM search WHERE search MATCH 'artist:rós'").fetchall() == [(7,)]


def test_search_rows_leave_with_their_entries(conn):
    root, top = add_root(conn)
    folder = add_entry(conn, root, top, "Album", is_dir=True)
    track = add_entry(conn, root, folder, "01.flac")
    for entry in (folder, track):
        conn.execute("INSERT INTO search (rowid, name) VALUES (?, 'x')", (entry,))
    conn.execute("DELETE FROM entries WHERE id = ?", (folder,))  # the track goes by cascade
    assert conn.execute("SELECT count(*) FROM search").fetchone()[0] == 0


def test_v1_database_is_upgraded(tmp_path, monkeypatch):
    path = tmp_path / "library.db"
    conn = sqlite3.connect(path, autocommit=True)
    with monkeypatch.context() as patch:
        patch.setattr(schema, "SCHEMA_VERSION", 1)
        patch.setattr(schema, "MIGRATIONS", schema.MIGRATIONS[:1])
        migrate(conn)
    conn.execute("INSERT INTO roots (path, added_at) VALUES ('C:/Music', 'x')")
    migrate(conn)
    assert schema.version(conn) == SCHEMA_VERSION
    assert conn.execute("SELECT count(*) FROM roots").fetchone()[0] == 1
    conn.execute("INSERT INTO search (rowid, name) VALUES (1, 'x')")
    conn.close()


def test_v2_database_is_upgraded(tmp_path, monkeypatch):
    path = tmp_path / "library.db"
    conn = sqlite3.connect(path, autocommit=True)
    configure(conn, write=True)
    with monkeypatch.context() as patch:
        patch.setattr(schema, "SCHEMA_VERSION", 2)
        patch.setattr(schema, "MIGRATIONS", schema.MIGRATIONS[:2])
        migrate(conn)
    root, top = add_root(conn)
    folder = add_entry(conn, root, top, "Album", is_dir=True)
    found = add_entry(conn, root, folder, "01 Glove.flac")
    missing = add_entry(conn, root, folder, "02 Other.flac")
    conn.execute("INSERT INTO tracks (entry_id, title, artist, lyrics_key) VALUES (?, 'Glove', 'Björk', 'k1')",
                 (found,))
    conn.execute("INSERT INTO tracks (entry_id, title, lyrics_key) VALUES (?, 'Other', 'k2')", (missing,))
    conn.execute("INSERT INTO lyrics (key, status, plain) VALUES ('k1', 'found', 'lovely day')")
    conn.execute("INSERT INTO lyrics (key, status) VALUES ('k2', 'not_found')")
    conn.execute("INSERT INTO search (rowid, name) VALUES (?, 'Album')", (folder,))
    conn.execute("INSERT INTO search (rowid, name, title, artist, lyrics) VALUES (?, '01 Glove.flac', 'Glove', "
                 "'Björk', 'tag words')", (found,))
    conn.execute("INSERT INTO search (rowid, name, title) VALUES (?, '02 Other.flac', 'Other')", (missing,))
    migrate(conn)
    assert schema.version(conn) == SCHEMA_VERSION
    rows = conn.execute("SELECT rowid, name, title, artist, lyrics, lrclib FROM search ORDER BY rowid").fetchall()
    assert rows == [(folder, "Album", None, None, None, None),
                    (found, "01 Glove.flac", "Glove", "Björk", "tag words", "lovely day"),
                    (missing, "02 Other.flac", "Other", None, None, None)]
    assert conn.execute("SELECT rowid FROM search WHERE search MATCH '\"ove\"'").fetchall() == [(found,)]
    assert conn.execute("SELECT rowid FROM search WHERE search MATCH 'lrclib:\"vely\"'").fetchall() == [(found,)]
    assert conn.execute("SELECT rowid FROM search WHERE search MATCH '\"bjork\"'").fetchall() == [(found,)]
    conn.execute("DELETE FROM entries WHERE id = ?", (folder,))  # the trigger is back: the rows go with it
    assert conn.execute("SELECT count(*) FROM search").fetchone()[0] == 0
    assert not conn.execute("SELECT name FROM sqlite_temp_master").fetchall()
    conn.close()


def test_v3_database_rereads_id3_tags(tmp_path, monkeypatch):
    conn = sqlite3.connect(tmp_path / "library.db", autocommit=True)
    configure(conn, write=True)
    with monkeypatch.context() as patch:
        patch.setattr(schema, "SCHEMA_VERSION", 3)
        patch.setattr(schema, "MIGRATIONS", schema.MIGRATIONS[:3])
        migrate(conn)
    root, top = add_root(conn)
    tracks = {name: add_entry(conn, root, top, name) for name in ("a.mp3", "b.wav", "c.flac")}
    for entry_id, family in zip(tracks.values(), ("ID3", "ID3", "Vorbis"), strict=True):
        conn.execute("INSERT INTO tracks (entry_id, tag_family, read_size, read_mtime) VALUES (?, ?, 10, 1.5)",
                     (entry_id, family))
    migrate(conn)
    assert schema.version(conn) == SCHEMA_VERSION
    rows = dict(conn.execute("SELECT entry_id, read_mtime FROM tracks").fetchall())
    assert rows == {tracks["a.mp3"]: None, tracks["b.wav"]: None, tracks["c.flac"]: 1.5}
    conn.close()


def test_v4_database_forgets_ape_tracks(tmp_path, monkeypatch):
    """Schema v5 (SPEC §1 "Ignored", §2): a .ape is a plain file; its folders' totals and search row follow."""
    conn = sqlite3.connect(tmp_path / "library.db", autocommit=True)
    configure(conn, write=True)
    with monkeypatch.context() as patch:
        patch.setattr(schema, "SCHEMA_VERSION", 4)
        patch.setattr(schema, "MIGRATIONS", schema.MIGRATIONS[:4])
        migrate(conn)
    root, top = add_root(conn)
    album = add_entry(conn, root, top, "Album", is_dir=True)
    other = add_entry(conn, root, top, "Other", is_dir=True)
    files = {}
    for parent, name, ext in ((album, "image.flac", ".flac"), (album, "image.ape", ".ape"),
                              (other, "a.APE", ".ape"), (album, "notes.log", ".log")):
        files[name] = conn.execute("INSERT INTO entries (root_id, parent_id, name, is_dir, ext) VALUES (?, ?, ?, 0, ?)",
                                   (root, parent, name, ext)).lastrowid
    conn.execute("INSERT INTO lyrics (key, status, plain) VALUES ('k', 'found', 'words')")
    for name, fmt, duration in (("image.flac", "FLAC", 100.0), ("image.ape", "APE", 100.0), ("a.APE", "APE", 50.0)):
        conn.execute("INSERT INTO tracks (entry_id, format, title, duration, rg_track_gain, has_lyrics_tag, lyrics_key)"
                     " VALUES (?, ?, 'Title', ?, '-1 dB', 1, 'k')", (files[name], fmt, duration))
        conn.execute("INSERT INTO search (rowid, name, title, lrclib) VALUES (?, ?, 'Title', 'words')",
                     (files[name], name))
    conn.executemany("INSERT INTO folder_stats VALUES (?, ?, ?, ?, ?, ?, ?)",
                     [(top, 3, 250.0, 3, 3, 3, "APE, FLAC"), (album, 2, 200.0, 2, 2, 2, "APE, FLAC"),
                      (other, 1, 50.0, 1, 1, 1, "APE")])
    migrate(conn)
    assert schema.version(conn) == SCHEMA_VERSION
    assert [row[0] for row in conn.execute("SELECT entry_id FROM tracks")] == [files["image.flac"]]
    assert conn.execute("SELECT count(*) FROM entries").fetchone()[0] == 7  # the files stay, as files
    stats = {row[0]: row[1:] for row in conn.execute("SELECT * FROM folder_stats")}
    assert stats == {top: (1, 100.0, 1, 1, 1, "FLAC"), album: (1, 100.0, 1, 1, 1, "FLAC"),
                     other: (0, 0.0, 0, 0, 0, None)}
    search = conn.execute("SELECT rowid, name, title, lrclib FROM search ORDER BY rowid").fetchall()
    assert search == [(files["image.flac"], "image.flac", "Title", "words"),
                      (files["image.ape"], "image.ape", None, None), (files["a.APE"], "a.APE", None, None)]
    assert not conn.execute("SELECT name FROM sqlite_temp_master").fetchall()
    conn.close()
