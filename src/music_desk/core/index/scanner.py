"""The indexer's scans (SPEC §4): phase A walks the folder structure, phase B reads tags. Both are incremental.

- Phase A is a breadth-first `os.scandir` walk, so the top levels reach the index first. Each folder is reconciled
  against its indexed children: new entries are inserted, changed ones (size, mtime, case of the name) updated,
  and vanished ones deleted, with their subtrees, by cascade. Writes go to the DbWriter in batches of about
  `batch_rows` rows or `batch_seconds`.
- Phase B reads the tags of new or changed audio files (size or mtime differ from the last read) in a thread pool,
  through core/tags.py, and stores them in `tracks` and the `search` index.
- Then the `.lrc` sidecar flags and the folder statistics of the root are recomputed.

A folder that can't be listed keeps its index untouched, and an unreachable root isn't scanned at all, so an
unplugged drive never empties the index. Junctions and symbolic links aren't followed (they could loop).
Nothing on disk is ever changed: the scanner only reads.
"""

import logging
import os
import sqlite3
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from music_desk.core.cancel import CancelToken
from music_desk.core.formats import AUDIO_EXTENSIONS
from music_desk.core.index.roots import is_inside
from music_desk.core.index.store import DbWriter, connect_read, path_of
from music_desk.core.lyrics import FOUND, cache_key, track_from_fields
from music_desk.core.tags import TaggedFile

log = logging.getLogger(__name__)

RG_KEYS = ("replaygain_track_gain", "replaygain_track_peak", "replaygain_album_gain", "replaygain_album_peak")
TRACK_COLUMNS = (
    "format", "tag_family", "title", "artist", "albumartist", "album", "track", "track_total", "disc", "disc_total",
    "date", "genre", "composer", "comment", "has_lyrics_tag", "has_cover", "duration", "bitrate", "sample_rate",
    "bit_depth", "channels", "rg_track_gain", "rg_track_peak", "rg_album_gain", "rg_album_peak", "lyrics_key",
    "read_size", "read_mtime", "read_error",
)
_AUDIO_SQL = ", ".join(f"'{ext}'" for ext in sorted(AUDIO_EXTENSIONS))


@dataclass
class ScanProgress:
    root_id: int
    phase: str  # "structure", "tags", "stats" or "done"
    folders: int = 0  # listed so far
    files: int = 0  # seen so far
    tags_done: int = 0
    tags_total: int = 0


@dataclass
class ScanReport:
    root_id: int
    folders: int = 0
    files: int = 0
    added: int = 0
    updated: int = 0
    removed: int = 0
    unreadable_folders: int = 0
    tags_read: int = 0
    tag_errors: int = 0
    offline: bool = False
    cancelled: bool = False
    folder_id: int | None = None  # the scanned folder (the root's top entry for a root scan)


@dataclass
class _Child:
    name: str
    is_dir: bool
    ext: str | None
    size: int | None
    mtime: float


class _Ref:
    """A folder entry id, known now or once the batch that inserts it is written."""

    __slots__ = ("id",)

    def __init__(self, entry_id: int | None = None) -> None:
        self.id = entry_id


class _Batch:
    def __init__(self) -> None:
        self.clear()

    def clear(self) -> None:
        self.deletes: list[int] = []
        self.updates: list[tuple] = []  # (name, size, mtime, gen, id)
        self.inserts: list[tuple[_Ref, _Child, _Ref | None]] = []  # (parent, child, ref for a new folder)
        self.started = time.monotonic()

    @property
    def rows(self) -> int:
        return len(self.deletes) + len(self.updates) + len(self.inserts)


def list_folder(path: str) -> list[_Child]:
    """The folder's entries, without following junctions or symbolic links. Raises OSError if it can't be listed."""
    children = []
    with os.scandir(path) as entries:
        for entry in entries:
            try:
                if entry.is_symlink() or entry.is_junction():
                    log.debug("not following link %s", entry.path)
                    continue
                is_dir = entry.is_dir(follow_symlinks=False)
                stat = entry.stat(follow_symlinks=False)  # cached by scandir on Windows
            except OSError as e:
                log.warning("skipping %s: %s", entry.path, e)
                continue
            ext = None if is_dir else os.path.splitext(entry.name)[1].lower()
            children.append(_Child(entry.name, is_dir, ext, None if is_dir else stat.st_size, stat.st_mtime))
    return children


