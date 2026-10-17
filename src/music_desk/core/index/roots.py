"""Library roots (SPEC §3): add, merge, remove and the online check. Index-only: nothing on disk changes.

These functions block on the file system and on the writer, so the app calls them off the UI thread.
Roots never nest: a folder inside a root is already in the library, and adding a parent of existing roots
merges them under it, keeping their indexed entries (their top entries become ordinary folders).
"""

import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from music_desk.core.index.store import DbWriter


class AddStatus(Enum):
    ADDED = "added"
    MERGED = "merged"  # added, and existing roots inside it became its subfolders
    ALREADY_ROOT = "already_root"
    INSIDE_ROOT = "inside_root"  # already in the library under an existing root: the app navigates there
    IS_A_FILE = "is_a_file"  # only folders can be roots: the app offers the file's folder instead
    NOT_A_FOLDER = "not_a_folder"  # missing or unreachable


@dataclass(frozen=True)
class AddResult:
    status: AddStatus
    path: str
    root_id: int | None = None
    merged: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class Root:
    id: int
    path: str
    top_entry_id: int
    online: bool
    added_at: str
    last_scan: str | None


def normalize(path: str | Path) -> str:
    """Absolute, normalized, no trailing separator (except a drive root such as 'C:\\')."""
    return os.path.normpath(os.path.abspath(path))


def _key(path: str) -> str:
    return os.path.normcase(path)


def is_inside(child: str, parent: str) -> bool:
    """True if child is strictly inside parent (ignoring case, as Windows does)."""
    parent_key = _key(parent).rstrip("\\/") + os.sep
    return _key(child).startswith(parent_key)


def list_roots(conn: sqlite3.Connection) -> list[Root]:
    rows = conn.execute(
        """
        SELECT r.id, r.path, e.id, r.online, r.added_at, r.last_scan
        FROM roots r JOIN entries e ON e.root_id = r.id AND e.parent_id IS NULL
        """
    ).fetchall()
    roots = [Root(row[0], row[1], row[2], bool(row[3]), row[4], row[5]) for row in rows]
    return sorted(roots, key=lambda r: _key(r.path))


def add_root(writer: DbWriter, path: str | Path, *, now: datetime | None = None) -> AddResult:
    """Add a folder to the library: only its root row and top entry (the scanner fills the rest)."""
    path = normalize(path)
    if os.path.isfile(path):
        return AddResult(AddStatus.IS_A_FILE, path)
    if not os.path.isdir(path):
        return AddResult(AddStatus.NOT_A_FOLDER, path)
    added_at = (now or datetime.now()).isoformat(timespec="seconds")
    return writer.submit(lambda conn: _add(conn, path, added_at)).result()


def _add(conn: sqlite3.Connection, path: str, added_at: str) -> AddResult:
    roots = list_roots(conn)
    for root in roots:
        if _key(root.path) == _key(path):
            return AddResult(AddStatus.ALREADY_ROOT, root.path, root.id)
        if is_inside(path, root.path):
            return AddResult(AddStatus.INSIDE_ROOT, path, root.id)

    root_id = conn.execute("INSERT INTO roots (path, added_at, online) VALUES (?, ?, 1)", (path, added_at)).lastrowid
    top = conn.execute("INSERT INTO entries (root_id, parent_id, name, is_dir) VALUES (?, NULL, ?, 1)",
                       (root_id, path)).lastrowid
    assert root_id is not None and top is not None  # an INSERT always sets lastrowid
    children = [root for root in roots if is_inside(root.path, path)]
    for child in children:
        _merge(conn, root_id, top, path, child)
    if children:
        return AddResult(AddStatus.MERGED, path, root_id, tuple(child.path for child in children))
    return AddResult(AddStatus.ADDED, path, root_id)


def _merge(conn: sqlite3.Connection, root_id: int, top: int, path: str, child: Root) -> None:
    """Hang a former root's tree under the new root, creating the folders in between."""
    parts = Path(child.path).relative_to(path).parts  # Windows paths compare ignoring case
    parent = top
    for name in parts[:-1]:
        row = conn.execute("SELECT id FROM entries WHERE parent_id = ? AND name = ? COLLATE NOCASE",
                           (parent, name)).fetchone()
        parent = row[0] if row else conn.execute(
            "INSERT INTO entries (root_id, parent_id, name, is_dir) VALUES (?, ?, ?, 1)",
            (root_id, parent, name)).lastrowid
    # The old top entry becomes a plain folder first (one top entry per root), then the whole tree changes root
    # before the old root row goes, since deleting it cascades to whatever still points at it.
    conn.execute("UPDATE entries SET parent_id = ?, name = ? WHERE id = ?", (parent, parts[-1], child.top_entry_id))
    conn.execute("UPDATE entries SET root_id = ? WHERE root_id = ?", (root_id, child.id))
    conn.execute("DELETE FROM roots WHERE id = ?", (child.id,))


def remove_root(writer: DbWriter, root_id: int) -> bool:
    """Forget a root and everything indexed under it. Returns False if it was already gone."""
    return writer.submit(lambda conn: conn.execute("DELETE FROM roots WHERE id = ?", (root_id,)).rowcount > 0).result()


def check_online(writer: DbWriter, roots: list[Root]) -> list[Root]:
    """Probe each root's folder and record which are reachable. Returns the roots whose state changed (with
    their new state). An offline root keeps its index: it stays browsable, read-only."""
    changed = [Root(r.id, r.path, r.top_entry_id, online, r.added_at, r.last_scan)
               for r in roots if (online := os.path.isdir(r.path)) != r.online]
    if changed:
        writer.executemany("UPDATE roots SET online = ? WHERE id = ?",
                           [(int(r.online), r.id) for r in changed]).result()
    return changed
