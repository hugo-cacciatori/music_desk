"""Read queries for the browser (SPEC §5): a folder's subfolders for the tree, and its full listing for the file
list. They run on the UI's read-only connection and only touch indexed data, so opening a folder is instant even
on a slow drive."""

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from refrain.core.formats import AUDIO_EXTENSIONS
from refrain.core.fsops import natural_key
from refrain.core.index.roots import is_inside
from refrain.core.index.store import consistent_read, path_of
from refrain.core.lyrics import FOUND, INSTRUMENTAL, NOT_FOUND
from refrain.core.playlists import PlaylistEntry

_AUDIO_SQL = ", ".join(f"'{ext}'" for ext in sorted(AUDIO_EXTENSIONS))


@dataclass(frozen=True)
class FolderRow:
    id: int
    name: str
    has_subfolders: bool
    n_tracks: int


@dataclass(frozen=True)
class FileRow:
    """One row of the file list: a folder or a file, with its tag and health columns."""

    id: int
    name: str
    is_dir: bool
    ext: str | None
    size: int | None
    mtime: float | None
    is_audio: bool = False
    track: int | None = None
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    year: str | None = None
    duration: float | None = None
    format: str | None = None
    bitrate: int | None = None
    rg: str | None = None  # "full", "partial", "none" (audio only)
    # "synced", "plain", "instrumental", "not found", "pending", "no tags", "" when unreadable (audio only); the
    # window shows "error" or "offline" in place of "pending" from the lyrics service's state
    lrclib: str | None = None
    read_error: str | None = None
    n_tracks: int | None = None  # folders: tracks in the subtree
    lyrics_key: str | None = None


def child_folders(conn: sqlite3.Connection, parent_id: int, *, only_music: bool = False) -> list[FolderRow]:
    """Subfolders in Explorer's natural order. With only_music, folders without any track below are left out."""
    rows = conn.execute(
        """
        SELECT e.id, e.name,
               EXISTS (SELECT 1 FROM entries c WHERE c.parent_id = e.id AND c.is_dir = 1),
               coalesce(s.n_tracks, 0)
        FROM entries e LEFT JOIN folder_stats s ON s.entry_id = e.id
        WHERE e.parent_id = ? AND e.is_dir = 1
        """, (parent_id,)).fetchall()
    folders = [FolderRow(r[0], r[1], bool(r[2]), r[3]) for r in rows]
    if only_music:
        folders = [f for f in folders if f.n_tracks]
    return sorted(folders, key=lambda f: natural_key(f.name))


def _rg_state(gains: tuple) -> str:
    present = sum(value is not None for value in gains)
    return "full" if present == len(gains) else "partial" if present else "none"


def _lrclib_state(status: str | None, synced: str | None, key: str | None = "", read_error: str | None = None) -> str:
    if read_error:
        return ""  # the tags can't be read: nothing to look up (the row's tooltip says why)
    if key is None:
        return "no tags"  # no artist or title, even from the file name: never looked up
    if status == FOUND:
        return "synced" if synced else "plain"
    if status == INSTRUMENTAL:
        return "instrumental"
    if status == NOT_FOUND:
        return "not found"
    return "pending"


# The file list's columns for a set of entries; the WHERE clause is the caller's (a folder, or search results).
LISTING_SELECT = """
    SELECT e.id, e.name, e.is_dir, e.ext, e.size, e.mtime,
           t.entry_id IS NOT NULL, t.track, t.title, t.artist, t.album, t.date, coalesce(t.duration, s.duration),
           t.format, t.bitrate, t.rg_track_gain, t.rg_track_peak, t.rg_album_gain, t.rg_album_peak,
           l.status, l.synced, t.read_error, s.n_tracks, t.lyrics_key
    FROM entries e
    LEFT JOIN tracks t ON t.entry_id = e.id
    LEFT JOIN lyrics l ON l.key = t.lyrics_key
    LEFT JOIN folder_stats s ON s.entry_id = e.id
"""


