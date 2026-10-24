"""Pages of the app alone in a window of their own (SPEC §31), driven by the app's own controller: the cue editor
("Edit this cue sheet…", MusicDesk open or closed) and the Trim page ("Trim this track…", MusicDesk closed).

`ToolHost` is the part of the main window those controllers use, and nothing else: the page area, the status bar,
the toast, a job list, the Options window's settings, the shell, and Preview in player. There is no index here:
`refresh_after_tool` only tells the Starter (an open main window's index catches up), and indexing never pauses.
Back, Done and Close close the window; closing during a run asks first, and unsaved cue sheet edits ask as in the
app.
"""

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMainWindow, QMessageBox, QStackedWidget

from music_desk.core import fsops
from music_desk.core.path_locks import PathLocks
from music_desk.services.jobs import JobList
from music_desk.ui.controllers.split import SplitController
from music_desk.ui.controllers.trim import TrimController
from music_desk.ui.toast import Toast
from music_desk.ui.tools.tool_cards import ToolCards

log = logging.getLogger(__name__)


class ToolHost(QMainWindow):
    closed = Signal()
    finished = Signal(object)  # list[Path]: folders the run changed; an open main window's index catches up
    shellFailed = Signal(str)

    def __init__(self, cards: ToolCards, locks: PathLocks, title: str) -> None:
        super().__init__()
        self.setWindowTitle(f"MusicDesk · {title}")
        self.resize(980, 680)
        self.settings = cards.settings
        self.tools = SimpleNamespace(cards=cards)  # what the controllers read: the Options window's cards
        self.center = QStackedWidget()
        self.setCentralWidget(self.center)
        self.toast = Toast(self)
        self.jobs = JobList(self)
        self.playlist_dir: Path | None = None  # None: %LOCALAPPDATA%\MusicDesk\playlists, as the app's
        self.launch = fsops.open_with_default_app
        self.show_in_explorer = fsops.open_in_explorer
        self._shell = ThreadPoolExecutor(1, thread_name_prefix="tool-shell")
        self.shellFailed.connect(self.toast.show_message)
        self.controller: SplitController | TrimController | None = None

    # -- what the controllers call on their window --

    def _library_open(self) -> bool:
        """The controllers' gate for "a run may start, and its folders are re-read after": here the re-read is
        `refresh_after_tool`'s signal."""
        return True

    def refresh_after_tool(self, folder: Path) -> None:
        self.finished.emit([folder])

    def _pause_indexing(self, paused: bool) -> None:
        """No index here: nothing to pause."""

    def _unsaved_edits_under(self, folder: Path) -> bool:
        """No Metadata tab here: no unsaved tag edits."""
        return False

    def _start_job(self, kind: str, folder: Path) -> int:
        return self.jobs.add(kind, folder).id

    def show_job(self, job_id: int) -> None:
        """The one page there is."""
        if self.controller is not None:
            self.center.setCurrentWidget(self.controller.page)

    def close_tools(self) -> None:
        """Back and Done: the page was the whole window."""
        self.close()

    def _name_masks(self) -> tuple[tuple[str, ...], str]:
        return tuple(self.settings.name_masks), self.settings.name_replacement

    def _in_shell(self, fn, message: str, fallback: str, fallback_is_dir: bool) -> None:
        def run() -> None:
            try:
                fn()
            except OSError as e:
                log.warning("%s (%s)", message, e)
                self.shellFailed.emit(message)
        self._shell.submit(run)

    def bring_to_front(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    # -- the window --

    @property
    def running(self) -> bool:
        return self.controller is not None and self.controller.job is not None

    def confirm_stop(self) -> bool:
        what = "the split" if isinstance(self.controller, SplitController) else "the trim"
        answer = QMessageBox.question(
            self, "Still running",
            f"Stop {what} and close? It deletes the files it was writing and leaves the original as it was.")
        return answer == QMessageBox.StandardButton.Yes

    def closeEvent(self, event: QCloseEvent) -> None:
        controller = self.controller
        if isinstance(controller, SplitController) and not controller.can_leave():
            event.ignore()  # unsaved edits of the sheet, kept on Cancel
            return
        if self.running and not self.confirm_stop():
            event.ignore()
            return
        self.shutdown()
        super().closeEvent(event)
        self.closed.emit()

    def shutdown(self) -> None:
        if self.controller is not None:
            self.controller.close()  # a running flac is killed; this run's .partial files are deleted
        self._shell.shutdown(wait=False)


class CueEditorWindow(ToolHost):
    """"Edit this cue sheet…" (SPEC §31): the app's cue editor alone, which edits and saves the sheet (no Split:
    splitting is "Split this cue sheet…"'s recap), with Preview in player; no Back, closing leaves."""

    def __init__(self, cards: ToolCards, locks: PathLocks) -> None:
        super().__init__(cards, locks, "Edit cue sheet")
        self.controller = SplitController(self, locks, self)
        self.controller.page.set_standalone()
        self.center.addWidget(self.controller.page)

    def start(self, paths: list[str]) -> bool:
        self.bring_to_front()
        controller = self.controller
        assert isinstance(controller, SplitController)
        if len(paths) != 1 or Path(paths[0]).suffix.lower() != ".cue":
            controller.page.show_error("Nothing to edit: choose one cue sheet (.cue).")
            return False
        controller.open(paths[0])
        return True


class TrimWindow(ToolHost):
    """"Trim this track…" alone: the track's Trim page, with Trim and Preview in player."""

    def __init__(self, cards: ToolCards, locks: PathLocks) -> None:
        super().__init__(cards, locks, "Trim")
        self.controller = TrimController(self, locks, self)
        self.center.addWidget(self.controller.page)

    def start(self, paths: list[str]) -> bool:
        self.bring_to_front()
        controller = self.controller
        assert isinstance(controller, TrimController)
        if len(paths) != 1 or os.path.isdir(paths[0]):
            controller.page.show_error("Trim didn't start: choose one track (FLAC, WAV or AIFF).")
            return False
        controller.open(paths[0])
        return True
