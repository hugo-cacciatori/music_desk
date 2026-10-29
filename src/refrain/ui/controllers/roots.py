"""The library's folders (SPEC §3): Add folder… (the sidebar's button, Ctrl+O, a drop on the window), Remove from
library, and what an add found. Nothing on disk changes: only the index."""

import os

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QFileDialog, QMessageBox

from refrain.core.index.roots import AddResult, AddStatus, Root
from refrain.ui.controllers import STATUS_MS


class RootsController(QObject):
    def __init__(self, window, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.window = window

    def choose_folder(self) -> str:
        """Replaced in tests."""
        return QFileDialog.getExistingDirectory(self.window, "Add a music folder to the library")

    def add_folder(self) -> None:
        if self.window.library is None:
            return
        folder = self.choose_folder()
        if folder:
            self.window.library.add_folders([folder])

    def confirm_remove(self, root: Root) -> bool:
        """Replaced in tests."""
        answer = QMessageBox.question(
            self.window, "Remove from library",
            f"Remove {root.path} from the library?\n\nNothing on disk changes: only the app's index of this folder "
            "is deleted. Adding it again rescans it.")
        return answer == QMessageBox.StandardButton.Yes

    def remove_root(self, root: Root) -> None:
        window = self.window
        if window.library is not None and self.confirm_remove(root):
            window.library.remove_root(root.id)
            window.statusBar().showMessage(f"Removed {root.path} from the library. Nothing on disk changed.",
                                           STATUS_MS)

    def show_add_results(self, results: list[AddResult]) -> None:
        window = self.window
        messages, files = [], []
        for result in results:
            match result.status:
                case AddStatus.ADDED:
                    messages.append(f"Added {result.path} to the library.")
                case AddStatus.MERGED:
                    count = len(result.merged)
                    messages.append(f"Added {result.path} to the library. {count} library folder"
                                    f"{'s' if count > 1 else ''} inside it became its subfolders.")
                case AddStatus.ALREADY_ROOT | AddStatus.INSIDE_ROOT:
                    messages.append(f"{result.path} is already in the library.")
                case AddStatus.IS_A_FILE:
                    files.append(result.path)
                case AddStatus.NOT_A_FOLDER:
                    messages.append(f"{result.path} can't be added: the folder doesn't exist or can't be reached.")
        if messages:
            window.statusBar().showMessage(" ".join(messages), STATUS_MS)
        last = next((r for r in reversed(results) if r.root_id is not None), None)
        if last is not None and last.root_id is not None:
            window.sidebar.select_root(last.root_id)
        if files:
            folders = list(dict.fromkeys(os.path.dirname(path) for path in files))
            what = "its folder" if len(folders) == 1 else "their folders"
            window.toast.show_message(
                "Only folders can be added to the library, not files.", f"Add {what}",
                lambda: window.open_library.add_folders(folders))
