"""Inline rename's controller (SPEC §11): F2 in the file list or the tree, and the Metadata tab's rename hint. The
rename runs on the window's shell thread; the index then follows in place, and a toast offers Undo."""

import os
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox

from refrain.core import inline_rename
from refrain.core.index import queries
from refrain.core.index.queries import find_folder
from refrain.core.index.store import path_of
from refrain.ui.controllers import STATUS_MS


class RenameController(QObject):
    done = Signal(object, bool)  # RenameResult, whether it was an Undo (shell thread)
    failed = Signal(str)

    def __init__(self, window, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.window = window
        self.done.connect(self._renamed)
        self.failed.connect(lambda message: window.toast.show_message(message))

    def confirm_extension_change(self, old: str, new: str) -> bool:
        answer = QMessageBox.question(
            self.window, "Change the extension?",
            f"Change the extension of {old} to {Path(new).suffix or '(none)'}?\n\nThe file may stop opening in its "
            "usual app, or stop counting as audio.")
        return answer == QMessageBox.StandardButton.Yes

    def rename_path(self, path: str, new_name: str) -> None:
        """A rename asked by path (the hint after a save)."""
        window = self.window
        if not window._library_open():
            return
        conn = window.open_library.conn
        found = find_folder(conn, os.path.dirname(path))
        entry_id = queries.find_child(conn, found[0], os.path.basename(path)) if found and found[1] else None
        if entry_id is not None:
            self.rename_entry(entry_id, new_name)

    def rename_entry(self, entry_id: int, new_name: str) -> None:
        """Rename a file or folder on disk (off the UI thread), then update the index in place."""
        window = self.window
        if not window._library_open() or not window._folder_exists(entry_id):
            return
        conn = window.open_library.conn
        if queries.parent_folder(conn, entry_id) is None:
            window.toast.show_message("A library folder can't be renamed here. Rename it in File Explorer, then "
                                      "add it again.")
            return
        path = path_of(conn, entry_id)
        if new_name == path.name:
            return
        is_dir = bool(conn.execute("SELECT is_dir FROM entries WHERE id = ?", (entry_id,)).fetchone()[0])
        if not is_dir and Path(new_name).suffix.lower() != path.suffix.lower() and \
                not self.confirm_extension_change(path.name, new_name):
            return
        metadata = window.side_panel.metadata
        edited = metadata.path == path or path in metadata.paths
        if (edited or is_dir) and not window.side_panel.can_leave():
            return
        locks = window.open_library.locks

        def run() -> None:
            try:
                result = inline_rename.rename_entry(path, new_name, locks)
            except inline_rename.RenameError as e:
                self.failed.emit(str(e))
                return
            self.done.emit(result, False)

        window.shell.submit(run)

    def _renamed(self, result: inline_rename.RenameResult, was_undo: bool) -> None:
        window = self.window
        if not result.steps:
            return
        if window._library_open():
            window.open_library.indexer.apply_renames(result.steps)
        old, new = result.steps[0]
        if was_undo:
            window.statusBar().showMessage(f"Renamed {old.name} back to {new.name}.", STATUS_MS)
            return
        text = f"Renamed {old.name} to {new.name}"
        if result.sidecar:
            text += f", and its lyrics file {result.sidecar[0].name} to {result.sidecar[1].name}"
        text += "."
        if result.warnings:
            text += " " + " ".join(result.warnings)
        locks = window.open_library.locks if window._library_open() else None

        def undo() -> None:
            def run() -> None:
                try:
                    back = inline_rename.undo(result, locks)
                except inline_rename.RenameError as e:
                    self.failed.emit(str(e))
                    return
                self.done.emit(back, True)
            window.shell.submit(run)

        window.toast.show_message(text, "Undo", undo, timeout_ms=10_000)
