"""The side panel on the right (SPEC §7): the Metadata and Lyrics tabs for files, the folder summary, or the
preview of one log, cue sheet, note or image (§20)."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QStackedWidget, QTabWidget, QVBoxLayout, QWidget

from music_desk.core import preview
from music_desk.core.index import queries
from music_desk.services.lyrics_service import LyricsService
from music_desk.services.tag_io import TagIO
from music_desk.services.tag_writer import LyricsEmbedded, TagWriter
from music_desk.ui.side_panel.file_preview import FilePreview
from music_desk.ui.side_panel.folder_summary import FolderSummaryView
from music_desk.ui.side_panel.lyrics_tab import LyricsTab
from music_desk.ui.side_panel.metadata_tab import MetadataTab


@dataclass(frozen=True)
class SelectedItem:
    path: Path
    is_dir: bool
    is_audio: bool
    entry_id: int | None = None  # the index entry (the Lyrics tab reads the track from the index)


class SidePanel(QWidget):
    def __init__(self, io: TagIO, writer: TagWriter, conn: Callable[[], sqlite3.Connection | None] | None = None,
                 parent: QWidget | None = None, *, lyrics_service: LyricsService | None = None) -> None:
        super().__init__(parent)
        self.io = io
        self._conn = conn or (lambda: None)
        self.tabs = QTabWidget()
        self.metadata = MetadataTab(io, writer)
        self.lyrics = LyricsTab(self._conn, lyrics_service, writer)
        self.lyrics.can_write = self._can_write_lyrics
        writer.lyricsEmbedded.connect(self._lyrics_embedded)
        writer.replaygainRemoved.connect(self._replaygain_removed)
        self.tabs.addTab(self.metadata, "Metadata")
        self.tabs.addTab(self.lyrics, "Lyrics")
        self.metadata.dirtyChanged.connect(
            lambda dirty: self.tabs.setTabText(0, "Metadata •" if dirty else "Metadata"))

        self.summary = FolderSummaryView()
        self.preview = FilePreview(io)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.tabs)
        self.stack.addWidget(self.summary)
        self.stack.addWidget(self.preview)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.stack)
        self.setMinimumWidth(280)
        self._cover_token: int | None = None
        io.folderCover.connect(self._folder_cover)

    # -- files --

    def show_selection(self, items: list[SelectedItem]) -> None:
        audio = [item.path for item in items if item.is_audio and not item.is_dir]
        self._show_lyrics(items)
        if len(items) == 1 and not items[0].is_dir and not audio and preview.kind(items[0].path):
            self.metadata.show_message(f"{items[0].path.name} isn't an audio file.")  # behind the preview
            self.preview.show_file(items[0].path, self._stamp(items[0].entry_id))
            self.stack.setCurrentWidget(self.preview)
            return
        self._leave_preview()
        self.stack.setCurrentWidget(self.tabs)
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

    def _stamp(self, entry_id: int | None) -> tuple | None:
        """The index's (size, mtime) of a file: a change means it was rewritten, and the preview reloads."""
        conn = self._conn()
        if conn is None or entry_id is None:
            return None
        row = conn.execute("SELECT size, mtime FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return tuple(row) if row else None

    def _leave_preview(self) -> None:
        if self.stack.currentWidget() is self.preview:
            self.preview.clear()

    def _show_lyrics(self, items: list[SelectedItem]) -> None:
        tracks = [item for item in items if item.is_audio and not item.is_dir]
        if len(items) == 1 and tracks and tracks[0].entry_id is not None:
            self.lyrics.show_track(tracks[0].entry_id)
        elif len(items) == 1 and tracks:
            self.lyrics.show_message("This file isn't in the library index yet.")
        elif len(items) > 1:
            self.lyrics.show_message("Select a single track to see its lyrics.")
        else:
            self.lyrics.show_message("Select a track to see its lyrics from LRCLIB.")

    def _can_write_lyrics(self, path: str) -> str | None:
        """Saving LRCLIB lyrics must not race unsaved edits of the same file in the Metadata tab."""
        if self.metadata.dirty and (self.metadata.path == Path(path) or Path(path) in map(Path, self.metadata.paths)):
            return "This file has unsaved edits in the Metadata tab. Save or revert them first."
        return None

    def _replaygain_removed(self, result) -> None:
        """ReplayGain removed from the file the Metadata tab shows: its Loudness block follows."""
        path = self.metadata.path
        if path is not None and not self.metadata.dirty and not self.metadata.multi and \
                any(Path(p) == path for p in result.removed):
            self.metadata.show_file(path)

    def _lyrics_embedded(self, result: LyricsEmbedded) -> None:
        """Lyrics saved into a file the Metadata tab shows (by the Lyrics tab or auto-save): show the new tag."""
        path = self.metadata.path
        if path is not None and not self.metadata.dirty and not self.metadata.multi and \
                any(Path(p) == path for p in result.written):
            self.metadata.show_file(path)

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
        self.lyrics.show_message("Select a track to see its lyrics from LRCLIB.")
        previous = self.summary.summary
        self._leave_preview()
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
