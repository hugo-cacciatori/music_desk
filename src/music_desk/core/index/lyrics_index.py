"""The index side of the lyrics service (SPEC §9): which tracks still need an LRCLIB lookup, and recording a result
with the folder totals and the LRCLIB column following it. Plain Python, no Qt.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from music_desk.core.index.scanner import adjust_folder_stats
from music_desk.core.index.store import read_lyrics, upsert_lyrics
from music_desk.core.lyrics import FOUND, NOT_FOUND, LyricsOptions, Track, track_from_fields
from music_desk.core.tags import Fields


@dataclass(frozen=True)
class PendingTrack:
    entry_id: int
    folder_id: int
    key: str  # tracks.lyrics_key: one lookup serves every track with this key
    track: Track


@dataclass(frozen=True)
class LyricsInfo:
    """What the Lyrics tab shows for one track, all from the index (no file read on the UI thread)."""

    entry_id: int
    name: str
    track: Track  # with the file-name fallback; `embedded` is the file's lyrics tag as last indexed
    key: str | None  # None: no artist or title, never looked up
    entry: dict | None  # the cached LRCLIB entry, if any
    read_error: str | None


_TRACK_SELECT = """
    SELECT t.entry_id, e.parent_id, e.name, t.artist, t.albumartist, t.title, t.album, t.duration, t.lyrics_key
    FROM tracks t
    JOIN entries e ON e.id = t.entry_id
"""


def _track(name, artist, albumartist, title, album, duration, embedded=None) -> Track:
    """Rebuilt as the scanner built it, so the "Artist - Title" file-name fallback gives the same key."""
    fields = Fields(title=title, artist=artist, albumartist=albumartist, album=album, lyrics=embedded)
    return track_from_fields(fields, name, duration)


def retry_before(today: date, options: LyricsOptions | None = None) -> str:
    """not_found entries checked before this date are looked up again (core.lyrics.is_fresh's rule)."""
    return (today - timedelta(days=(options or LyricsOptions()).not_found_retry_days)).isoformat()


def pending_tracks(conn: sqlite3.Connection, retry_before: str, *, folder_id: int | None = None,
                   entry_id: int | None = None, skip: set[str] | frozenset[str] = frozenset(),
                   limit: int | None = None) -> list[PendingTrack]:
    """Tracks whose key has no cache entry, or only a stale not_found, one per key, folder by folder.

    Tracks without artist or title (no key) and unreadable files are never pending. `folder_id` or `entry_id`
    narrow it to one folder's files or one file; keys in `skip` are left out.
    """
    where, params = "", [NOT_FOUND, retry_before]
    if folder_id is not None:
        where, params = " AND e.parent_id = ?", [*params, folder_id]
    if entry_id is not None:
        where, params = f"{where} AND e.id = ?", [*params, entry_id]
    rows = conn.execute(
        f"""{_TRACK_SELECT}
        LEFT JOIN lyrics l ON l.key = t.lyrics_key
        WHERE t.lyrics_key IS NOT NULL AND t.read_error IS NULL
          AND (l.key IS NULL OR (l.status = ? AND l.checked < ?)){where}
        ORDER BY e.parent_id, e.name COLLATE NOCASE
        """, params)
    pending, seen = [], set(skip)
    try:
        for entry, folder, name, artist, albumartist, title, album, duration, key in rows:
            if key in seen:
                continue
            seen.add(key)
            pending.append(PendingTrack(entry, folder, key, _track(name, artist, albumartist, title, album, duration)))
            if limit is not None and len(pending) >= limit:
                break
    finally:
        rows.close()
    return pending


def track_for_entry(conn: sqlite3.Connection, entry_id: int) -> PendingTrack | None:
    """One track to look up again, whatever its cache entry says (Retry now). None without a key."""
    row = conn.execute(f"{_TRACK_SELECT} WHERE t.entry_id = ? AND t.lyrics_key IS NOT NULL AND t.read_error IS NULL",
                       (entry_id,)).fetchone()
    if row is None:
        return None
    entry, folder, name, artist, albumartist, title, album, duration, key = row
    return PendingTrack(entry, folder, key, _track(name, artist, albumartist, title, album, duration))


def lyrics_info(conn: sqlite3.Connection, entry_id: int) -> LyricsInfo | None:
    """The Lyrics tab's view of one audio file; None when it isn't an indexed track."""
    row = conn.execute(
        """
        SELECT e.name, t.artist, t.albumartist, t.title, t.album, t.duration, t.lyrics_key, t.read_error, s.lyrics
        FROM tracks t JOIN entries e ON e.id = t.entry_id LEFT JOIN search s ON s.rowid = t.entry_id
        WHERE t.entry_id = ?
        """, (entry_id,)).fetchone()
    if row is None:
        return None
    name, artist, albumartist, title, album, duration, key, read_error, embedded = row
    track = _track(name, artist, albumartist, title, album, duration, embedded)
    return LyricsInfo(entry_id, name, track, key, read_lyrics(conn, key) if key else None, read_error)


def record_lyrics(conn: sqlite3.Connection, key: str, entry: dict) -> list[int]:
    """Store a lookup result (a writer job). When the key becomes found, or stops being found, n_lrclib_found
    follows up to the root for every track with that key. Returns the folders whose files carry the key, whose
    LRCLIB column changed."""
    old = conn.execute("SELECT status FROM lyrics WHERE key = ?", (key,)).fetchone()
    was_found = old is not None and old[0] == FOUND
    upsert_lyrics(conn, key, entry)
    found = entry.get("status") == FOUND
    folders = [row[0] for row in conn.execute(
        "SELECT e.parent_id FROM tracks t JOIN entries e ON e.id = t.entry_id "
        "WHERE t.lyrics_key = ? AND t.read_error IS NULL", (key,))]
    if found != was_found:
        delta = 1 if found else -1
        for folder in folders:  # once per track: two tracks in one folder count twice
            adjust_folder_stats(conn, folder, (0, 0.0, 0, 0, 0), (0, 0.0, 0, 0, delta))
    return sorted(set(folders))
