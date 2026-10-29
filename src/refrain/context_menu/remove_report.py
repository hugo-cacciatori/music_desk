"""The right-click Remove ReplayGain tags' report (SPEC §31): a small window that stays until it is closed, the same
whether Refrain was open or not. It says how many tracks were cleared, how many had no ReplayGain tags, and each
file that couldn't be changed, with why.
"""

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from refrain.core.formats import format_for
from refrain.core.text import plural
from refrain.services.tag_writer import ReplayGainRemoved
from refrain.ui import theme
from refrain.ui.task_header import TaskHeader

TITLE = "Refrain · Remove ReplayGain tags"


def ask_remove(parent: QWidget | None, count: int, where: str, comment: str) -> bool:
    """The app's question before removing ReplayGain tags (SPEC §10 B), in the app or alone."""
    noun = plural(count, "track")
    box = QMessageBox(QMessageBox.Icon.Question, "Remove ReplayGain tags",
                      f"Remove the ReplayGain tags from {noun} in {where}?\n\n"
                      f"This deletes their track and album gain and peak values, and the comment line "
                      f"\"{comment}\" that ReplayGain writes. Other tags, other comment lines and the audio "
                      f"itself are not changed, and replaygain.log files stay.", parent=parent)
    remove = box.addButton("Remove", QMessageBox.ButtonRole.DestructiveRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    box.exec()
    return box.clickedButton() is remove


def split_tracks(paths: list[str]) -> tuple[list[str], list[str]]:
    """The clicked tracks ReplayGain works on (an extension rsgain scans, as the menu judges them), and the rest,
    left out. The menu shows the entry only when there is no rest; a start from the command line may have one."""
    tracks, left_out = [], []
    for path in paths:
        info = format_for(path)
        (tracks if info is not None and info.replaygain else left_out).append(path)
    return tracks, left_out


def report_lines(result: ReplayGainRemoved, left_out: list[str]) -> tuple[str, list[str]]:
    """The window's message, and its list: the files not changed (with why), then the files left out."""
    done = len(result.removed)
    parts = [f"Removed the ReplayGain tags of {plural(done, 'track')}." if done else
             "No ReplayGain tags were removed."]
    if result.unchanged:
        parts.append(f"{plural(result.unchanged, 'track')} had none.")
    if result.failed:
        parts.append(f"{plural(len(result.failed), 'file')} couldn't be changed: see below.")
    lines = [message for _path, message in result.failed]
    if left_out:
        parts.append(f"{plural(len(left_out), 'file')} left out: not a track ReplayGain works on.")
        lines += [f"{os.path.basename(path)}: left out" for path in left_out]
    return " ".join(parts), lines


class RemoveReport(QWidget):
    revealRequested = Signal(str)  # the tracks' folder
    closed = Signal()

    def __init__(self, folder: str, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(TITLE)
        self.resize(640, 320)
        theme.task_window(self)
        self.folder = folder
        self.heading = TaskHeader()
        self.heading.set("Remove ReplayGain tags", folder)
        self.message = QLabel("Removing the ReplayGain tags…")
        self.message.setWordWrap(True)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.hide()
        theme.card(self.details)
        reveal = QPushButton("Open in File Explorer")
        reveal.setToolTip("Show the tracks' folder in File Explorer")
        reveal.clicked.connect(lambda: self.revealRequested.emit(self.folder))
        self.close_button = QPushButton("Close")
        self.close_button.setToolTip("Close this report")
        self.close_button.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addWidget(reveal)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.heading)
        layout.addWidget(self.message)
        layout.addWidget(self.details, 1)
        layout.addLayout(buttons)

    def show_result(self, result: ReplayGainRemoved, left_out: list[str]) -> None:
        message, lines = report_lines(result, left_out)
        self.message.setText(message)
        self.details.setPlainText("\n".join(lines))
        self.details.setVisible(bool(lines))
        self.bring_to_front()

    def bring_to_front(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event: QCloseEvent) -> None:
        super().closeEvent(event)
        self.closed.emit()
