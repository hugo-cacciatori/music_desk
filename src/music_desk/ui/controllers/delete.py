"""Delete's controller (SPEC §19, §16): the selected files and folders go to the Recycle Bin, and nowhere else.

The check (a library root, a lock, a place where Windows can't recycle) comes first and touches nothing; the
recycling runs on the window's shell thread, one item at a time, so a failure names its file and the others still
go. The lock taken at the check is held until the report.
"""

import dataclasses
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from music_desk.core import fsops
from music_desk.core.index import queries
from music_desk.core.index.store import path_of
from music_desk.core.path_locks import LockedError
from music_desk.ui.controllers import STATUS_MS

DELETE_OWNER = "a delete"  # its PathLocks owner, as other jobs see it


@dataclasses.dataclass(frozen=True)
class DeleteItem:
    entry_id: int
    path: Path
    is_dir: bool


@dataclasses.dataclass
class DeleteRequest:
    """One Delete, from the check to the report (SPEC §19). Its lock is held until the end."""

    items: list[DeleteItem]
    lock: int
    view: QWidget | None  # where Del was pressed, for the selection afterwards
    next_id: int | None  # the row to select afterwards


class DeleteController(QObject):
    checked = Signal(object, list)  # the DeleteRequest, [(path, why it can't be recycled)] (shell thread)
    done = Signal(object, list, list)  # the DeleteRequest, the sent items, [(name, error)] (shell thread)

    def __init__(self, window, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.window = window
        # Tests replace both, so they never use the real Recycle Bin.
        self.recycle = fsops.send_to_recycle_bin
        self.recycle_check = fsops.can_recycle
        self.checked.connect(self._checked)
        self.done.connect(self._done)

    def delete_entries(self, ids: list[int], view: QWidget | None = None) -> None:
        """Del, Shift+Del or Delete in a menu: the items go to the Recycle Bin after a confirmation, and nowhere
        else. Refused, with nothing touched: a library root, an item a job or a save holds, and a place where
        Windows can't recycle (checked off the UI thread, before asking)."""
        window = self.window
        if not window._library_open() or not ids:
            return
        conn = window.open_library.conn
        items: list[DeleteItem] = []
        for entry_id in dict.fromkeys(ids):
            row = conn.execute("SELECT is_dir FROM entries WHERE id = ?", (entry_id,)).fetchone()
            if row is None:
                continue
            path = path_of(conn, entry_id)
            if queries.parent_folder(conn, entry_id) is None:
                window.toast.show_message(f"{path} is a library folder. Use Remove from library to take it out of "
                                          "the library.")
                return
            items.append(DeleteItem(entry_id, path, bool(row[0])))
        # An item inside a selected folder goes with it (the tree can select a folder and its subfolder).
        items = [i for i in items if not any(o.is_dir and o is not i and i.path.is_relative_to(o.path) for o in items)]
        if not items:
            return
        try:
            lock = window.open_library.locks.acquire([item.path for item in items], DELETE_OWNER)
        except LockedError as e:
            window.toast.show_message(f"{Path(e.path).name} is in use by {e.owner}. Try again when it has finished.")
            return
        request = DeleteRequest(items, lock, view, self._row_after(ids, view))
        check = self.recycle_check

        def run() -> None:
            reasons = []
            for item in items:
                try:
                    reason = check(item.path)
                except OSError as e:
                    reason = f"Windows couldn't check {item.path.name} ({e})"
                if reason is not None:
                    reasons.append((str(item.path), reason))
            self.checked.emit(request, reasons)

        window._shell.submit(run)

    def _row_after(self, ids: list[int], view: QWidget | None) -> int | None:
        """The row to select once these are gone: the first one after them, else the last one before (Explorer)."""
        window = self.window
        if view is window.file_view.view:
            shown = window.file_view.visible_ids()
        elif view is window.search_view.view:
            shown = window.search_view.visible_ids()
        else:
            return None  # the tree: the open folder's own rule applies (SPEC §5)
        chosen = set(ids)
        places = [n for n, entry_id in enumerate(shown) if entry_id in chosen]
        if not places:
            return None
        after = [i for i in shown[places[-1] + 1:] if i not in chosen]
        before = [i for i in shown[:places[0]] if i not in chosen]
        return after[0] if after else before[-1] if before else None

    def _checked(self, request: DeleteRequest, reasons: list[tuple[str, str]]) -> None:
        window = self.window
        if not window._library_open():
            return
        if reasons:
            window.open_library.locks.release(request.lock)
            path, reason = reasons[0]
            is_dir = any(str(item.path) == path and item.is_dir for item in request.items)
            window.toast.show_message(
                f"MusicDesk only deletes to the Recycle Bin, and {reason}, so nothing was deleted. You can delete it "
                "in File Explorer.", "Open in File Explorer",
                lambda: window._shell.submit(lambda: window.show_in_explorer(path, is_dir)))
            return
        # No question (Hugo, 2026-10-17): the Recycle Bin is the undo. Unsaved tag edits on a deleted file go with it.
        if self._unsaved_edits_in(request.items):
            window.side_panel.metadata.discard_edits()
        recycle = self.recycle

        def run() -> None:
            sent, failed = [], []
            for item in request.items:  # one at a time, so a failure names its file and the others still go
                try:
                    recycle(item.path)
                except OSError as e:  # CannotRecycle too: never deleted for good instead
                    failed.append((item.path.name, e.strerror or str(e)))
                else:
                    sent.append(item)
            self.done.emit(request, sent, failed)

        window._shell.submit(run)

    def _unsaved_edits_in(self, items: list[DeleteItem]) -> bool:
        metadata = self.window.side_panel.metadata
        if not metadata.dirty:
            return False
        edited = [metadata.path] if metadata.path is not None else metadata.paths
        return any(path == item.path or (item.is_dir and path.is_relative_to(item.path))
                   for path in edited for item in items)

    def _done(self, request: DeleteRequest, sent: list[DeleteItem], failed: list[tuple[str, str]]) -> None:
        window = self.window
        if not window._library_open():
            return
        window.open_library.locks.release(request.lock)
        for parent in dict.fromkeys(item.path.parent for item in sent):
            window.open_library.indexer.request_path(str(parent))  # at once; the watcher would see it too, but later
        if sent:
            what = f"'{sent[0].path.name}'" if len(sent) == 1 else f"{len(sent)} items"
            window.statusBar().showMessage(f"Sent {what} to the Recycle Bin.", STATUS_MS)
        if failed:
            shown = "; ".join(f"{name}: {why}" for name, why in failed[:5])
            more = f" (and {len(failed) - 5} more)" if len(failed) > 5 else ""
            window.toast.show_message(f"Not sent to the Recycle Bin: {shown}{more}", timeout_ms=15_000)
        if request.next_id is not None and sent:
            if request.view is window.file_view.view and not window.showing_search:
                window.file_view.select_ids([request.next_id])
            elif request.view is window.search_view.view and window.showing_search:
                window.search_view.select_ids([request.next_id])