def read_file(path: Path, size: int | None, mtime: float | None) -> tuple[dict, str | None]:
    """A `tracks` row for an audio file, and the lyrics text for the search index. Never raises."""
    row = dict.fromkeys(TRACK_COLUMNS)
    row.update(format=path.suffix.lstrip(".").upper(), read_size=size, read_mtime=mtime,
               has_lyrics_tag=0, has_cover=0)
    try:
        tagged = TaggedFile(path)
        fields = tagged.fields()
        stream = tagged.stream_info()
        track = track_from_fields(fields, path.name, stream.duration)
        row.update(
            tag_family=tagged.info.family.value, title=fields.title, artist=fields.artist,
            albumartist=fields.albumartist, album=fields.album, track=fields.track, track_total=fields.track_total,
            disc=fields.disc, disc_total=fields.disc_total, date=fields.year, genre=fields.genre,
            composer=fields.composer, comment=fields.comment, has_lyrics_tag=int(bool(track.embedded)),
            has_cover=int(tagged.cover() is not None), duration=stream.duration, bitrate=stream.bitrate,
            sample_rate=stream.sample_rate, bit_depth=stream.bits_per_sample, channels=stream.channels,
            lyrics_key=cache_key(track.artist, track.title) if track.artist and track.title else None,
        )
        for key in RG_KEYS:
            row["rg_" + key.removeprefix("replaygain_")] = tagged.get_replaygain(key)
        return row, track.embedded
    except Exception as e:  # noqa: BLE001 - a broken file is indexed with its error, the scan goes on
        row["read_error"] = f"{type(e).__name__}: {e}"
        return row, None


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