def listing_fields(r: tuple) -> dict:
    """FileRow's fields from a LISTING_SELECT row."""
    is_audio = bool(r[6])
    return dict(
        id=r[0], name=r[1], is_dir=bool(r[2]), ext=r[3], size=r[4], mtime=r[5], is_audio=is_audio,
        track=r[7], title=r[8], artist=r[9], album=r[10], year=r[11], duration=r[12], format=r[13],
        bitrate=r[14],
        rg=_rg_state(r[15:19]) if is_audio else None,
        lrclib=_lrclib_state(r[19], r[20], r[23], r[21]) if is_audio else None,
        read_error=r[21], n_tracks=r[22] if r[2] else None, lyrics_key=r[23],
    )


def folder_listing(conn: sqlite3.Connection, folder_id: int) -> list[FileRow]:
    """Every entry of a folder (folders and files, all of them), in no particular order: the view sorts."""
    rows = conn.execute(f"{LISTING_SELECT} WHERE e.parent_id = ?", (folder_id,)).fetchall()
    return [FileRow(**listing_fields(r)) for r in rows]


def folder_crumbs(conn: sqlite3.Connection, entry_id: int) -> list[tuple[int, str]]:
    """(id, name) from the root's top entry down to this entry; the top entry's name is the root path."""
    rows = conn.execute(
        """
        WITH RECURSIVE up(id, parent_id, name, depth) AS (
            SELECT id, parent_id, name, 0 FROM entries WHERE id = ?
            UNION ALL
            SELECT e.id, e.parent_id, e.name, up.depth + 1 FROM entries e JOIN up ON e.id = up.parent_id
        )
        SELECT id, name FROM up ORDER BY depth DESC
        """, (entry_id,)).fetchall()
    return [(row[0], row[1]) for row in rows]


def parent_folder(conn: sqlite3.Connection, entry_id: int) -> int | None:
    row = conn.execute("SELECT parent_id FROM entries WHERE id = ?", (entry_id,)).fetchone()
    return row[0] if row else None


def find_child(conn: sqlite3.Connection, folder_id: int, name: str) -> int | None:
    row = conn.execute("SELECT id FROM entries WHERE parent_id = ? AND name = ? COLLATE NOCASE",
                       (folder_id, name)).fetchone()
    return row[0] if row else None


def folder_path_ids(conn: sqlite3.Connection, entry_id: int) -> list[int]:
    """The ids from the root's top entry down to this entry (to expand the tree to a folder)."""
    rows = conn.execute(
        """
        WITH RECURSIVE up(id, parent_id, depth) AS (
            SELECT id, parent_id, 0 FROM entries WHERE id = ?
            UNION ALL
            SELECT e.id, e.parent_id, up.depth + 1 FROM entries e JOIN up ON e.id = up.parent_id
        )
        SELECT id FROM up ORDER BY depth DESC
        """, (entry_id,)).fetchall()
    return [row[0] for row in rows]


