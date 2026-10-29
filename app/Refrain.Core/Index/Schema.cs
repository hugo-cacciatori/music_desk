using Microsoft.Data.Sqlite;

namespace Refrain.Core.Index;

/// <summary>The index schema (SPEC §2), versioned through PRAGMA user_version. It starts from the Python app's v5
/// tables in one step; the index itself is never shared with the Python app, only rebuilt by a rescan.</summary>
/// <remarks>A root's top folder is the entry with parent_id NULL whose name is the root's full path; every other
/// entry holds a plain name, so paths are rebuilt from parents and renaming a folder is one row update.</remarks>
public static class Schema
{
    public const int Version = 1;
    const int BusyTimeoutMs = 5000;

    // Migrations[n] takes a database from user_version n to n + 1.
    static readonly string[] Migrations =
    [
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
            UNIQUE (parent_id, name COLLATE NOCASE)
        );
        CREATE INDEX entries_parent ON entries(parent_id);
        CREATE INDEX entries_root ON entries(root_id);
        -- NULLs are distinct in UNIQUE, so the one-top-entry-per-root rule needs its own index.
        CREATE UNIQUE INDEX entries_top ON entries(root_id) WHERE parent_id IS NULL;

        CREATE TABLE tracks (
            entry_id        INTEGER PRIMARY KEY REFERENCES entries(id) ON DELETE CASCADE,
            format          TEXT,
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
            has_cover       INTEGER NOT NULL DEFAULT 0,
            duration        REAL,
            bitrate         INTEGER,
            sample_rate     INTEGER,
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

        -- LRCLIB's results (task 6); key = casefold(artist + "\x1f" + title), as the Python app's.
        CREATE TABLE lyrics (
            key       TEXT PRIMARY KEY,
            status    TEXT NOT NULL,
            lrclib_id INTEGER,
            plain     TEXT,
            synced    TEXT,
            checked   TEXT
        );

        -- A folder's whole subtree.
        CREATE TABLE folder_stats (
            entry_id INTEGER PRIMARY KEY REFERENCES entries(id) ON DELETE CASCADE,
            n_tracks INTEGER NOT NULL DEFAULT 0,
            duration REAL NOT NULL DEFAULT 0
        );

        -- Free text (SPEC §6): trigram tokens, so any substring of 3 characters or more uses the index.
        CREATE VIRTUAL TABLE search USING fts5(
            name, title, artist, albumartist, album, genre, lrclib,
            tokenize = 'trigram remove_diacritics 1'
        );
        CREATE TRIGGER entries_search_delete AFTER DELETE ON entries BEGIN
            DELETE FROM search WHERE rowid = old.id;
        END;
        """,
    ];

    public static int CurrentVersion(SqliteConnection conn)
    {
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "PRAGMA user_version";
        return Convert.ToInt32(cmd.ExecuteScalar());
    }

    public static void Configure(SqliteConnection conn, bool write)
    {
        var pragmas = $"PRAGMA foreign_keys = ON; PRAGMA busy_timeout = {BusyTimeoutMs};";
        pragmas += write ? " PRAGMA journal_mode = WAL; PRAGMA synchronous = NORMAL;" : " PRAGMA query_only = ON;";
        using var cmd = conn.CreateCommand();
        cmd.CommandText = pragmas;
        cmd.ExecuteNonQuery();
    }

    /// <summary>Brings the database up to <see cref="Version"/>, all steps in one transaction. A newer database is
    /// refused untouched.</summary>
    public static void Migrate(SqliteConnection conn)
    {
        var current = CurrentVersion(conn);
        if (current > Version)
            throw new SchemaException($"The index is at schema version {current}, newer than this app's {Version}.");
        if (current == Version) return;
        using var tx = conn.BeginTransaction();
        using var cmd = conn.CreateCommand();
        cmd.Transaction = tx;
        cmd.CommandText = string.Join("\n", Migrations[current..]) + $"\nPRAGMA user_version = {Version};";
        cmd.ExecuteNonQuery();
        tx.Commit();
    }
}

public sealed class SchemaException(string message) : Exception(message);
