"""The right-click Remove ReplayGain tags with Refrain not open (SPEC §31): the app's question, then the report
window alone; the process ends when it closes. The removal is the app's own (`TagWriter.remove_replaygain`), under
the process's locks.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from shiboken6 import isValid

from refrain.context_menu.remove_report import RemoveReport, ask_remove, split_tracks
from refrain.core import fsops
from refrain.core.path_locks import PathLocks
from refrain.services.tag_writer import ReplayGainRemoved, TagWriter
from refrain.ui.tools.tool_cards import ToolCards

log = logging.getLogger(__name__)


class RemoveEntry(QObject):
    closed = Signal()  # the report closed, or the question was cancelled: nothing of this entry is left on screen
    finished = Signal(object)  # list[Path]: the tracks' folders; the index of an open main window catches up

    def __init__(self, cards: ToolCards, locks: PathLocks, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.cards = cards
        self.tag_writer = TagWriter(locks, parent=self)
        self.tag_writer.replaygainRemoved.connect(self._removed)
        self.report: RemoveReport | None = None
        self._left_out: list[str] = []
        self._folders: list[Path] = []
        self._shell = ThreadPoolExecutor(1, thread_name_prefix="remove-shell")

    @property
    def running(self) -> bool:
        return self.report is not None and self.report.message.text().startswith("Removing")

    def ask_remove_replaygain(self, count: int, where: str, comment: str) -> bool:
        """Confirm the removal (replaced in tests)."""
        return ask_remove(None, count, where, comment)

    def start(self, paths: list[str]) -> bool:
        """The question, then the removal and its report. False when nothing started: on a cancelled question
        `closed` is emitted at once (connect to it first)."""
        tracks, self._left_out = split_tracks(paths)
        folders = sorted({str(Path(path).parent) for path in tracks})
        self._folders = [Path(folder) for folder in folders]
        self.report = RemoveReport(folders[0] if folders else "")
        self.report.revealRequested.connect(lambda path: self._shell.submit(self._reveal, path))
        self.report.closed.connect(self.closed)
        self.report.closed.connect(self.report.deleteLater)  # parentless: freed while Qt runs
        if not tracks:
            self.report.message.setText("Nothing was done: no track ReplayGain works on was clicked.")
            self.report.bring_to_front()
            return False
        options = self.cards.replaygain_card.options()
        if not self.ask_remove_replaygain(len(tracks), folders[0] if len(folders) == 1 else "several folders",
                                          options.comment):
            self.report.closed.disconnect(self.closed)
            self.report.deleteLater()
            self.report = None
            self.closed.emit()
            return False
        self.tag_writer.remove_replaygain(tracks, options)
        self.report.bring_to_front()
        return True

    def _removed(self, result: ReplayGainRemoved) -> None:
        if self.report is not None:
            self.report.show_result(result, self._left_out)
        self.finished.emit(self._folders)

    @staticmethod
    def _reveal(path: str) -> None:
        try:
            fsops.open_in_explorer(path, True)
        except OSError as e:
            log.warning("File Explorer couldn't show %s (%s)", path, e)

    def shutdown(self) -> None:
        self.tag_writer.close()  # the removal finishes first
        self._shell.shutdown(wait=False)
        if self.report is not None and isValid(self.report):  # not yet freed by its own close
            self.report.hide()
            self.report.deleteLater()
