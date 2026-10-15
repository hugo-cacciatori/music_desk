"""The index schema (SPEC §2) and its migrations, versioned through PRAGMA user_version.

A root's top folder is an entry with parent_id NULL whose name is the root's full path; every other entry holds
a plain name, so paths are rebuilt from parents and renaming a folder is one row update.
"""

import sqlite3

SCHEMA_VERSION = 3

BUSY_TIMEOUT_MS = 5000

# MIGRATIONS[n] takes a database from user_version n to n + 1.
MIGRATIONS: list[str] = [
    """
    CREATE TABLE roots (
        id        INTEGER PRIMARY KEY,
        path      TEXT NOT NULL UNIQUE COLLATE NOCASE,
        added_at  TEXT NOT NULL,
        last_scan TEXT,
        online    INTEGER NOT NULL DEFAULT 1
    );

    CREATE TABLE entries (
        id        INTEGER PRIMARY KEY,
        root_id   INTEGER NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
        parent_id INTEGER REFERENCES entries(id) ON DELETE CASCADE,
        name      TEXT NOT NULL,
        is_dir    INTEGER NOT NULL,
        ext       TEXT,
        size      INTEGER,
        mtime     REAL,
        scan_gen  INTEGER NOT NULL DEFAULT 0,
        UNIQUE (parent_id, name COLLATE NOCASE)
    );
    CREATE INDEX entries_parent ON entries(parent_id);
    CREATE INDEX entries_root ON entries(root_id);
    -- NULLs are distinct in UNIQUE, so the one-top-entry-per-root rule needs its own index.
    CREATE UNIQUE INDEX entries_top ON entries(root_id) WHERE parent_id IS NULL;

    CREATE TABLE tracks (
        entry_id        INTEGER PRIMARY KEY REFERENCES entries(id) ON DELETE CASCADE,
        format          TEXT,
        tag_family      TEXT,
        title           TEXT,
        artist          TEXT,
        albumartist     TEXT,
        album           TEXT,
        track           INTEGER,
        track_total     INTEGER,
        disc            INTEGER,
        disc_total      INTEGER,
        date            TEXT,
        genre           TEXT,
        composer        TEXT,
        comment         TEXT,
        has_lyrics_tag  INTEGER NOT NULL DEFAULT 0,
        has_lrc_sidecar INTEGER NOT NULL DEFAULT 0,
        has_cover       INTEGER NOT NULL DEFAULT 0,
        duration        REAL,
        bitrate         INTEGER,
        sample_rate     INTEGER,
        bit_depth       INTEGER,
        channels        INTEGER,
        rg_track_gain   TEXT,
        rg_track_peak   TEXT,
        rg_album_gain   TEXT,
        rg_album_peak   TEXT,
        lyrics_key      TEXT,
        read_size       INTEGER,
        read_mtime      REAL,
        read_error      TEXT
    );
    CREATE INDEX tracks_lyrics_key ON tracks(lyrics_key);

    -- The script's lyrics cache entries; key = casefold(artist + "\\x1f" + title).
    CREATE TABLE lyrics (
        key       TEXT PRIMARY KEY,
        status    TEXT NOT NULL,
        source    TEXT,
        lrclib_id INTEGER,
        plain     TEXT,
        synced    TEXT,
        checked   TEXT,
        error     TEXT
    );

    CREATE TABLE folder_stats (
        entry_id          INTEGER PRIMARY KEY REFERENCES entries(id) ON DELETE CASCADE,
        n_tracks          INTEGER NOT NULL DEFAULT 0,
        duration          REAL NOT NULL DEFAULT 0,
        n_with_rg         INTEGER NOT NULL DEFAULT 0,
        n_with_lyrics_tag INTEGER NOT NULL DEFAULT 0,
        n_lrclib_found    INTEGER NOT NULL DEFAULT 0,
        formats           TEXT
    );

    CREATE VIRTUAL TABLE search USING fts5(
        entry_id UNINDEXED, name, title, artist, albumartist, album, genre, lyrics,
        tokenize = 'unicode61 remove_diacritics 2'
    );
    """,
    # v2: search rows are keyed by rowid = entries.id (an UNINDEXED column can't be deleted from without a full
    # scan), and leave with their entry, including on cascades.
    """
    DROP TABLE search;
    CREATE VIRTUAL TABLE search USING fts5(
        name, title, artist, albumartist, album, genre, lyrics,
        tokenize = 'unicode61 remove_diacritics 2'
    );
    CREATE TRIGGER entries_search_delete AFTER DELETE ON entries BEGIN
        DELETE FROM search WHERE rowid = old.id;
    END;
    """,
    # v3 (SPEC §6): trigram tokens, so free text matches any substring as seek_lyrics' search did, and an lrclib
    # column with the LRCLIB plain text of found keys. The rows are copied over with their rowids.
    """
    CREATE TEMP TABLE search_v2 AS
        SELECT rowid AS id, name, title, artist, albumartist, album, genre, lyrics FROM search;
    DROP TABLE search;
    CREATE VIRTUAL TABLE search USING fts5(
        name, title, artist, albumartist, album, genre, lyrics, lrclib,
        tokenize = 'trigram remove_diacritics 1'
    );
    INSERT INTO search (rowid, name, title, artist, albumartist, album, genre, lyrics)
        SELECT id, name, title, artist, albumartist, album, genre, lyrics FROM search_v2;
    DROP TABLE search_v2;
    UPDATE search SET lrclib = (
        SELECT l.plain FROM tracks t JOIN lyrics l ON l.key = t.lyrics_key
        WHERE t.entry_id = search.rowid AND l.status = 'found'
    ) WHERE rowid IN (SELECT t.entry_id FROM tracks t JOIN lyrics l ON l.key = t.lyrics_key
                      WHERE l.status = 'found');
    DROP TRIGGER IF EXISTS entries_search_delete;
    CREATE TRIGGER entries_search_delete AFTER DELETE ON entries BEGIN
        DELETE FROM search WHERE rowid = old.id;
    END;
    """,
]

assert len(MIGRATIONS) == SCHEMA_VERSION


class SchemaError(Exception):
    """The database was written by a newer version of the app, or isn't at the expected version."""


def version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def configure(conn: sqlite3.Connection, *, write: bool) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    if write:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")


def migrate(conn: sqlite3.Connection) -> None:
    """Bring the database up to SCHEMA_VERSION, all steps in one transaction. A newer database is refused untouched."""
    current = version(conn)
    if current > SCHEMA_VERSION:
        raise SchemaError(f"the index is at schema version {current}, newer than this app's {SCHEMA_VERSION}")
    if current == SCHEMA_VERSION:
        return
    # executescript commits first and runs outside any transaction, so the steps are wrapped explicitly.
    script = "BEGIN;\n" + "\n".join(MIGRATIONS[current:]) + f"\nPRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;"
    try:
        conn.executescript(script)
    except sqlite3.Error:
        if conn.in_transaction:
            conn.execute("ROLLBACK")  # conn.rollback() is a no-op on autocommit connections
        raise
