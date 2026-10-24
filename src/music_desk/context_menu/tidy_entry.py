"""The right-click Tidy with MusicDesk not open (SPEC §31): the app's Tidy page alone, in a window of its own (the
plan, Run Tidy, the album-by-album progress, the report), then the Redundant files window, as in the app. No main
window.

The plan and the run are the app's own (`TidyRunner`), with the Options window's File names, Rename, ReplayGain
and Clean up lists and the process's folder locks. The entry ends (`closed`) when both its windows are closed.
"""

import threading
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMessageBox, QVBoxLayout, QWidget

from music_desk.core import cleanup, fsops
from music_desk.core.path_locks import LockedError, PathLocks
from music_desk.core.replaygain import plural
from music_desk.services.tidy_job import TidyFinished, TidyPlanned, TidyRunner, report_text
from music_desk.ui.recycle_window import RecycleWindow
from music_desk.ui.tools.tidy_page import TidyPage
from music_desk.ui.tools.tool_cards import TIDY, ToolCards

TITLE = "MusicDesk · Tidy"
INTRO = ("Tidy made these files redundant (split images and their cue sheets), or found them beside the music "
         "(pictures, videos, folders holding nothing else). Every box is checked: uncheck what to keep, then click "
         "Delete selected to send the rest to the Recycle Bin. Closing this window keeps them all.")


class TidyWindow(QWidget):
    closed = Signal()  # the page and the Redundant files window are both closed
    finished = Signal(object)  # list[Path]: the trees Tidy worked in, or recycled from; an open main window's
    # index catches up

    def __init__(self, cards: ToolCards, locks: PathLocks, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self.resize(900, 640)
        self.cards = cards
        self.locks = locks
        self.page = TidyPage()
        self.page.set_standalone()
        self.page.backRequested.connect(self.close)
        self.page.doneRequested.connect(self.close)
        self.page.runRequested.connect(self.run)
        self.page.stopRequested.connect(lambda: self.runner.cancel())
        layout = QVBoxLayout(self)
        layout.addWidget(self.page)
        self.runner = TidyRunner(locks, self)
        self.runner.planned.connect(self._planned)
        self.runner.planProgress.connect(self.page.set_plan_progress)
        self.runner.lines.connect(self.page.add_lines)
        self.runner.progress.connect(self.page.set_progress)
        self.runner.finished.connect(self._finished)
        self.recycle = fsops.send_to_recycle_bin  # the Redundant files window's Delete selected; tests replace it
        self.check_recycle = fsops.can_recycle
        self.plan = None
        self.offer: RecycleWindow | None = None
        self._token: int | None = None
        self._ended = False
        self._open = {"page"}  # the windows still open ("page", "offer"); isVisible is still True in closeEvent

    @property
    def running(self) -> bool:
        return self.page.state == "running"

    def start(self, paths: list[str]) -> bool:
        """Show the window and read the plan. False when it didn't start: the window then says why."""
        self.bring_to_front()
        roots = [Path(path) for path in paths]
        self.page.show_planning(roots[0] if roots else Path())
        if len(roots) > 1:
            self.page.heading.setText(f"<b>Tidy</b> · {plural(len(roots), 'folder')} in {roots[0].parent}")
        if not roots:
            self.page.show_error("Nothing was clicked.")
            return False
        if self.cards.problems(TIDY):
            self.page.show_error("The File names, Rename or ReplayGain options need fixing. They are in the "
                                 "right-click menu's MusicDesk > Settings.")
            return False
        settings = self.cards.settings
        self._token = self.runner.plan(roots, self.cards.rename_options(), self.cards.replaygain_card.options(),
                                       settings.name_masks, settings.name_replacement,
                                       self.cards.cleanup_options().lists)
        return True

    def _planned(self, planned: TidyPlanned) -> None:
        if planned.token != self._token or planned.cancelled:
            return
        if planned.error or planned.plan is None:
            self.page.show_error(planned.error or "The folders couldn't be read.")
            return
        self.plan = planned.plan
        self.page.show_plan(planned.plan)

    def run(self) -> None:
        plan = self.plan
        if plan is None or not plan.has_work or self.running:
            return
        try:
            self._token = self.runner.run(plan, self.cards.replaygain_card.options())
        except LockedError as e:
            self.page.message.setText(f"{e.path} is in use by {e.owner}. Try again when it has finished.")
            self.page.message.show()
            return
        self.page.show_running()

    def _finished(self, result: TidyFinished) -> None:
        if result.token != self._token:
            return
        self.page.show_result(report_text(result))
        self.finished.emit(self.plan.trees if self.plan is not None else [result.root])
        if result.offers:
            self._show_offer(result.offers, result.root)

    def _show_offer(self, items: list[cleanup.Item], folder: Path) -> None:
        self.offer = RecycleWindow(None, folder, items, locks=self.locks, owner="the Tidy tool", intro=INTRO,
                                   recycle=self.recycle, check_recycle=self.check_recycle)
        self.offer.finished.connect(lambda report: self.finished.emit(report.folders))
        self.offer.revealRequested.connect(lambda path, is_dir: reveal(path, is_dir))
        self.offer.closed.connect(lambda: self._window_closed("offer"))
        self._open.add("offer")
        self.offer.show()
        self.offer.raise_()

    def bring_to_front(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def confirm_stop(self) -> bool:
        answer = QMessageBox.question(
            self, "Tidy is running",
            "Stop Tidy and close? The current step stops as the tools do (a split leaves nothing half-written), and "
            "no further album starts.")
        return answer == QMessageBox.StandardButton.Yes

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.running and not self.confirm_stop():
            event.ignore()
            return
        self.runner.close()  # a running split, rename or rsgain stops as those tools' do
        super().closeEvent(event)
        self._window_closed("page")

    def _window_closed(self, which: str) -> None:
        """Once the page and its Redundant files window are both closed, the entry is over."""
        self._open.discard(which)
        if self._ended or self._open:
            return
        self._ended = True
        if self.offer is not None:
            self.offer.deleteLater()
        self.closed.emit()

    def shutdown(self) -> None:
        self.runner.close()
        if self.offer is not None:
            self.offer.close()


def reveal(path: str, is_dir: bool) -> None:
    """Open in File Explorer, from a Redundant files window; on a thread of its own (the shell can be slow)."""
    def run() -> None:
        try:
            fsops.open_in_explorer(path, is_dir)
        except OSError:
            pass

    threading.Thread(target=run, daemon=True).start()
