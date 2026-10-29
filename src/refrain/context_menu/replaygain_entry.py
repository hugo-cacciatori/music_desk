"""The right-click ReplayGain entries (SPEC §31, §35): the app's ReplayGain page alone, in a
window of its own: rsgain's lines, the progress bar and Stop, then the report with its buttons. No main window.

The run is the app's own job (`ReplayGainRunner.start`), with the Options window's ReplayGain options and the
process's folder locks, so a main window opened meanwhile can't start another tool on the same folders. Each entry
gets its own window, and a finished report stays until the user closes it.
"""

import os
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QVBoxLayout, QWidget

from refrain.context_menu.entries import OPTIONS_WHERE
from refrain.core.path_locks import LockedError, PathLocks
from refrain.services.replaygain_job import DISCOGRAPHY, LOOSE, TRACKS, Finished, ReplayGainRunner
from refrain.ui import theme
from refrain.ui.controllers.tools import ENTRY_KINDS, entry_problem, entry_title
from refrain.ui.qt_util import bring_to_front
from refrain.ui.shell import Shell
from refrain.ui.tools.replaygain_report import ReplayGainView
from refrain.ui.tools.tool_cards import REPLAYGAIN, ToolCards

TITLE = "Refrain · ReplayGain"


class ReplayGainWindow(QWidget):
    closed = Signal()
    finished = Signal(object)  # Finished: the index of an open main window catches up on its folders

    def __init__(self, cards: ToolCards, locks: PathLocks, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self.resize(900, 600)
        theme.task_window(self)  # the window background under its page's cards (SPEC §36 task 4)
        self.cards = cards
        self.view = ReplayGainView()
        self.view.doneRequested.connect(self.close)
        self.view.cancelRequested.connect(lambda: self.runner.cancel())
        self.shell = Shell("replaygain-shell", self)
        self.shell.failed.connect(self._shell_failed)
        self.view.openLogRequested.connect(lambda path: self.shell.run(
            lambda: self.shell.launch(path), f"Windows couldn't open {os.path.basename(path)}."))
        self.view.revealRequested.connect(lambda path: self.shell.run(
            lambda: self.shell.show_in_explorer(path, True), f"File Explorer couldn't show {path}."))
        self.note = QLabel()  # the log or File Explorer couldn't open
        self.note.setWordWrap(True)
        self.note.hide()
        layout = QVBoxLayout(self)
        layout.addWidget(self.view, 1)
        layout.addWidget(self.note)
        self.runner = ReplayGainRunner(locks, self)
        self.runner.lines.connect(self.view.add_lines)
        self.runner.progress.connect(self.view.set_progress)
        self.runner.finished.connect(self._finished)

    @property
    def running(self) -> bool:
        return self.view.state == "running"

    def start(self, entry: str, paths: list[str]) -> bool:
        """Show the window and start the entry's run at once (no recap, as the tool). False when it didn't start:
        the window then says why."""
        self.bring_to_front()
        kind = ENTRY_KINDS[entry]
        targets = [Path(path) for path in paths]
        folder = targets[0].parent if targets and kind in (TRACKS, LOOSE) else targets[0] if targets else None
        self.view.folder = folder  # Open in File Explorer, also after a refusal
        self.view.heading.set(entry_title(kind, targets), folder or "")
        problem = entry_problem(kind, targets)
        if problem:
            self.view.show_error(f"ReplayGain didn't start: {problem}.")
            return False
        problems = self.cards.problems(REPLAYGAIN)
        if problems:
            self.view.show_error(f"ReplayGain didn't start: {problems[0]}. The options: {OPTIONS_WHERE}.")
            return False
        try:
            self.runner.start(kind, targets, self.cards.replaygain_card.options())
        except LockedError as e:
            self.view.show_error(f"ReplayGain didn't start: {e}")
            return False
        self.view.show_running(folder, entry_title(kind, targets), kind == DISCOGRAPHY,
                               "Track" if kind == LOOSE else "Album")
        return True

    def bring_to_front(self) -> None:
        bring_to_front(self)

    def _finished(self, finished: Finished) -> None:
        self.view.show_result(finished)
        QApplication.alert(self)  # the taskbar button flashes if the window is behind others
        self.finished.emit(finished)

    def confirm_stop(self) -> bool:
        answer = QMessageBox.question(
            self, "ReplayGain is running",
            "Stop ReplayGain and close? The album being measured is reported as cancelled and isn't leveled; the "
            "ones after it aren't processed.")
        return answer == QMessageBox.StandardButton.Yes

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.running and not self.confirm_stop():
            event.ignore()
            return
        self.shutdown()
        super().closeEvent(event)
        self.closed.emit()

    def shutdown(self) -> None:
        self.runner.close()  # a running rsgain is killed; its album is reported as cancelled
        self.shell.shutdown()

    def _shell_failed(self, message: str, _fallback: str, _fallback_is_dir: bool) -> None:
        self.note.setText(message)
        self.note.show()
