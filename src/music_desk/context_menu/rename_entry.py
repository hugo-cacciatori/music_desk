"""The right-click Rename with MusicDesk not open (SPEC §31): the app's Rename preview page alone, in a window of its
own: the preview list, Apply, then the summary, which is the recap. No main window.

The plan and the run are the app's own (`RenameRunner.plan_paths`, `apply`), with the Options window's File names
and Rename options and the process's folder locks, so a main window opened meanwhile can't start another tool on
the same folders.
"""

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMessageBox, QVBoxLayout, QWidget

from music_desk.core.path_locks import LockedError, PathLocks
from music_desk.services.rename_job import Applied, Planned, RenameRunner
from music_desk.ui.controllers.tools import rename_target_text
from music_desk.ui.tools.rename_preview import RenamePreview
from music_desk.ui.tools.tool_cards import RENAME, ToolCards

TITLE = "MusicDesk · Rename from tags"


class RenameWindow(QWidget):
    closed = Signal()
    finished = Signal(object)  # list[Path]: the folders renamed in; the index of an open main window catches up

    def __init__(self, cards: ToolCards, locks: PathLocks, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self.resize(900, 600)
        self.cards = cards
        self.view = RenamePreview()
        self.view.set_standalone()
        self.view.backRequested.connect(self.close)
        self.view.doneRequested.connect(self.close)
        self.view.applyRequested.connect(self.apply)
        self.view.cancelRequested.connect(lambda: self.runner.cancel())
        layout = QVBoxLayout(self)
        layout.addWidget(self.view)
        self.runner = RenameRunner(locks, self)
        self.runner.planned.connect(self._planned)
        self.runner.applied.connect(self._applied)
        self._token: int | None = None

    @property
    def running(self) -> bool:
        return self.view.state == "applying"

    def start(self, paths: list[str]) -> bool:
        """Show the window and plan the preview. False when it didn't start: the window then says why."""
        self.bring_to_front()
        targets = [Path(path) for path in paths]
        self.view.show_planning(targets[0] if targets else Path(), what=rename_target_text(targets) if targets
                                else "nothing was clicked")
        if not targets:
            self.view.show_error("Rename didn't start: nothing was clicked.")
            return False
        if self.cards.problems(RENAME):
            self.view.show_error("Rename didn't start: the File names or Rename options need fixing. They are in "
                                 "the right-click menu's MusicDesk > Settings.")
            return False
        self._token = self.runner.plan_paths(targets, self.cards.rename_options())
        return True

    def apply(self) -> None:
        plan = self.view.plan
        if plan is None:
            return
        try:
            self._token = self.runner.apply(plan)
        except LockedError as e:
            self.view.message.setText(f"{e.path} is in use by {e.owner}. Try again when it has finished.")
            return
        self.view.show_applying()

    def bring_to_front(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def _planned(self, result: Planned) -> None:
        if result.token != self._token or result.cancelled:
            return
        if result.plan is None:
            self.view.show_error(f"The preview failed: {result.error}")
        else:
            self.view.show_plan(result.plan)

    def _applied(self, result: Applied) -> None:
        if result.token != self._token:
            return
        if result.report is None:
            self.view.show_error(f"Renaming failed: {result.error}")
        else:
            self.view.show_report(result.report)
        if self.view.plan is not None:
            self.finished.emit(self.view.plan.trees)

    def confirm_stop(self) -> bool:
        answer = QMessageBox.question(
            self, "Rename is running",
            "Stop renaming and close? The files renamed so far keep their new names; the others stay as they were.")
        return answer == QMessageBox.StandardButton.Yes

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.running and not self.confirm_stop():
            event.ignore()
            return
        self.shutdown()
        super().closeEvent(event)
        self.closed.emit()

    def shutdown(self) -> None:
        self.runner.close()  # a preview stops reading; a run stops between two files
