"""The side panel on the right (SPEC §7): the Metadata and Lyrics tabs for files, or the folder summary."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QLabel, QStackedWidget, QTabWidget, QVBoxLayout, QWidget

from music_desk.core.index import queries
from music_desk.services.tag_io import TagIO
from music_desk.services.tag_writer import TagWriter
from music_desk.ui.side_panel.folder_summary import FolderSummaryView
from music_desk.ui.side_panel.metadata_tab import MetadataTab


@dataclass(frozen=True)
class SelectedItem:
    path: Path
    is_dir: bool
    is_audio: bool


class SidePanel(QWidget):
    def __init__(self, io: TagIO, writer: TagWriter, conn: Callable[[], sqlite3.Connection | None] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.io = io
        self._conn = conn or (lambda: None)
        self.tabs = QTabWidget()
        self.metadata = MetadataTab(io, writer)
        self.lyrics = QLabel("Lyrics from LRCLIB will appear here.")
        self.lyrics.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lyrics.setWordWrap(True)
        self.tabs.addTab(self.metadata, "Metadata")
        self.tabs.addTab(self.lyrics, "Lyrics")
        self.metadata.dirtyChanged.connect(
            lambda dirty: self.tabs.setTabText(0, "Metadata •" if dirty else "Metadata"))

        self.summary = FolderSummaryView()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.tabs)
        self.stack.addWidget(self.summary)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.stack)
        self.setMinimumWidth(280)
        self._cover_token: int | None = None
        io.folderCover.connect(self._folder_cover)

    # -- files --

    def show_selection(self, items: list[SelectedItem]) -> None:
        self.stack.setCurrentWidget(self.tabs)
        audio = [item.path for item in items if item.is_audio and not item.is_dir]
        if not items:
            self.metadata.show_message("Select a file to see its tags.")
        elif not audio and len(items) == 1 and items[0].is_dir:
            self.metadata.show_message("A folder is selected. Select a file to edit its tags.")
        elif not audio and len(items) == 1:
            self.metadata.show_message(f"{items[0].path.name} isn't an audio file.")
        elif not audio:
            self.metadata.show_message(f"{len(items)} items selected, none of them audio files.")
        elif len(items) == 1:
            if self.metadata.path != audio[0] or self.metadata.snapshot is None:
                self.metadata.show_file(audio[0])
        elif self.metadata.paths != audio or not self.metadata.multi:
            # several items: the audio files among them are edited together (one audio file too, with a note)
            self.metadata.show_files(audio, skipped=len(items) - len(audio))

    def can_leave(self) -> bool:
        return self.metadata.can_leave()

    # -- a folder --

    @property
    def showing_folder(self) -> int | None:
        if self.stack.currentWidget() is self.summary and self.summary.summary is not None:
            return self.summary.summary.id
        return None

    def show_folder(self, entry_id: int) -> None:
        """The folder summary (one folder selected, or nothing selected: the open folder)."""
        conn = self._conn()
        summary = queries.folder_summary(conn, entry_id) if conn is not None else None
        if summary is None:
            self.show_selection([])
            return
        self.metadata.show_message("Select a file to see its tags.")
        previous = self.summary.summary
        self.summary.show_summary(summary)
        self.stack.setCurrentWidget(self.summary)
        if previous is None or previous.id != summary.id or previous.first_track != summary.first_track:
            self._cover_token = self.io.load_folder_cover(summary.path, summary.first_track)

    def refresh_folder(self) -> None:
        """The index changed: update the numbers (and the cover if the first track changed)."""
        if self.showing_folder is not None:
            self.show_folder(self.showing_folder)

    def _folder_cover(self, token: int, image: QImage | None, source: str) -> None:
        if token == self._cover_token:
            self.summary.set_cover(image, source)
