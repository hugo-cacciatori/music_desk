"""The folder summary (SPEC §7): what the side panel shows for a folder, from the index.

Tool shortcuts (Tidy album, ReplayGain) join the Play buttons when the tools exist (M5).
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from music_desk.core.index.queries import FolderSummary
from music_desk.ui.file_view import format_duration


def _coverage(count: int, total: int) -> str:
    if not total:
        return "–"
    if count == total:
        return f"all {total} tracks"
    return f"{count} of {total} tracks"


class FolderSummaryView(QWidget):
    playRequested = Signal(int, bool)  # folder entry id, with subfolders
    toolsRequested = Signal(int)  # folder entry id

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
        self.name_label.setStyleSheet("font-weight: 600;")
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
                ("lyrics", "Lyrics tag"), ("contents", "In this folder"))
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
        self.play_all_button = QPushButton("Play with subfolders")
        self.play_all_button.clicked.connect(lambda: self._play(True))
        self.tools_button = QPushButton("Tools…")
        self.tools_button.clicked.connect(lambda: self.summary and self.toolsRequested.emit(self.summary.id))
        buttons.addWidget(self.play_button)
        buttons.addWidget(self.play_all_button)
        buttons.addStretch(1)
        buttons.addWidget(self.tools_button)
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
        self.values["lyrics"].setText(_coverage(summary.n_with_lyrics_tag, summary.n_tracks))
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
