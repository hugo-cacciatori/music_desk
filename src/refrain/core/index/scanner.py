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

from refrain.core.cancel import CancelToken
from refrain.core.formats import AUDIO_EXTENSIONS
from refrain.core.index.queries import find_folder
from refrain.core.index.scan_read import Child, list_folder, read_file
from refrain.core.index.scan_write import (
    adjust_folder_stats,
    match_renames,
    stats_values,
    update_folder_stats,
    update_sidecars,
    update_sidecars_in_folder,
    write_tracks,
)
from refrain.core.index.store import DbWriter, connect_read, path_of

log = logging.getLogger(__name__)

PAUSE_POLL_S = 0.1  # how often a paused scan looks for folders the user asks for
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
        self.inserts: list[tuple[_Ref, Child, _Ref | None]] = []  # (parent, child, ref for a new folder)
        self.started = time.monotonic()

    @property
    def rows(self) -> int:
        return len(self.deletes) + len(self.updates) + len(self.inserts)


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
        self._pauses = 0  # pause() calls not yet resumed: a long scan waits, serving only what the user asks for

    def pause(self) -> None:
        """Hold long scans (a whole root, a subtree) at their next checkpoint, while a ReplayGain job needs the
        drive: on a hard disk, the two reading at once slowed rsgain several times over. Folders the user asks
        for are still served meanwhile. Calls nest; each needs its resume()."""
        with self._lock:
            self._pauses += 1

    def resume(self) -> None:
        with self._lock:
            self._pauses = max(0, self._pauses - 1)

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._pauses > 0

    def _hold_while_paused(self, ctx: "_Context", batch: "_Batch") -> None:
        while self.paused and not ctx.cancel.cancelled:
            self._serve_priority(ctx, batch)
            time.sleep(PAUSE_POLL_S)

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

    def update_file(self, path: str | Path) -> ScanReport | None:
        """Re-read one file the app just changed (a tag save) into the index: its entry, tracks and search rows,
        and its folders' totals adjusted by the difference, without rescanning anything else. A file the index
        doesn't know yet makes its folder be scanned instead. None if outside every root."""
        path = Path(path)
        conn = connect_read(self.writer.path)
        try:
            found = find_folder(conn, path.parent)
            if found is None:
                return None
            folder_id, exact = found
            row = conn.execute("SELECT id, root_id FROM entries WHERE parent_id = ? AND name = ? COLLATE NOCASE",
                               (folder_id, path.name)).fetchone() if exact else None
        finally:
            conn.close()
        if row is None:
            return self.scan_folder(folder_id, recursive=not exact)
        entry_id, root_id = row
        try:
            stat = path.stat()
        except OSError:
            return self.scan_folder(folder_id)  # it went away meanwhile
        track, lyrics = read_file(path, stat.st_size, stat.st_mtime)
        entry = (entry_id, folder_id, path.name, stat.st_size, stat.st_mtime)

        def write(conn: sqlite3.Connection) -> None:
            before = stats_values(conn, entry_id)
            conn.execute("UPDATE entries SET name = ?, size = ?, mtime = ? WHERE id = ?",
                         (path.name, stat.st_size, stat.st_mtime, entry_id))
            write_tracks(conn, [entry], [(track, lyrics)])
            adjust_folder_stats(conn, folder_id, before, stats_values(conn, entry_id))

        self.writer.submit(write).result()
        report = ScanReport(root_id, files=1, updated=1, tags_read=1, folder_id=folder_id)
        report.tag_errors = int(track["read_error"] is not None)
        return report

    def apply_renames(self, steps: list[tuple[Path, Path]]) -> ScanReport | None:
        """Renames the app just made (inline rename, Undo): update the entries in place, so their ids stay (the
        selection with them) and the subtree of a renamed folder follows. A file whose audio-ness changed with
        its extension is re-read. Entries the index doesn't know make their folder be scanned instead."""
        if not steps:
            return None
        conn = connect_read(self.writer.path)
        try:
            found = find_folder(conn, steps[0][0].parent)
            if found is None:
                return None
            folder_id, exact = found
            rows = []
            for old, new in steps:
                row = conn.execute(
                    "SELECT e.id, e.root_id, e.is_dir, t.entry_id IS NOT NULL FROM entries e "
                    "LEFT JOIN tracks t ON t.entry_id = e.id WHERE e.parent_id = ? AND e.name = ? COLLATE NOCASE",
                    (folder_id, old.name)).fetchone() if exact else None
                if row is None:
                    break
                rows.append((row, new))
        finally:
            conn.close()
        if len(rows) != len(steps):
            return self.scan_folder(folder_id, recursive=not exact)

        changed_kind = [(entry_id, has_track, new) for (entry_id, _root, is_dir, has_track), new in rows
                         if not is_dir and has_track != (new.suffix.lower() in AUDIO_EXTENSIONS)]

        def write(conn: sqlite3.Connection) -> None:
            for (entry_id, _root, is_dir, _has_track), new in rows:
                ext = None if is_dir else new.suffix.lower()
                conn.execute("UPDATE entries SET name = ?, ext = ? WHERE id = ?", (new.name, ext, entry_id))
                conn.execute("UPDATE search SET name = ? WHERE rowid = ?", (new.name, entry_id))
            for entry_id, had_track, new in changed_kind:
                if had_track:  # "song.flac" renamed "song.txt": no longer a track
                    conn.execute("DELETE FROM tracks WHERE entry_id = ?", (entry_id,))
                    conn.execute("DELETE FROM search WHERE rowid = ?", (entry_id,))
                    conn.execute("INSERT INTO search (rowid, name) VALUES (?, ?)", (entry_id, new.name))
            update_sidecars_in_folder(conn, folder_id)

        self.writer.submit(write).result()
        root_id = rows[0][0][1]
        if changed_kind:
            for _entry_id, had_track, new in changed_kind:
                if not had_track:  # "song.txt" renamed "song.flac": read its tags
                    self.update_file(new)
            self._finish(root_id, last_scan=None)  # the totals changed with the kind of file
        return ScanReport(root_id, updated=len(rows), folder_id=folder_id)

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
            self._hold_while_paused(ctx, batch)
            ref, path = queue[0]
            if ref.id is None:  # inserted by the pending batch
                self._flush(ctx, batch)
                batch.clear()
            queue.popleft()
            assert ref.id is not None  # the flush gave it its id
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
        new_children = []
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
                new_children.append(child)
                continue
            if row[1] != child.name or (not child.is_dir and (row[3] != child.size or row[4] != child.mtime)):
                batch.updates.append((child.name, child.size, child.mtime, ctx.gen, row[0]))
                ctx.report.updated += 1
            if child.is_dir:
                subfolders.append((_Ref(row[0]), os.path.join(path, child.name)))

        # A vanished entry and a new one alike in kind, extension, size and mtime (one of each) were renamed
        # outside the app, or by the app just before its own index update: keep the row, its id and its tags.
        renamed = match_renames(list(known.values()), new_children)
        for row, child in renamed:
            del known[row[1].casefold()]
            new_children.remove(child)
            batch.updates.append((child.name, child.size, child.mtime, ctx.gen, row[0]))
            ctx.report.updated += 1
            if child.is_dir:
                subfolders.append((_Ref(row[0]), os.path.join(path, child.name)))
        for child in new_children:
            new_ref = _Ref() if child.is_dir else None
            batch.inserts.append((ref, child, new_ref))
            ctx.report.added += 1
            if new_ref is not None:
                subfolders.append((new_ref, os.path.join(path, child.name)))
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
                self._hold_while_paused(ctx, _Batch())
            chunk = stale[start:start + self.tag_batch]
            paths = [Path(ctx.path_of(parent_id), name) for _id, parent_id, name, _s, _m in chunk]
            results = list(ctx.pool.map(read_file, paths, [r[3] for r in chunk], [r[4] for r in chunk]))
            self.writer.submit(lambda conn, c=chunk, r=results: write_tracks(conn, c, r)).result()
            ctx.report.tags_read += len(chunk)
            ctx.report.tag_errors += sum(1 for row, _lyrics in results if row["read_error"])
            ctx.progress.tags_done += len(chunk)
            self.on_progress(ctx.progress)

    # -- after the scan --

    def _finish(self, root_id: int, last_scan: str | None) -> None:
        def write(conn: sqlite3.Connection) -> None:
            update_sidecars(conn, root_id)
            update_folder_stats(conn, root_id)
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
