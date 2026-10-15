"""The ReplayGain tool's page (SPEC §10 B): the preview, the running job, and the report.

Preview: the album folders that will be processed, with their keyword subfolders and file counts, and warnings
(unsupported formats skipped, a command line too long). Running: per-album progress and live log lines, with
Stop. Report: for a discography the summary table (Album, Result, Album gain, Leveled to, Files changed), then each
album's report as written to its replaygain.log, with "Open replaygain.log" and "Open in File Explorer".
"""

from pathlib import Path

from PySide6.QtCore import Signal
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

from music_desk.core.replaygain import AlbumPreview, format_gain, plural
from music_desk.services.replaygain_job import Finished

PREVIEW_COLUMNS = ("Album folder", "Files", "Subfolders", "Notes")
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


def short_result(finished: Finished) -> str:
    """One line for the jobs panel (after its state word: Done, Stopped, Failed)."""
    if finished.error:
        return finished.error
    if finished.nothing:
        return "nothing to do, no audio file rsgain can scan"
    if finished.cancelled:
        return "nothing was leveled; see the report"
    results = [run_log.result() for run_log in finished.album_logs]
    problems = sum(1 for r in results if r != "OK")
    if problems:
        text = f"{plural(len(results), 'album')}, {problems} need{'s' if problems == 1 else ''} checking"
    else:
        text = f"{plural(len(results), 'album')} OK"
    if finished.result is not None and finished.result.target is not None:
        text += f", leveled to {format_gain(finished.result.target)}"
    return text


class ReplayGainView(QWidget):
    backRequested = Signal()
    runRequested = Signal()
    cancelRequested = Signal()
    doneRequested = Signal()
    openLogRequested = Signal(str)  # a replaygain.log
    revealRequested = Signal(str)  # a folder

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = "idle"  # previewing, preview, running, result, error
        self.albums: list[AlbumPreview] = []
        self.finished: Finished | None = None
        self.folder: Path | None = None

        self.back_button = QPushButton("← Back to Tools")
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

        self.albums_table = table(PREVIEW_COLUMNS)
        self.summary_table = table(SUMMARY_COLUMNS)
        self.text = QPlainTextEdit()  # live log lines, then the reports
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setMaximumBlockCount(20000)
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.text.setFont(mono)  # the RunLog tables line up in columns

        self.run_button = QPushButton("Run")
        self.run_button.clicked.connect(self.runRequested)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.cancelRequested)
        self.open_log_button = QPushButton("Open replaygain.log")
        self.open_log_button.clicked.connect(self._open_log)
        self.reveal_button = QPushButton("Open in File Explorer")
        self.reveal_button.clicked.connect(lambda: self.folder and self.revealRequested.emit(str(self.folder)))
        self.done_button = QPushButton("Done")
        self.done_button.clicked.connect(self.doneRequested)
        buttons = QHBoxLayout()
        for button in (self.run_button, self.stop_button, self.open_log_button, self.reveal_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self.done_button)

        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addLayout(status)
        layout.addWidget(self.albums_table, 1)
        layout.addWidget(self.summary_table)
        layout.addWidget(self.text, 2)
        layout.addLayout(buttons)
        self._show("idle", "")

    # -- states --

    def show_previewing(self, folder: Path, discography: bool, title: str = "ReplayGain") -> None:
        self.folder = folder
        self.heading.setText(f"<b>{title}</b> · {'Discography' if discography else 'This album'} · {folder}")
        self.albums_table.setRowCount(0)
        self._show("previewing", "Looking for album folders…")

    def show_preview(self, albums: list[AlbumPreview]) -> None:
        self.albums = albums
        rows = []
        for album in albums:
            subfolders = ", ".join(f"{name} ({count})" for name, count in album.subfolders) or "–"
            notes = "; ".join(album.warnings) or ("no audio file rsgain can scan" if not album.files else "")
            rows.append((album.name, str(len(album.files)), subfolders, notes))
        fill(self.albums_table, rows)
        runnable = [a for a in albums if a.files and not a.too_long]
        if not albums or not any(a.files for a in albums):
            message = "Nothing to do: no audio file rsgain can scan here."
        else:
            message = (f"{plural(len(runnable), 'album')} will be processed, one at a time. Tags are written and "
                       f"a report is appended to each album's replaygain.log. Nothing has changed yet.")
        self._show("preview", message)
        self.run_button.setEnabled(bool(runnable))

    def show_running(self, folder: Path | None = None, title: str | None = None) -> None:
        if folder is not None:
            self.folder = folder
        if title is not None:
            self.heading.setText(f"<b>{title}</b> · {self.folder}")
        self.text.clear()
        self.busy.setRange(0, 0)
        self._show("running", "Running… Files in this folder can't be edited meanwhile.")

    def add_lines(self, lines: list[str]) -> None:
        for line in lines:
            self.text.appendPlainText(line)

    def set_progress(self, done: int, total: int, label: str) -> None:
        if total <= 0:
            return
        self.busy.setRange(0, total)
        self.busy.setValue(done)
        if label and label != "done":
            self.message.setText(f"Album {min(done + 1, total)} of {total}: {label}")

    def show_result(self, finished: Finished) -> None:
        self.finished = finished
        self.folder = finished.folder
        if finished.error:
            self._show("error", f"The job failed: {finished.error}")
            return
        if finished.nothing:
            self._show("result", "Nothing was done: no audio file rsgain can scan here.")
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

    def show_error(self, text: str) -> None:
        self._show("error", text)

    def _show(self, state: str, message: str) -> None:
        self.state = state
        self.message.setText(message)
        self.busy.setVisible(state in ("previewing", "running"))
        if state == "previewing":
            self.busy.setRange(0, 0)
        self.albums_table.setVisible(state == "preview")
        has_summary = state == "result" and self.finished is not None and self.finished.result is not None
        self.summary_table.setVisible(has_summary)
        self.text.setVisible(state in ("running", "result"))
        self.run_button.setVisible(state == "preview")
        self.stop_button.setVisible(state == "running")
        self.open_log_button.setVisible(state == "result" and self.finished is not None and not self.finished.nothing)
        self.reveal_button.setVisible(state in ("result", "error"))
        self.done_button.setVisible(state != "running")
        self.done_button.setText("Done" if state == "result" else "Close")

    def _open_log(self) -> None:
        if self.finished is not None:
            self.openLogRequested.emit(str(self.finished.log_path))

    def shown_rows(self) -> list[tuple[str, ...]]:
        """The preview table (tests)."""
        return [tuple(self.albums_table.item(r, c).text() for c in range(len(PREVIEW_COLUMNS)))
                for r in range(self.albums_table.rowCount())]

    def summary_rows(self) -> list[tuple[str, ...]]:
        return [tuple(self.summary_table.item(r, c).text() for c in range(len(SUMMARY_COLUMNS)))
                for r in range(self.summary_table.rowCount())]

