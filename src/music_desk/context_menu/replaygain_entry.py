"""The right-click ReplayGain entries with MusicDesk not open (SPEC §31): the app's ReplayGain page alone, in a
window of its own: rsgain's lines, the progress bar and Stop, then the report with its buttons. No main window.

The run is the app's own job (`ReplayGainRunner.start`), with the Options window's ReplayGain options and the
process's folder locks, so a main window opened meanwhile can't start another tool on the same folders. Each entry
gets its own window, and a finished report stays until the user closes it.
"""

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QVBoxLayout, QWidget

from music_desk.core import fsops
from music_desk.core.path_locks import LockedError, PathLocks
from music_desk.services.replaygain_job import DISCOGRAPHY, LOOSE, TRACKS, Finished, ReplayGainRunner
from music_desk.ui.controllers.tools import ENTRY_KINDS, entry_title
from music_desk.ui.tools.rg_report import ReplayGainView
from music_desk.ui.tools.tool_cards import REPLAYGAIN, ToolCards

log = logging.getLogger(__name__)

TITLE = "MusicDesk · ReplayGain"


class ReplayGainWindow(QWidget):
    closed = Signal()
    finished = Signal(object)  # Finished: the index of an open main window catches up on its folders
    shellFailed = Signal(str)

    def __init__(self, cards: ToolCards, locks: PathLocks, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self.resize(900, 600)
        self.cards = cards
        self.view = ReplayGainView()
        self.view.back_button.hide()  # nothing to go back to
        self.view.done_button.setToolTip("Close this window")
        self.view.doneRequested.connect(self.close)
        self.view.cancelRequested.connect(lambda: self.runner.cancel())
        self.view.openLogRequested.connect(lambda path: self._in_shell(
            lambda: fsops.open_with_default_app(path), f"Windows couldn't open {os.path.basename(path)}."))
        self.view.revealRequested.connect(lambda path: self._in_shell(
            lambda: fsops.open_in_explorer(path, True), f"File Explorer couldn't show {path}."))
        self.note = QLabel()  # a later entry refused while this one runs
        self.note.setWordWrap(True)
        self.note.hide()
        layout = QVBoxLayout(self)
        layout.addWidget(self.view, 1)
        layout.addWidget(self.note)
        self.runner = ReplayGainRunner(locks, self)
        self.runner.lines.connect(self.view.add_lines)
        self.runner.progress.connect(self.view.set_progress)
        self.runner.finished.connect(self._finished)
        self._shell = ThreadPoolExecutor(1, thread_name_prefix="replaygain-shell")
        self.shellFailed.connect(self._shell_failed)

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
        self.view.heading.setText(f"<b>{entry_title(kind, targets)}</b>" + (f" · {folder}" if folder else ""))
        problems = self.cards.problems(REPLAYGAIN)
        if not targets or problems:
            reason = problems[0] if problems else "nothing was clicked"
            self.view.show_error(f"ReplayGain didn't start: {reason}. The options are in the right-click menu's "
                                 "MusicDesk > Settings.")
            return False
        try:
            self.runner.start(kind, targets, self.cards.replaygain_card.options())
        except LockedError as e:
            self.view.show_error(f"ReplayGain didn't start: {e.path} is in use by {e.owner}. Try again when it has "
                                 "finished.")
            return False
        self.view.show_running(folder, entry_title(kind, targets), kind == DISCOGRAPHY,
                               "Track" if kind == LOOSE else "Album")
        return True

    def refuse(self) -> None:
        """Another ReplayGain entry while this one runs: one rsgain at a time (SPEC §10 B)."""
        self.bring_to_front()
        self.note.setText("Another ReplayGain entry wasn't started: this run is still going. Choose it again when "
                          "this one has finished.")
        self.note.show()

    def bring_to_front(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def _finished(self, finished: Finished) -> None:
        self.note.hide()
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
        self._shell.shutdown(wait=False)

    def _in_shell(self, fn, message: str) -> None:
        def run() -> None:
            try:
                fn()
            except OSError as e:
                log.warning("%s (%s)", message, e)
                self.shellFailed.emit(message)
        self._shell.submit(run)

    def _shell_failed(self, message: str) -> None:
        self.note.setText(message)
        self.note.show()
