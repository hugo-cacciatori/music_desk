"""The side panel on the right (SPEC §7): the Metadata tab, and the Lyrics tab (M4)."""

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QTabWidget, QWidget

from music_desk.services.tag_io import TagIO
from music_desk.services.tag_writer import TagWriter
from music_desk.ui.side_panel.metadata_tab import MetadataTab


@dataclass(frozen=True)
class SelectedItem:
    path: Path
    is_dir: bool
    is_audio: bool


class SidePanel(QTabWidget):
    def __init__(self, io: TagIO, writer: TagWriter, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.metadata = MetadataTab(io, writer)
        self.lyrics = QLabel("Lyrics from LRCLIB will appear here.")
        self.lyrics.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lyrics.setWordWrap(True)
        self.addTab(self.metadata, "Metadata")
        self.addTab(self.lyrics, "Lyrics")
        self.metadata.dirtyChanged.connect(
            lambda dirty: self.setTabText(0, "Metadata •" if dirty else "Metadata"))
        self.setMinimumWidth(280)

    def show_selection(self, items: list[SelectedItem]) -> None:
        if not items:
            self.metadata.show_message("Select a file to see its tags.")
        elif len(items) > 1:
            self.metadata.show_message(f"{len(items)} items selected. Select a single file to edit its tags.")
        elif items[0].is_dir:
            self.metadata.show_message("A folder is selected. Select a file to edit its tags.")
        elif not items[0].is_audio:
            self.metadata.show_message(f"{items[0].path.name} isn't an audio file.")
        elif self.metadata.path != items[0].path or self.metadata.snapshot is None:
            self.metadata.show_file(items[0].path)

    def can_leave(self) -> bool:
        return self.metadata.can_leave()
