# 2. Data and storage
- `%LOCALAPPDATA%\MusicDesk\` holds:
  - `library.db` (index and lyrics cache), `settings.json`, `logs\app.log` (rotating);
  - `playlists\` (temporary .m3u8 files; the last 20 are kept).
- Nothing is stored inside music folders except tags, renames, `replaygain.log` and the playlists the user
  exports (§15), where the user saves them.
- Schema sketch:
  - `roots(id, path, added_at, last_scan, online)`
  - `entries(id, root_id, parent_id, name, is_dir, ext, size, mtime, scan_gen)`, indexed on `parent_id`, with
    `UNIQUE(parent_id, name COLLATE NOCASE)`. Paths are rebuilt from parents, so renaming a folder is one row update.
  - `tracks(entry_id PK, format, tag_family, title, artist, albumartist, album, track, track_total, disc,
    disc_total, date, genre, composer, comment, has_lyrics_tag, has_lrc_sidecar, has_cover, duration, bitrate,
    sample_rate, bit_depth, channels, rg_track_gain, rg_track_peak, rg_album_gain, rg_album_peak, lyrics_key,
    read_size, read_mtime, read_error)`
  - `lyrics(key PK, status, source, lrclib_id, plain, synced, checked, error)`: the script's cache entries.
    The key is `casefold(artist + "\x1f" + title)`, and tracks join to it through `tracks.lyrics_key`.
  - `folder_stats(entry_id, n_tracks, duration, n_with_rg, n_with_lyrics_tag, n_lrclib_found, formats)`,
    recomputed after scans.
  - `search` (FTS5): name, title, artist, albumartist, album, genre, plus lyrics text: `lyrics` (the tags', also
    read by the Lyrics tab for its tag note) and `lrclib` (the LRCLIB plain text). Schema v3, below.
    - The trigram tokenizer needs SQLite ≥ 3.45, the version bundled with Python 3.13 (3.45.3 checked).
    - Queries shorter than 3 characters can't use trigram. They fall back to `LIKE` on the worker thread, under
      the same result cap.
    - The trigram index costs about 3× the indexed text, which is accepted for the search it gives.
- One DB writer thread fed by a queue. The UI uses its own read-only connection.
- As built (M2 task 1, `core/index/`):
  - Schema version 1 is in `PRAGMA user_version`. Migrations run in one transaction, and an index from a newer
    app is refused without being touched.
  - A root's top folder is the `entries` row with `parent_id NULL`, and its `name` is the root's full path. A
    partial unique index allows one per root, because NULLs are distinct in UNIQUE.
  - `search` uses the `unicode61 remove_diacritics 2` tokenizer, so "Sigur Ros" finds "Sigur Rós". (Replaced by
    trigram with `remove_diacritics 1` in schema v3, at M6.)
- As built (M6 task 1): schema v3. `search` is `fts5(name, title, artist, albumartist, album, genre, lyrics, lrclib,
  tokenize='trigram remove_diacritics 1')`. The migration copies the v2 rows through a temporary table with their
  rowids, fills `lrclib` from the found keys, and recreates the delete trigger (tested from a v2 database).
  `lrclib` follows `record_lyrics` (the key's plain text when found, else NULL; the cache import goes through it
  too) and `_write_tracks` (a new track takes its key's text at once).
  - Connections: `foreign_keys=ON` and `busy_timeout=5000`. The writer adds `journal_mode=WAL` and
    `synchronous=NORMAL`. Readers open with `mode=ro` and `query_only`, and check the schema version.
  - `DbWriter.submit(fn)` runs each job in its own `BEGIN IMMEDIATE` transaction and returns a Future. A failing
    job is rolled back alone, and `stop()` finishes the queued jobs before closing.
  - `LyricsStore` serves the `lyrics` table as the lookup cache, with the script's entry shape per status.
