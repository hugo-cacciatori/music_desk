"""What a scan writes to the index after reading (SPEC §4): `tracks` and `search` rows, the `.lrc` sidecar flags,
the folder statistics, and which vanished entries are renames of new ones."""

import os
import sqlite3

from refrain.core.index.scan_read import TRACK_COLUMNS, Child
from refrain.core.lyrics import FOUND


def write_tracks(conn: sqlite3.Connection, chunk: list, results: list) -> None:
    columns = ", ".join(TRACK_COLUMNS)
    marks = ", ".join("?" * (len(TRACK_COLUMNS) + 1))
    for (entry_id, _parent, name, _size, _mtime), (row, lyrics) in zip(chunk, results, strict=True):
        if conn.execute("SELECT 1 FROM entries WHERE id = ?", (entry_id,)).fetchone() is None:
            continue  # gone since it was listed
        conn.execute(f"INSERT OR REPLACE INTO tracks (entry_id, {columns}) VALUES ({marks})",
                     (entry_id, *(row[c] for c in TRACK_COLUMNS)))
        conn.execute("DELETE FROM search WHERE rowid = ?", (entry_id,))
        # lrclib: a new copy of an already-found song is searchable by its lyrics at once (SPEC §6).
        conn.execute("INSERT INTO search (rowid, name, title, artist, albumartist, album, genre, lyrics, lrclib) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, "
                     "(SELECT plain FROM lyrics WHERE key = ? AND status = 'found'))",
                     (entry_id, name, row["title"], row["artist"], row["albumartist"], row["album"], row["genre"],
                      lyrics, row["lyrics_key"]))


_SIDECARS_SQL = """
    UPDATE tracks SET has_lrc_sidecar = EXISTS (
        SELECT 1 FROM entries s
        WHERE s.parent_id = e.parent_id AND s.ext = '.lrc'
          AND lower(substr(s.name, 1, length(s.name) - 4))
            = lower(substr(e.name, 1, length(e.name) - length(e.ext)))
    )
    FROM entries e WHERE e.id = tracks.entry_id AND e.{column} = ?
    """


def update_sidecars(conn: sqlite3.Connection, root_id: int) -> None:
    """has_lrc_sidecar: a same-stem .lrc next to the audio file (it can appear without the audio changing)."""
    conn.execute(_SIDECARS_SQL.format(column="root_id"), (root_id,))


def update_sidecars_in_folder(conn: sqlite3.Connection, folder_id: int) -> None:
    """has_lrc_sidecar for one folder's tracks (after a rename there)."""
    conn.execute(_SIDECARS_SQL.format(column="parent_id"), (folder_id,))


def match_renames(vanished: list, appeared: list[Child]) -> list[tuple]:
    """Pairs (row, child) that are the same entry under a new name: same kind, lowercase extension, size and
    mtime, and the only such pair. Anything ambiguous stays a delete plus an insert."""
    def key(is_dir, name, size, mtime):
        return (bool(is_dir), None if is_dir else os.path.splitext(name)[1].lower(), None if is_dir else size,
                mtime)

    old: dict[tuple, list] = {}
    for row in vanished:
        old.setdefault(key(row[2], row[1], row[3], row[4]), []).append(row)
    new: dict[tuple, list[Child]] = {}
    for child in appeared:
        new.setdefault(key(child.is_dir, child.name, child.size, child.mtime), []).append(child)
    return [(rows[0], new[k][0]) for k, rows in old.items() if len(rows) == 1 and len(new.get(k, [])) == 1]


def stats_values(conn: sqlite3.Connection, entry_id: int) -> tuple:
    """What one file adds to its folders' totals: (tracks, duration, with RG, with lyrics tag, LRCLIB found)."""
    row = conn.execute(
        """
        SELECT t.read_error IS NULL, t.duration, t.rg_track_gain IS NOT NULL, t.has_lyrics_tag, l.status = ?
        FROM tracks t LEFT JOIN lyrics l ON l.key = t.lyrics_key WHERE t.entry_id = ?
        """, (FOUND, entry_id)).fetchone()
    if row is None or not row[0]:
        return (0, 0.0, 0, 0, 0)
    return (1, row[1] or 0.0, int(bool(row[2])), int(bool(row[3])), int(bool(row[4])))


def adjust_folder_stats(conn: sqlite3.Connection, folder_id: int, before: tuple, after: tuple) -> None:
    """Apply one file's change to its folder and every folder above it (the formats list is left to scans)."""
    delta = tuple(a - b for a, b in zip(after, before, strict=True))
    if not any(delta):
        return
    folder = folder_id
    while folder is not None:
        conn.execute("INSERT OR IGNORE INTO folder_stats (entry_id) VALUES (?)", (folder,))
        conn.execute(
            "UPDATE folder_stats SET n_tracks = n_tracks + ?, duration = duration + ?, n_with_rg = n_with_rg + ?, "
            "n_with_lyrics_tag = n_with_lyrics_tag + ?, n_lrclib_found = n_lrclib_found + ? WHERE entry_id = ?",
            (*delta, folder))
        folder = conn.execute("SELECT parent_id FROM entries WHERE id = ?", (folder,)).fetchone()[0]


def update_folder_stats(conn: sqlite3.Connection, root_id: int) -> None:
    """Totals for every folder of the root, counting its whole subtree."""
    rows = conn.execute(
        """
        SELECT e.id, e.parent_id, e.is_dir, t.entry_id IS NOT NULL AND t.read_error IS NULL, t.duration, t.format,
               t.rg_track_gain IS NOT NULL, t.has_lyrics_tag, l.status = ?
        FROM entries e LEFT JOIN tracks t ON t.entry_id = e.id LEFT JOIN lyrics l ON l.key = t.lyrics_key
        WHERE e.root_id = ?
        """, (FOUND, root_id)).fetchall()
    parent = {row[0]: row[1] for row in rows}
    stats = {row[0]: [0, 0.0, 0, 0, 0, set()] for row in rows if row[2]}
    for _id, parent_id, is_dir, is_track, duration, fmt, has_rg, has_lyrics, found in rows:
        if is_dir or not is_track:
            continue
        folder = parent_id
        while folder is not None:
            s = stats[folder]
            s[0] += 1
            s[1] += duration or 0
            s[2] += bool(has_rg)
            s[3] += bool(has_lyrics)
            s[4] += bool(found)
            s[5].add(fmt)
            folder = parent[folder]
    conn.execute("DELETE FROM folder_stats WHERE entry_id IN (SELECT id FROM entries WHERE root_id = ?)", (root_id,))
    conn.executemany(
        "INSERT INTO folder_stats (entry_id, n_tracks, duration, n_with_rg, n_with_lyrics_tag, n_lrclib_found, "
        "formats) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(folder, s[0], s[1], s[2], s[3], s[4], ", ".join(sorted(f for f in s[5] if f)) or None)
         for folder, s in stats.items()])
