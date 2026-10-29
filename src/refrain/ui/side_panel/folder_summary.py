"""The folder summary (SPEC §7): what the side panel shows for a folder, from the index.

The tools start from the toolbar (SPEC §30), not from here.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from refrain.core.index.queries import FolderSummary
from refrain.ui import theme
from refrain.ui.file_view import format_duration


def _coverage(count: int, total: int) -> str:
    if not total:
        return "–"
    if count == total:
        return f"all {total} tracks"
    return f"{count} of {total} tracks"


class FolderSummaryView(QWidget):
    playRequested = Signal(int, bool)  # folder entry id, with subfolders

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.summary: FolderSummary | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        header = QHBoxLayout()
        self.cover = QLabel("No cover")
        self.cover.setFixedSize(112, 112)
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.setFrameShape(QFrame.Shape.StyledPanel)
        names = QVBoxLayout()
        self.name_label = QLabel()
        self.name_label.setWordWrap(True)
        theme.strong(self.name_label)
        self.path_label = QLabel()
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.cover_source = QLabel()
        self.cover_source.setWordWrap(True)
        names.addWidget(self.name_label)
        names.addWidget(self.path_label)
        names.addWidget(self.cover_source)
        names.addStretch(1)
        header.addWidget(self.cover)
        header.addLayout(names, 1)
        layout.addLayout(header)

        grid = QGridLayout()
        self.values: dict[str, QLabel] = {}
        rows = (("tracks", "Tracks"), ("duration", "Duration"), ("formats", "Formats"), ("rg", "ReplayGain"),
                ("lrclib", "LRCLIB lyrics"), ("contents", "In this folder"))
        for row, (key, label) in enumerate(rows):
            value = QLabel()
            value.setWordWrap(True)
            grid.addWidget(QLabel(label), row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(value, row, 1)
            self.values[key] = value
        grid.setColumnStretch(1, 1)
        box = QFrame()
        box.setFrameShape(QFrame.Shape.StyledPanel)
        box.setLayout(grid)
        layout.addWidget(box)

        buttons = QHBoxLayout()
        self.play_button = QPushButton("Play folder")
        self.play_button.clicked.connect(lambda: self._play(False))
        self.play_button.setToolTip("Play this folder's tracks in your default player, as a temporary playlist")
        self.play_all_button = QPushButton("Play with subfolders")
        self.play_all_button.setToolTip("Play the tracks of this folder and of all its subfolders in your default "
                                        "player")
        self.play_all_button.clicked.connect(lambda: self._play(True))
        buttons.addWidget(self.play_button)
        buttons.addWidget(self.play_all_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addStretch(1)

    def show_summary(self, summary: FolderSummary) -> None:
        folder_changed = self.summary is None or self.summary.id != summary.id
        self.summary = summary
        self.name_label.setText(summary.path.name or str(summary.path))
        self.path_label.setText(str(summary.path))
        self.values["tracks"].setText(f"{summary.n_tracks:,}" if summary.n_tracks else "No tracks")
        self.values["duration"].setText(format_duration(summary.duration) or "–")
        self.values["formats"].setText(summary.formats or "–")
        self.values["rg"].setText(_coverage(summary.n_with_rg, summary.n_tracks))
        self.values["lrclib"].setText(_coverage(summary.n_lrclib_found, summary.n_tracks))  # found on LRCLIB
        parts = []
        if summary.n_subfolders:
            parts.append(f"{summary.n_subfolders} subfolder{'s' if summary.n_subfolders > 1 else ''}")
        if summary.n_files:
            parts.append(f"{summary.n_files} file{'s' if summary.n_files > 1 else ''}")
        self.values["contents"].setText(", ".join(parts) or "Empty")
        direct_tracks = summary.first_track is not None and summary.first_track.parent == summary.path
        self.play_button.setEnabled(direct_tracks)
        self.play_all_button.setEnabled(summary.n_tracks > 0)
        if folder_changed:
            self.set_cover(None, "")

    def set_cover(self, image: QImage | None, source: str) -> None:
        if image is None:
            self.cover.setPixmap(QPixmap())
            self.cover.setText("No cover")
            self.cover_source.setText("")
            return
        self.cover.setPixmap(QPixmap.fromImage(image).scaled(
            self.cover.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.cover_source.setText(f"Cover from {source}")

    def _play(self, recursive: bool) -> None:
        if self.summary is not None:
            self.playRequested.emit(self.summary.id, recursive)
