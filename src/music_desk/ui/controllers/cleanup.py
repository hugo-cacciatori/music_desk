"""Clean up's finder (SPEC §32, §35): the Clean up entry's window (context_menu/cleanup_entry.py) reads the trees
on a worker, then opens the Redundant files window with what it found. No preview and no question: the window is
the check, and the Recycle Bin is the undo.

The Recycle Bin goes through attributes that tests replace.
"""

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from music_desk.core import cleanup
from music_desk.core.cleanup import CleanupOptions, Item

log = logging.getLogger(__name__)

OWNER = "Clean up"
INTRO = ("Clean up found these. Every box is checked: uncheck what to keep, then click Delete selected to send the "
         "rest to the Recycle Bin. Closing this window keeps them all. The Clean up options (… > Options…) say "
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
