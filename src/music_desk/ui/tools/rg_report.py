"""The ReplayGain tool's page (SPEC §10 B): the running job, and the report. There is no preview: the card's Run
starts the job, which only writes tags and the logs, and the report lists what was skipped (unsupported formats,
a command line too long).

Running: per-album progress and live log lines, with Stop. Report: for a discography the summary table (Album,
Result, Album gain, Leveled to, Files changed), then each album's report as written to its replaygain.log, with
"Open replaygain.log" and "Open in File Explorer".
"""

import time
from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from music_desk.core.replaygain import format_gain, plural
from music_desk.services.replaygain_job import Finished
from music_desk.ui.qt_util import cell_text

SUMMARY_COLUMNS = ("Album", "Result", "Album gain", "Leveled to", "Files changed")


def table(columns) -> QTableWidget:
    widget = QTableWidget(0, len(columns))
    widget.setHorizontalHeaderLabels(columns)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.verticalHeader().hide()
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    widget.horizontalHeader().setStretchLastSection(True)
    return widget


def fill(widget: QTableWidget, rows: list[tuple[str, ...]]) -> None:
    widget.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            widget.setItem(r, c, QTableWidgetItem(value))
    widget.resizeColumnsToContents()


class ReplayGainView(QWidget):
    backRequested = Signal()
    cancelRequested = Signal()
    doneRequested = Signal()
    openLogRequested = Signal(str)  # a replaygain.log
    revealRequested = Signal(str)  # a folder

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = "idle"  # running, result, error
        self._album = ""
        self._unit = "Album"
        self._album_started = 0.0
        self._clock = QTimer(self, interval=1000)  # the elapsed time while an album is scanned
        self._clock.timeout.connect(self._tick)
        self.finished: Finished | None = None
        self.folder: Path | None = None

        self.back_button = QPushButton("← Back")
        self.back_button.setToolTip("Go back to the file list")
        self.back_button.clicked.connect(self.backRequested)
        self.heading = QLabel()
        self.heading.setWordWrap(True)
        header = QHBoxLayout()
        header.addWidget(self.back_button)
        header.addWidget(self.heading, 1)

        self.busy = QProgressBar()
        self.busy.setTextVisible(False)
        self.busy.setMaximumWidth(160)
        self.message = QLabel()
        self.message.setWordWrap(True)
        status = QHBoxLayout()
        status.addWidget(self.busy)
        status.addWidget(self.message, 1)

        self.summary_table = table(SUMMARY_COLUMNS)
        self.text = QPlainTextEdit()  # live log lines, then the reports
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setMaximumBlockCount(20000)
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.text.setFont(mono)  # the RunLog tables line up in columns

        self.stop_button = QPushButton("Stop")
        self.stop_button.setToolTip("Stop ReplayGain: rsgain is stopped and the album being measured isn't "
                                    "leveled")
        self.stop_button.clicked.connect(self.cancelRequested)
        self.open_log_button = QPushButton("Open replaygain.log")
        self.open_log_button.setToolTip("Open the replaygain.log this run wrote, in its default app")
        self.open_log_button.clicked.connect(self._open_log)
        self.reveal_button = QPushButton("Open in File Explorer")
        self.reveal_button.clicked.connect(lambda: self.folder and self.revealRequested.emit(str(self.folder)))
        self.reveal_button.setToolTip("Show the folder in File Explorer")
        self.done_button = QPushButton("Done")
        self.done_button.setToolTip("Close this report and go back to the file list")
        self.done_button.clicked.connect(self.doneRequested)
        buttons = QHBoxLayout()
        for button in (self.stop_button, self.open_log_button, self.reveal_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self.done_button)

        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addLayout(status)
        layout.addWidget(self.summary_table)
        layout.addWidget(self.text, 2)
        layout.addLayout(buttons)
        self._show("idle", "")

    # -- states --

    def show_running(self, folder: Path | None = None, title: str | None = None, discography: bool = False,
                     unit: str = "Album") -> None:
        """`unit`: what the progress counts, "Album" or (tracks each on their own) "Track"."""
        self._unit = unit
        if folder is not None:
            self.folder = folder
        what = title or f"ReplayGain · {'Discography' if discography else 'This album'}"
        self.heading.setText(f"<b>{what}</b> · {self.folder}")
        self.text.clear()
        self.busy.setRange(0, 0)
        self._album = "Starting"
        self._album_started = time.monotonic()
        self._show("running", "")
        self._clock.start()
        self._tick()

    def add_lines(self, lines: list[str]) -> None:
        for line in lines:
            self.text.appendPlainText(line)

    def set_progress(self, done: int, total: int, label: str) -> None:
        if total <= 0:
            return
        self.busy.setRange(0, total)
        self.busy.setValue(done)
        if label and label != "done":
            self._album = f"{self._unit} {min(done + 1, total)} of {total}: {label}"
            self._album_started = time.monotonic()
            self._tick()

    def _tick(self) -> None:
        """While an album is scanned: its elapsed time. rsgain prints nothing until the album is done (its output
        is held back when it goes to a pipe), so without this a long album looked frozen."""
        if self.state != "running":
            self._clock.stop()
            return
        seconds = int(time.monotonic() - self._album_started)
        self.message.setText(f"{self._album} · {seconds // 60}:{seconds % 60:02d}. rsgain reports each album when "
                             "it is done; files in this folder can't be edited meanwhile.")

    def show_result(self, finished: Finished) -> None:
        self._clock.stop()
        self.finished = finished
        self.folder = finished.folder
        if finished.error:
            self._show("error", f"The job failed: {finished.error}")
            return
        if finished.nothing:
            self._show("result", "Nothing was done: no audio file rsgain can scan here.")
            return
        if finished.loose is not None:
            self._show_loose(finished)
            return
        results = [run_log.result() for run_log in finished.album_logs]
        if finished.result is not None:
            fill(self.summary_table, [
                (a.name, a.result, format_gain(a.gain),
                 format_gain(finished.result.target) if a.changed is not None else "not leveled",
                 str(a.changed) if a.changed is not None else "-") for a in finished.result.albums])
            rows = sum(self.summary_table.rowHeight(r) for r in range(self.summary_table.rowCount()))
            self.summary_table.setFixedHeight(min(240, self.summary_table.horizontalHeader().height() + rows + 4))
        reports = "\n".join(run_log.text() for run_log in finished.album_logs)
        self.text.setPlainText(reports)
        problems = sum(1 for r in results if r != "OK")
        if finished.cancelled:
            message = "Stopped. The albums after that point were not processed, and nothing was leveled."
        elif problems:
            message = f"Done, but {plural(problems, 'album')} need{'s' if problems == 1 else ''} checking: see below."
        else:
            message = f"Done: {plural(len(results), 'album')} OK."
        if finished.result is not None and finished.result.target is not None and not finished.cancelled:
            message += f" Every album was leveled to {format_gain(finished.result.target)}."
        self._show("result", message)

    def _show_loose(self, finished: Finished) -> None:
        """Tracks each on its own (SPEC §31): their report is only here, no log file has it."""
        loose = finished.loose
        assert loose is not None
        self.text.setPlainText(loose.text())
        problems = sum(1 for run_log in loose.logs if run_log.result() != "OK")
        if finished.cancelled:
            message = f"Stopped: {plural(len(loose.not_run), 'track')} after that point were not processed."
        elif problems:
            message = f"Done, but {plural(problems, 'track')} need{'s' if problems == 1 else ''} checking: see below."
        else:
            message = f"Done: {plural(len(loose.logs), 'track')} OK, each on its own."
        if loose.skipped:
            message += f" {plural(len(loose.skipped), 'file')} left out."
        self._show("result", message)

    def show_error(self, text: str) -> None:
        self._show("error", text)

    def _show(self, state: str, message: str) -> None:
        self.state = state
        self.message.setText(message)
        self.busy.setVisible(state == "running")
        has_summary = state == "result" and self.finished is not None and self.finished.result is not None
        self.summary_table.setVisible(has_summary)
        self.text.setVisible(state in ("running", "result"))
        self.stop_button.setVisible(state == "running")
        self.open_log_button.setVisible(state == "result" and self.finished is not None and not self.finished.nothing
                                        and self.finished.log_path is not None)
        self.reveal_button.setVisible(state in ("result", "error"))
        self.done_button.setVisible(state != "running")
        self.done_button.setText("Done" if state == "result" else "Close")

    def _open_log(self) -> None:
        if self.finished is not None and self.finished.log_path is not None:
            self.openLogRequested.emit(str(self.finished.log_path))

    def summary_rows(self) -> list[tuple[str, ...]]:
        return [tuple(cell_text(self.summary_table, r, c) for c in range(len(SUMMARY_COLUMNS)))
                for r in range(self.summary_table.rowCount())]
