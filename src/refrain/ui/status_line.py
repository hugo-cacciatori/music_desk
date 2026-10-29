"""The main window's status bar labels: indexing progress (SPEC §4), and the lyrics lookups with Retry now while
LRCLIB is unreachable (SPEC §9 Controls). Transient messages go through `statusBar().showMessage` as before."""

import os
import time

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QLabel, QToolButton

from refrain.core.index.scanner import ScanProgress
from refrain.services.lyrics_service import OFFLINE, RUNNING, UNREACHABLE, LyricsProgress

PAUSED_NOTE = " (paused while a task reads albums)"
LYRICS_RETRY_TIP = "Try reaching LRCLIB again now instead of waiting."  # the status bar's Retry now


class StatusLine(QObject):
    def __init__(self, window, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.window = window
        self.indexing_paused = False  # while a task reads whole albums (the library says so)
        self._lyrics_state = ""
        self.index_label = QLabel()
        self.index_label.hide()
        self.lyrics_label = QLabel()
        self.lyrics_label.hide()
        self.lyrics_button = QToolButton()  # Retry now, while LRCLIB is unreachable (no Pause since 0.8.3)
        self.lyrics_button.setText("Retry now")
        self.lyrics_button.setToolTip(LYRICS_RETRY_TIP)
        self.lyrics_button.setAutoRaise(True)
        self.lyrics_button.clicked.connect(self._retry_now)
        self.lyrics_button.hide()
        bar = window.statusBar()
        for widget in (self.index_label, self.lyrics_label, self.lyrics_button):
            bar.addPermanentWidget(widget)

    # -- indexing --

    def show_index_progress(self, progress: ScanProgress) -> None:
        library = self.window.library
        root = next((r for r in library.roots() if r.id == progress.root_id), None) if library else None
        name = (os.path.basename(root.path) or root.path) if root else "library"  # a drive root has no basename
        if progress.phase == "structure":
            text = f"Indexing {name}: {progress.folders:,} folders, {progress.files:,} files"
        elif progress.phase == "tags":
            text = f"Reading tags in {name}: {progress.tags_done:,} / {progress.tags_total:,}"
        elif progress.phase == "stats":
            text = f"Indexing {name}: totals"
        else:
            text = f"Indexed {name}: {progress.files:,} files"
        self.index_label.setText(text + (PAUSED_NOTE if self.indexing_paused else ""))
        self.index_label.show()

    def set_indexing_paused(self, paused: bool) -> None:
        """The library pauses indexing while a task reads whole albums (`LibraryService.DRIVE_OWNERS`), whichever
        window started it (SPEC §35); the status line says so."""
        self.indexing_paused = paused
        if self.index_label.isVisible():
            text = self.index_label.text().removesuffix(PAUSED_NOTE)
            self.index_label.setText(text + PAUSED_NOTE if paused else text)

    # -- lyrics --

    def show_lyrics_progress(self, progress: LyricsProgress) -> None:
        """The lyrics label, and Retry now while LRCLIB is unreachable (SPEC §9 Controls)."""
        window = self.window
        if not window._library_open():
            return
        if progress.state != self._lyrics_state:  # offline on or off: the LRCLIB column's pending rows change
            self._lyrics_state = progress.state
            window.file_view.model.refresh_column("lrclib")
            window.search_view.model.refresh_column("lrclib")
        left = f"{progress.pending:,} to look up" if progress.pending is not None else "starting"
        if progress.state == OFFLINE:
            text = "Lyrics: lookups off"
        elif progress.state == UNREACHABLE:
            resume = time.strftime("%H:%M", time.localtime(window.open_library.lyrics.resume_at))
            text = f"LRCLIB unreachable, retrying at {resume}"
        elif progress.state == RUNNING:
            text = f"Looking up lyrics: {left}"
        elif progress.pending:
            text = f"Lyrics: {progress.pending:,} to retry later"
        else:
            text = ""
        self.lyrics_label.setText(text)
        self.lyrics_label.setVisible(bool(text))
        self.lyrics_button.setVisible(progress.state == UNREACHABLE)

    def lyrics_entry(self, _key: str, entry: dict) -> None:
        window = self.window
        if entry.get("status") == "error" and window._library_open():
            window.file_view.model.refresh_column("lrclib")  # the failed key's rows show "error"
            window.search_view.model.refresh_column("lrclib")

    def lrclib_unreachable(self, resume_at: float) -> None:
        minutes = max(1, round((resume_at - time.time()) / 60))
        self.window.toast.show_message(f"LRCLIB unreachable, retrying in {minutes} min.", "Retry now",
                                       self.window.open_library.lyrics.retry_now)

    def _retry_now(self) -> None:
        if self.window._library_open():
            self.window.open_library.lyrics.retry_now()