class Scanner:
    """Scans roots and folders into the index. One scan at a time; `request_folder` may be called from any
    thread to serve a folder (the one on screen) ahead of the walk."""

    def __init__(self, writer: DbWriter, *, workers: int = 4, batch_rows: int = 500, batch_seconds: float = 0.2,
                 tag_batch: int = 200, on_progress: Callable[[ScanProgress], None] | None = None,
                 on_folder: Callable[[int], None] | None = None,
                 now: Callable[[], datetime] = datetime.now) -> None:
        self.writer = writer
        self.workers = workers
        self.batch_rows = batch_rows
        self.batch_seconds = batch_seconds
        self.tag_batch = tag_batch
        self.on_progress = on_progress or (lambda _progress: None)
        # A folder served out of priority (request_folder) is up to date. Scans of their own report through
        # their ScanReport instead.
        self.on_folder = on_folder or (lambda _entry_id: None)
        self.now = now
        self._priority: deque[int] = deque()
        self._lock = threading.Lock()

    def request_folder(self, entry_id: int) -> None:
        with self._lock:
            if entry_id not in self._priority:
                self._priority.append(entry_id)

    # -- public scans --

    def scan_root(self, root_id: int, cancel: CancelToken | None = None) -> ScanReport | None:
        """Scan a whole root. None if the root was removed meanwhile."""
        conn = connect_read(self.writer.path)
        try:
            row = conn.execute(
                "SELECT r.path, e.id FROM roots r JOIN entries e ON e.root_id = r.id AND e.parent_id IS NULL "
                "WHERE r.id = ?", (root_id,)).fetchone()
            if row is None:
                return None
            return self._scan_tree(conn, root_id, row[1], row[0], cancel or CancelToken(), whole_root=True)
        finally:
            conn.close()

    def scan_folder(self, entry_id: int, cancel: CancelToken | None = None, *,
                    recursive: bool = False) -> ScanReport | None:
        """Reconcile one folder: its direct children (F5, revalidation on open), or its whole subtree.
        None if the entry left the index meanwhile (its root removed, or a parent scan deleted it)."""
        cancel = cancel or CancelToken()
        conn = connect_read(self.writer.path)
        try:
            row = conn.execute("SELECT root_id FROM entries WHERE id = ?", (entry_id,)).fetchone()
            if row is None:
                return None
            root_id = row[0]
            if recursive:
                return self._scan_tree(conn, root_id, entry_id, str(path_of(conn, entry_id)), cancel,
                                       whole_root=False)
            report = ScanReport(root_id, folder_id=entry_id)
            with ThreadPoolExecutor(self.workers, thread_name_prefix="tag-reader") as pool:
                ctx = _Context(conn, pool, root_id, self._next_gen(conn, root_id), report,
                               ScanProgress(root_id, "structure"), cancel)
                if not self._serve_folder(ctx, entry_id):
                    report.offline = not os.path.isdir(ctx.path_of(entry_id))
                    return report
            if not cancel.cancelled:
                self._finish(root_id, last_scan=None)
            report.cancelled = cancel.cancelled
            return report
        finally:
            conn.close()

    def scan_path(self, path: str | Path, cancel: CancelToken | None = None, *,
                  recursive: bool = False) -> ScanReport | None:
        """Bring a path's folder up to date (the watcher's entry point). A path that isn't indexed yet (a new
        folder) is found by scanning the subtree of its nearest indexed ancestor. None if outside every root."""
        conn = connect_read(self.writer.path)
        try:
            found = find_folder(conn, path)
        finally:
            conn.close()
        if found is None:
            return None
        entry_id, exact = found
        return self.scan_folder(entry_id, cancel, recursive=recursive or not exact)

    @staticmethod
    def _next_gen(conn: sqlite3.Connection, root_id: int) -> int:
        return (conn.execute("SELECT max(scan_gen) FROM entries WHERE root_id = ?", (root_id,)).fetchone()[0]
                or 0) + 1

    def _scan_tree(self, conn: sqlite3.Connection, root_id: int, top: int, top_path: str, cancel: CancelToken,
                   *, whole_root: bool) -> ScanReport:
        """Phase A from `top` down, then phase B for the root's stale files, then the root's totals."""
        report = ScanReport(root_id, folder_id=top)
        if not os.path.isdir(top_path):
            report.offline = True
            return report
        progress = ScanProgress(root_id, "structure")
        with ThreadPoolExecutor(self.workers, thread_name_prefix="tag-reader") as pool:
            ctx = _Context(conn, pool, root_id, self._next_gen(conn, root_id), report, progress, cancel)
            ctx.paths[top] = top_path
            self._walk(ctx, _Ref(top), top_path)
            if not cancel.cancelled:
                progress.phase = "tags"
                self._read_tags(ctx, "e.root_id = ?", (root_id,))
        if cancel.cancelled:
            report.cancelled = True
            return report
        progress.phase = "stats"
        self.on_progress(progress)
        self._finish(root_id, last_scan=self.now().isoformat(timespec="seconds") if whole_root else None)
        progress.phase = "done"
        self.on_progress(progress)
        return report

    # -- phase A --

    def _walk(self, ctx: "_Context", top: _Ref, root_path: str) -> None:
        queue: deque[tuple[_Ref, str]] = deque([(top, root_path)])
        batch = _Batch()
        while queue and not ctx.cancel.cancelled:
            self._serve_priority(ctx, batch)
            ref, path = queue[0]
            if ref.id is None:  # inserted by the pending batch
                self._flush(ctx, batch)
                batch.clear()
            queue.popleft()
            ctx.paths[ref.id] = path
            for child_ref, child_path in self._reconcile(ctx, ref, path, batch):
                queue.append((child_ref, child_path))
            if batch.rows >= self.batch_rows or time.monotonic() - batch.started >= self.batch_seconds:
                self._flush(ctx, batch)
                batch.clear()
                self.on_progress(ctx.progress)
        self._flush(ctx, batch)

    def _reconcile(self, ctx: "_Context", ref: _Ref, path: str, batch: _Batch) -> list[tuple[_Ref, str]]:
        """Queue the changes for one folder; return its subfolders to walk."""
        try:
            listed = list_folder(path)
        except OSError as e:
            log.warning("can't list %s: %s (its index is kept)", path, e)
            ctx.report.unreadable_folders += 1
            return []
        ctx.report.folders += 1
        ctx.progress.folders += 1
        # Also for a folder inserted moments ago: a priority request may have filled it already.
        known = {row[1].casefold(): row for row in ctx.conn.execute(
            "SELECT id, name, is_dir, size, mtime FROM entries WHERE parent_id = ?", (ref.id,))}
        subfolders = []
        for child in listed:
            if not child.is_dir:
                ctx.report.files += 1
                ctx.progress.files += 1
            row = known.pop(child.name.casefold(), None)
            if row is not None and bool(row[2]) != child.is_dir:
                batch.deletes.append(row[0])  # a file became a folder, or the reverse
                ctx.report.removed += 1
                row = None
            if row is None:
                new_ref = _Ref() if child.is_dir else None
                batch.inserts.append((ref, child, new_ref))
                ctx.report.added += 1
                if new_ref is not None:
                    subfolders.append((new_ref, os.path.join(path, child.name)))
                continue
            if row[1] != child.name or (not child.is_dir and (row[3] != child.size or row[4] != child.mtime)):
                batch.updates.append((child.name, child.size, child.mtime, ctx.gen, row[0]))
                ctx.report.updated += 1
            if child.is_dir:
                subfolders.append((_Ref(row[0]), os.path.join(path, child.name)))
        for row in known.values():
            batch.deletes.append(row[0])
            ctx.report.removed += 1
        return subfolders

    def _flush(self, ctx: "_Context", batch: _Batch) -> None:
        if not batch.rows:
            return
        root_id, gen = ctx.root_id, ctx.gen
        inserts = [(parent.id, child) for parent, child, _ref in batch.inserts]

        def write(conn: sqlite3.Connection) -> list[int]:
            for entry_id in batch.deletes:
                conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
            conn.executemany("UPDATE entries SET name = ?, size = ?, mtime = ?, scan_gen = ? WHERE id = ?",
                             batch.updates)
            conn.executemany("UPDATE search SET name = ? WHERE rowid = ?", [(u[0], u[4]) for u in batch.updates])
            ids = []
            for parent_id, child in inserts:
                entry_id = conn.execute(
                    "INSERT INTO entries (root_id, parent_id, name, is_dir, ext, size, mtime, scan_gen) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (root_id, parent_id, child.name, int(child.is_dir), child.ext, child.size, child.mtime, gen),
                ).lastrowid
                conn.execute("INSERT INTO search (rowid, name) VALUES (?, ?)", (entry_id, child.name))
                ids.append(entry_id)
            return ids

        ids = self.writer.submit(write).result()
        for (_parent, _child, ref), entry_id in zip(batch.inserts, ids, strict=True):
            if ref is not None:
                ref.id = entry_id

    # -- priority folders --

    def _serve_priority(self, ctx: "_Context", batch: _Batch) -> None:
        while not ctx.cancel.cancelled:
            with self._lock:
                if not self._priority:
                    return
                entry_id = self._priority.popleft()
            self._flush(ctx, batch)  # the folder may be in the pending batch
            batch.clear()
            self._serve_folder(ctx, entry_id, notify=True)

    def _serve_folder(self, ctx: "_Context", entry_id: int, notify: bool = False) -> bool:
        row = ctx.conn.execute("SELECT is_dir FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if row is None or not row[0]:
            return False
        path = ctx.path_of(entry_id)
        batch = _Batch()
        before = ctx.report.unreadable_folders
        self._reconcile(ctx, _Ref(entry_id), path, batch)
        if ctx.report.unreadable_folders > before:
            return False
        self._flush(ctx, batch)
        self._read_tags(ctx, "e.parent_id = ?", (entry_id,))
        if notify:
            self.on_folder(entry_id)
        return True

    # -- phase B --

    def _read_tags(self, ctx: "_Context", where: str, params: tuple) -> None:
        stale = ctx.conn.execute(
            f"""
            SELECT e.id, e.parent_id, e.name, e.size, e.mtime FROM entries e
            LEFT JOIN tracks t ON t.entry_id = e.id
            WHERE {where} AND e.is_dir = 0 AND e.ext IN ({_AUDIO_SQL})
              AND (t.entry_id IS NULL OR t.read_size IS NOT e.size OR t.read_mtime IS NOT e.mtime)
            """, params).fetchall()
        ctx.progress.tags_total += len(stale)
        for start in range(0, len(stale), self.tag_batch):
            if ctx.cancel.cancelled:
                return
            if where.startswith("e.root_id"):
                self._serve_priority(ctx, _Batch())
            chunk = stale[start:start + self.tag_batch]
            paths = [Path(ctx.path_of(parent_id), name) for _id, parent_id, name, _s, _m in chunk]
            results = list(ctx.pool.map(read_file, paths, [r[3] for r in chunk], [r[4] for r in chunk]))
            self.writer.submit(lambda conn, c=chunk, r=results: _write_tracks(conn, c, r)).result()
            ctx.report.tags_read += len(chunk)
            ctx.report.tag_errors += sum(1 for row, _lyrics in results if row["read_error"])
            ctx.progress.tags_done += len(chunk)
            self.on_progress(ctx.progress)

    # -- after the scan --

    def _finish(self, root_id: int, last_scan: str | None) -> None:
        def write(conn: sqlite3.Connection) -> None:
            _update_sidecars(conn, root_id)
            _update_folder_stats(conn, root_id)
            if last_scan:
                conn.execute("UPDATE roots SET last_scan = ? WHERE id = ?", (last_scan, root_id))
        self.writer.submit(write).result()


class _Context:
    """The state of one scan."""

    def __init__(self, conn, pool, root_id, gen, report, progress, cancel) -> None:
        self.conn: sqlite3.Connection = conn
        self.pool: ThreadPoolExecutor = pool
        self.root_id: int = root_id
        self.gen: int = gen
        self.report: ScanReport = report
        self.progress: ScanProgress = progress
        self.cancel: CancelToken = cancel
        self.paths: dict[int, str] = {}  # folder id -> path, filled by the walk

    def path_of(self, entry_id: int) -> str:
        path = self.paths.get(entry_id)
        if path is None:
            path = self.paths[entry_id] = str(path_of(self.conn, entry_id))
        return path


def _write_tracks(conn: sqlite3.Connection, chunk: list, results: list) -> None:
    columns = ", ".join(TRACK_COLUMNS)
    marks = ", ".join("?" * (len(TRACK_COLUMNS) + 1))
    for (entry_id, _parent, name, _size, _mtime), (row, lyrics) in zip(chunk, results, strict=True):
        if conn.execute("SELECT 1 FROM entries WHERE id = ?", (entry_id,)).fetchone() is None:
            continue  # gone since it was listed
        conn.execute(f"INSERT OR REPLACE INTO tracks (entry_id, {columns}) VALUES ({marks})",
                     (entry_id, *(row[c] for c in TRACK_COLUMNS)))
        conn.execute("DELETE FROM search WHERE rowid = ?", (entry_id,))
        conn.execute("INSERT INTO search (rowid, name, title, artist, albumartist, album, genre, lyrics) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (entry_id, name, row["title"], row["artist"], row["albumartist"], row["album"], row["genre"],
                      lyrics))


def _update_sidecars(conn: sqlite3.Connection, root_id: int) -> None:
    """has_lrc_sidecar: a same-stem .lrc next to the audio file (it can appear without the audio changing)."""
    conn.execute(
        """
        UPDATE tracks SET has_lrc_sidecar = EXISTS (
            SELECT 1 FROM entries s
            WHERE s.parent_id = e.parent_id AND s.ext = '.lrc'
              AND lower(substr(s.name, 1, length(s.name) - 4))
                = lower(substr(e.name, 1, length(e.name) - length(e.ext)))
        )
        FROM entries e WHERE e.id = tracks.entry_id AND e.root_id = ?
        """, (root_id,))


def _update_folder_stats(conn: sqlite3.Connection, root_id: int) -> None:
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
