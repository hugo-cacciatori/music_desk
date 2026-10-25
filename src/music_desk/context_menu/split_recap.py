"""The right-click "Split this cue sheet…" (SPEC §31, Hugo 2026-10-24): a recap of what the split will do, then
Split or Cancel, nothing else. The split is the app's own (`SplitRunner`: the sheet read, planned with the app's
defaults, then cut by the bundled flac), in the same window: its progress and Stop, then the report, which stays
until the window is closed.

The defaults are the cue preview's Split's: every track, the sheet's folder, the File names masks, the sheet's
encoding as read. A sheet that can't be split as it is says why, with Split greyed: "Edit this cue sheet…" in the
right-click menu fixes it. The same window shows with MusicDesk open (its index then re-reads the folder) or
closed.
"""

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from music_desk.core.cue import CueError
from music_desk.core.cue_edit import CueDocument, validate
from music_desk.core.path_locks import LockedError, PathLocks
from music_desk.services.split_job import Loaded, Planned, SplitFinished, SplitRunner
from music_desk.ui.controllers.split import plural, report_text
from music_desk.ui.tools.tool_cards import ToolCards

TITLE = "MusicDesk · Split"
FIX_IT = 'To fix it, use "Edit this cue sheet…" in the right-click menu.'


def clock(samples: int, rate: int) -> str:
    seconds = samples // rate if rate else 0
    return f"{seconds // 60}:{seconds % 60:02d}"


