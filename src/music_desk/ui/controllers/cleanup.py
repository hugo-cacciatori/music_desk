"""Clean up's controller (SPEC §32): the toolbar's Clean up (or a folder menu's) reads the tree on a worker, then
opens the Redundant files window with what it found. No preview and no question: the window is the check, and the
Recycle Bin is the undo.

The Recycle Bin goes through attributes that tests replace.
"""

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from music_desk.core import cleanup, fsops
from music_desk.core.cleanup import CleanupOptions, Item
from music_desk.ui.controllers import STATUS_MS
from music_desk.ui.recycle_window import RecycleWindow
from music_desk.ui.tools.tool_cards import ToolRequest

log = logging.getLogger(__name__)

OWNER = "Clean up"
INTRO = ("Clean up found these. Every box is checked: uncheck what to keep, then click Delete selected to send the "
         "rest to the Recycle Bin. Closing this window keeps them all. The Clean up options (File > Options…) say "
         "what is looked for.")


@dataclass
class Found:
    token: int
    folder: Path  # several folders: the deepest folder holding them all
    items: list[Item] = field(default_factory=list)
    error: str | None = None
    folders: list[Path] = field(default_factory=list)  # what was read


def where_text(folders: list[Path]) -> str:
    """"Kid A", or "3 folders": what Clean up read, for its messages."""
    if len(folders) == 1:
        return folders[0].name or str(folders[0])
    return f"{len(folders)} folders"


class CleanupFinder(QObject):
    """Reads the tree for Clean up on its own thread (Ground rule 2)."""

    found = Signal(object)  # Found

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = ThreadPoolExecutor(1, thread_name_prefix="cleanup")
        self._token = 0

    def find(self, folder: Path | list[Path], options: CleanupOptions) -> int:
        """Read a folder, or several (the right-click menu, SPEC §31: one list for them all)."""
        self._token += 1
        token = self._token
        folders = folder if isinstance(folder, list) else [folder]
        try:
            common = Path(os.path.commonpath(folders))
        except ValueError:  # on several drives
            common = folders[0]

        def run() -> None:
            result = Found(token, common, folders=folders)
            try:
                seen: set[Path] = set()
                for each in folders:
                    for item in cleanup.find_items(each, options):
                        if item.path not in seen:  # nested folders: each item once
                            seen.add(item.path)
                            result.items.append(item)
            except Exception as e:  # noqa: BLE001 - said in the status bar
                log.exception("Clean up couldn't read %s", folder)
                result.error = f"{type(e).__name__}: {e}"
            self.found.emit(result)

        self._pool.submit(run)
        return token

    def close(self) -> None:
        self._pool.shutdown(wait=True)


class CleanupController(QObject):
    def __init__(self, window, locks, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.window = window
        self.locks = locks
        self.finder = CleanupFinder(self)
        self.finder.found.connect(self._found)
        self.recycle = fsops.send_to_recycle_bin  # the window's Delete selected; replaced in tests (a recorder)
        self.check_recycle = fsops.can_recycle
        self.offer: RecycleWindow | None = None
        self._token: int | None = None

    def open(self, request: ToolRequest) -> None:
        window = self.window
        if self.offer is not None and self.offer.state == "deleting":  # one run at a time: show it
            self.offer.show()
            self.offer.raise_()
            return
        assert request.cleanup_options is not None  # set for every Clean up request
        folder = request.scope.folder
        self._token = self.finder.find(folder, request.cleanup_options)
        window.statusBar().showMessage(f"Looking for files to clean up in {folder.name or folder}…", STATUS_MS)

    def _found(self, found: Found) -> None:
        if found.token != self._token:
            return
        self._token = None
        window = self.window
        name = where_text(found.folders) if found.folders else found.folder.name or str(found.folder)
        if found.error is not None:
            text = f"Clean up couldn't read {found.folder}: {found.error}"
            window.statusBar().showMessage(text, STATUS_MS)
            return
        if not any(item.kept is None for item in found.items):
            window.statusBar().showMessage(f"Nothing to clean up in {name}.", STATUS_MS)
            return
        window.statusBar().clearMessage()
        if self.offer is not None:
            self.offer.close()
        self.offer = RecycleWindow(window, found.folder, found.items, locks=self.locks, owner=OWNER, intro=INTRO,
                                   recycle=self.recycle, check_recycle=self.check_recycle)
        self.offer.finished.connect(self._recycled)
        self.offer.revealRequested.connect(lambda path, is_dir: window._in_shell(
            lambda: window.show_in_explorer(path, is_dir), f"File Explorer couldn't show {path}.",
            str(Path(path).parent), True))
        self.offer.show()
        self.offer.raise_()

    def _recycled(self, report) -> None:
        window = self.window
        for folder in report.folders:
            window.refresh_after_tool(folder)
        count = len(report.recycled)
        text = f"Sent {count} item{'s' if count != 1 else ''} to the Recycle Bin." if count else \
            "Nothing was sent to the Recycle Bin."
        window.statusBar().showMessage(text, STATUS_MS)

    def shutdown(self) -> None:
        if self.offer is not None:
            self.offer.close()
        self.finder.close()