def _entries(conn: sqlite3.Connection, ids: list[int]) -> list[PlaylistEntry]:
    """Playlist entries from the index (no file is read), in the order given, audio files only: by extension, so
    a file whose tags aren't read yet plays too (without its duration and tags)."""
    if not ids:
        return []
    marks = ", ".join("?" * len(ids))
    rows = {r[0]: r for r in conn.execute(
        f"SELECT e.id, t.duration, t.artist, t.title FROM entries e LEFT JOIN tracks t ON t.entry_id = e.id "
        f"WHERE e.id IN ({marks}) AND e.is_dir = 0 AND e.ext IN ({_AUDIO_SQL})", ids)}
    folder_paths: dict[int, str] = {}
    entries = []
    for entry_id in ids:
        row = rows.get(entry_id)
        if row is None:
            continue  # not audio
        parent_id, name = conn.execute("SELECT parent_id, name FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if parent_id not in folder_paths:
            folder_paths[parent_id] = str(path_of(conn, parent_id))
        entries.append(PlaylistEntry(Path(folder_paths[parent_id], name), row[1], row[2], row[3]))
    return entries


def playlist_for_ids(conn: sqlite3.Connection, ids: list[int]) -> list[PlaylistEntry]:
    """Play selection: the selected audio files, in the given (view) order."""
    return _entries(conn, ids)


def folder_entry_ids(conn: sqlite3.Connection, folder_id: int, recursive: bool = False) -> list[int]:
    """The folder's files in natural order, then (recursive) each subfolder's, in natural order: Explorer's
    order, as Play folder and the playlist builder use it (SPEC §14, §15). From the index only."""
    ids: list[int] = []

    def visit(folder: int) -> None:
        rows = conn.execute("SELECT e.id, e.name, e.is_dir FROM entries e WHERE e.parent_id = ?",
                            (folder,)).fetchall()
        files = sorted((r for r in rows if not r[2]), key=lambda r: natural_key(r[1]))
        ids.extend(r[0] for r in files)
        if recursive:
            for sub in sorted((r for r in rows if r[2]), key=lambda r: natural_key(r[1])):
                visit(sub[0])

    visit(folder_id)
    return ids


def file_count(conn: sqlite3.Connection, folder_id: int) -> int:
    """The files anywhere under a folder, from the index, so no folder is walked (Delete's question, SPEC §19)."""
    return conn.execute("""
        WITH RECURSIVE sub(id) AS (
            SELECT ? UNION ALL SELECT e.id FROM entries e JOIN sub ON e.parent_id = sub.id WHERE e.is_dir = 1)
        SELECT COUNT(*) FROM entries WHERE is_dir = 0 AND parent_id IN (SELECT id FROM sub)""",
                        (folder_id,)).fetchone()[0]


def playlist_for_folder(conn: sqlite3.Connection, folder_id: int, recursive: bool = False) -> list[PlaylistEntry]:
    """Play folder (SPEC §14). Read from the index, so it is instant even on a slow drive."""
    return _entries(conn, folder_entry_ids(conn, folder_id, recursive))


def track_ids_for(conn: sqlite3.Connection, ids: list[int]) -> list[int]:
    """Dropped items, in the order given, as tracks: a folder brings every track under it (Explorer's order), a
    track itself; anything else (a cover, a text file) is left out. For the playlist builder (SPEC §15)."""
    tracks: list[int] = []
    for entry_id in ids:
        row = conn.execute("SELECT is_dir FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if row is None:
            continue
        candidates = folder_entry_ids(conn, entry_id, recursive=True) if row[0] else [entry_id]
        if not candidates:
            continue
        marks = ", ".join("?" * len(candidates))
        audio = {r[0] for r in conn.execute(f"SELECT entry_id FROM tracks WHERE entry_id IN ({marks})", candidates)}
        tracks.extend(i for i in candidates if i in audio)
    return tracks


@dataclass(frozen=True)
class TrackRow:
    """A playlist builder row: a track's columns (SPEC §15), from the index."""

    id: int
    path: Path
    name: str
    folder: str
    title: str | None
    artist: str | None
    album: str | None
    albumartist: str | None
    track: int | None
    disc: int | None
    year: str | None
    genre: str | None
    duration: float | None
    format: str | None


def track_rows(conn: sqlite3.Connection, ids: list[int]) -> dict[int, TrackRow]:
    """The rows of the given tracks (ids that left the index are absent). Folder paths are built once each."""
    rows: dict[int, TrackRow] = {}
    folders: dict[int, str] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        marks = ", ".join("?" * len(chunk))
        for r in conn.execute(
                f"""
                SELECT e.id, e.parent_id, e.name, t.title, t.artist, t.album, t.albumartist, t.track, t.disc, t.date,
                       t.genre, t.duration, t.format
                FROM entries e JOIN tracks t ON t.entry_id = e.id WHERE e.id IN ({marks})
                """, chunk):
            if r[1] not in folders:
                folders[r[1]] = str(path_of(conn, r[1]))
            rows[r[0]] = TrackRow(r[0], Path(folders[r[1]], r[2]), r[2], folders[r[1]], *r[3:])
    return rows


@dataclass(frozen=True)
class FolderSummary:
    """What the side panel shows for a folder (SPEC §7), from the index only."""

    id: int
    path: Path
    n_tracks: int  # the whole subtree
    duration: float
    formats: str | None
    n_with_rg: int
    n_lrclib_found: int
    n_subfolders: int  # direct
    n_files: int  # direct
    first_track: Path | None  # for the cover when there is no folder image


def first_track(conn: sqlite3.Connection, folder_id: int) -> Path | None:
    """The first audio file in natural order: the folder's own, else level by level below it (a breadth-first
    walk that stops at the first level with audio, so a huge root stays cheap)."""
    level = [folder_id]
    while level:
        marks = ", ".join("?" * len(level))
        rows = conn.execute(
            f"SELECT e.id, e.parent_id, e.name, e.is_dir, t.entry_id IS NOT NULL FROM entries e "
            f"LEFT JOIN tracks t ON t.entry_id = e.id WHERE e.parent_id IN ({marks})", level).fetchall()
        order = {folder: n for n, folder in enumerate(level)}
        tracks = sorted((r for r in rows if r[4]), key=lambda r: (order[r[1]], natural_key(r[2])))
        if tracks:
            return path_of(conn, tracks[0][0])
        subfolders = sorted((r for r in rows if r[3]), key=lambda r: (order[r[1]], natural_key(r[2])))
        level = [r[0] for r in subfolders]
    return None


def folder_summary(conn: sqlite3.Connection, folder_id: int) -> FolderSummary | None:
    with consistent_read(conn):
        return _folder_summary(conn, folder_id)


def _folder_summary(conn: sqlite3.Connection, folder_id: int) -> FolderSummary | None:
    if conn.execute("SELECT 1 FROM entries WHERE id = ? AND is_dir = 1", (folder_id,)).fetchone() is None:
        return None
    stats = conn.execute(
        "SELECT n_tracks, duration, formats, n_with_rg, n_lrclib_found FROM folder_stats "
        "WHERE entry_id = ?", (folder_id,)).fetchone() or (0, 0.0, None, 0, 0)
    subfolders, files = conn.execute(
        "SELECT coalesce(sum(is_dir), 0), coalesce(sum(1 - is_dir), 0) FROM entries WHERE parent_id = ?",
        (folder_id,)).fetchone()
    return FolderSummary(
        id=folder_id, path=path_of(conn, folder_id), n_tracks=stats[0] or 0, duration=stats[1] or 0.0,
        formats=stats[2], n_with_rg=stats[3] or 0, n_lrclib_found=stats[4] or 0,
        n_subfolders=subfolders, n_files=files, first_track=first_track(conn, folder_id),
    )


def find_folder(conn: sqlite3.Connection, path: str | Path) -> tuple[int, bool] | None:
    """The indexed folder for a path: (entry id, True) when indexed, else its nearest indexed ancestor
    (entry id, False). None when the path is outside every root. A file path resolves to its folder's lookup."""
    path = os.path.normpath(os.path.abspath(path))
    for root_path, top in conn.execute(
            "SELECT r.path, e.id FROM roots r JOIN entries e ON e.root_id = r.id AND e.parent_id IS NULL"):
        if os.path.normcase(path) == os.path.normcase(root_path):
            return top, True
        if not is_inside(path, root_path):
            continue
        current = top
        for name in Path(path).relative_to(root_path).parts:
            row = conn.execute("SELECT id, is_dir FROM entries WHERE parent_id = ? AND name = ? COLLATE NOCASE",
                               (current, name)).fetchone()
            if row is None or not row[1]:
                return current, False
            current = row[0]
        return current, True
    return None