class SplitRecapWindow(QWidget):
    closed = Signal()
    finished = Signal(object)  # list[Path]: the folder written to; an open main window's index catches up

    def __init__(self, cards: ToolCards, locks: PathLocks, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self.resize(720, 460)
        self.cards = cards
        self.state = "loading"  # recap, running, result, error
        self.path: Path | None = None
        self.plan = None
        self.document: CueDocument | None = None
        self.reasons: list[str] = []  # why it can't split as it is; empty: Split is enabled
        self._token: int | None = None
        self._run_token: int | None = None

        self.heading = QLabel()
        self.heading.setWordWrap(True)
        self.message = QLabel("Reading the cue sheet…")
        self.message.setWordWrap(True)
        self.details = QLabel()  # the source, the destination, the encoding
        self.details.setWordWrap(True)
        self.tracks = QTableWidget(0, 3)
        self.tracks.setHorizontalHeaderLabels(["Track", "File", "Length"])
        self.tracks.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tracks.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tracks.verticalHeader().hide()
        self.tracks.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.busy = QProgressBar()
        self.busy.setTextVisible(False)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)

        self.split_button = QPushButton("Split")
        self.split_button.setToolTip("Write these tracks as FLAC files in the cue sheet's folder, each proven against "
                                     "the original; the image and its cue sheet aren't touched")
        self.split_button.setDefault(True)
        self.split_button.clicked.connect(self.split)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setToolTip("Close without splitting anything")
        self.cancel_button.clicked.connect(self.close)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setToolTip("Stop the split; the tracks it was writing are deleted, the image isn't touched")
        self.stop_button.clicked.connect(lambda: self.runner.cancel())
        self.close_button = QPushButton("Close")
        self.close_button.setToolTip("Close this report")
        self.close_button.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addWidget(self.busy, 1)
        buttons.addStretch(1)
        for button in (self.split_button, self.cancel_button, self.stop_button, self.close_button):
            buttons.addWidget(button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.heading)
        layout.addWidget(self.message)
        layout.addWidget(self.details)
        layout.addWidget(self.tracks, 1)
        layout.addWidget(self.report, 1)
        layout.addLayout(buttons)

        self.runner = SplitRunner(locks, self)
        self.runner.loaded.connect(self._loaded)
        self.runner.planned.connect(self._planned)
        self.runner.progress.connect(self._progress)
        self.runner.finished.connect(self._finished)
        self._show("loading")

    @property
    def running(self) -> bool:
        return self.state == "running"

    def start(self, paths: list[str]) -> bool:
        """Read and plan the sheet; the recap follows. False when there is nothing to split (the window says why)."""
        self.bring_to_front()
        if len(paths) != 1 or Path(paths[0]).suffix.lower() != ".cue":
            self._fail("Split didn't start: choose one cue sheet (.cue).")
            return False
        self.path = Path(paths[0])
        self.heading.setText(f"<b>Split</b> · {self.path}")
        self._token = self.runner.load(self.path)
        return True

    def bring_to_front(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    # -- the recap --

    def _loaded(self, loaded: Loaded) -> None:
        if loaded.token != self._token:
            return
        if loaded.document is None or self.path is None:
            self._fail(f"The cue sheet can't be read: {loaded.error}. {FIX_IT}")
            return
        self.document = loaded.document
        try:
            sheet = loaded.document.sheet()
        except CueError as e:
            self._fail(f"Can't split: {e}. {FIX_IT}")
            return
        settings = self.cards.settings
        self._token = self.runner.plan(sheet, self.path, settings.name_masks, settings.name_replacement, None)

    def _planned(self, planned: Planned) -> None:
        document = self.document
        if planned.token != self._token or document is None:
            return
        plan = self.plan = planned.plan
        reasons = []
        if not self.cards.names_card.valid:
            reasons.append("The File names masks need fixing: MusicDesk > Settings in the right-click menu.")
        if any(problem.blocks_save for problem in validate(document, plan.info)):
            reasons.append("The cue sheet has values to fix.")
        if plan.refusal:
            reasons.append(plan.refusal)
        reasons += plan.warnings  # the cue preview's Split doesn't split a sheet that needs a look either
        self.reasons = reasons
        if plan.info is not None and plan.source is not None:
            info = plan.info
            source = f"{plan.source.name}: {info.format}, {info.rate / 1000:g} kHz, {clock(info.samples, info.rate)}"
        else:
            source = plan.source.name if plan.source is not None else "none found"
        self.details.setText(f"Source: {source}<br>Into: {plan.folder}<br>"
                             f"Cue sheet read as {document.encoding}")
        rate = plan.info.rate if plan.info is not None else 0
        self.tracks.setRowCount(len(plan.tracks))
        for row, track in enumerate(plan.tracks):
            number = "hidden" if track.row is None else f"{track.number:02d}"
            for column, text in enumerate((number, track.name, clock(track.length, rate))):
                self.tracks.setItem(row, column, QTableWidgetItem(text))
        self.tracks.resizeColumnToContents(0)
        self.tracks.resizeColumnToContents(2)
        if reasons:
            self.message.setText("<b>This cue sheet can't be split as it is:</b> " + " ".join(reasons) + " " + FIX_IT)
        else:
            count = len(plan.written)
            self.message.setText(f"Split writes {plural(count, 'track')} as FLAC files, each proven identical to its "
                                 "part of the original. The image and its cue sheet stay as they are.")
        self._show("recap")

    # -- the split --

    def split(self) -> None:
        plan = self.plan
        if plan is None or self.reasons or self.state != "recap":
            return
        try:
            self._run_token = self.runner.run(plan)
        except LockedError as e:
            self.message.setText(str(e))
            return
        self.message.setText("Splitting…")
        self.busy.setRange(0, max(len(plan.written), 1))
        self.busy.setValue(0)
        self._show("running")

    def _progress(self, done: int, total: int, name: str) -> None:
        self.busy.setRange(0, max(total, 1))
        self.busy.setValue(done)
        if name != "done":
            self.message.setText(f"Writing track {done + 1} of {total}: {name}")

    def _finished(self, result: SplitFinished) -> None:
        if result.token != self._run_token:
            return
        text = report_text(result)
        self.message.setText(text.split("\n", 1)[0])
        self.report.setPlainText(text)
        self.finished.emit([result.folder])
        self._show("result")

    # -- the window --

    def _fail(self, text: str) -> None:
        self.message.setText(text)
        self._show("error")

    def _show(self, state: str) -> None:
        self.state = state
        recap = state == "recap"
        self.details.setVisible(state in ("recap", "running"))
        self.tracks.setVisible(state in ("recap", "running"))
        self.report.setVisible(state == "result")
        self.busy.setVisible(state in ("loading", "running"))
        if state == "loading":
            self.busy.setRange(0, 0)
        self.split_button.setVisible(recap)
        self.split_button.setEnabled(recap and not self.reasons)
        self.cancel_button.setVisible(state in ("loading", "recap"))
        self.stop_button.setVisible(state == "running")
        self.close_button.setVisible(state in ("result", "error"))

    def confirm_stop(self) -> bool:
        answer = QMessageBox.question(
            self, "Splitting", "Stop the split and close? The tracks it was writing are deleted; the album stays as "
            "it was.")
        return answer == QMessageBox.StandardButton.Yes

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.running and not self.confirm_stop():
            event.ignore()
            return
        self.shutdown()
        super().closeEvent(event)
        self.closed.emit()

    def shutdown(self) -> None:
        self.runner.close()  # a running flac is killed; this run's .partial files are deleted
