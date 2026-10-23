"""The Lyrics tab (SPEC §8): what LRCLIB returned for the selected track. The file's own lyrics tag plays no part
(M12, SPEC §26: no function relies on it).

Everything comes from the index on the UI's read connection, so showing a track reads no file. The tab follows the
lyrics service: a result for the shown key, a change of state (offline, paused, unreachable) or a retry re-renders
it. Opening the Genius and Google searches is left to the window, which launches off the UI thread.
"""

import sqlite3
from collections.abc import Callable
from datetime import date, datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from music_desk.core.index.lyrics_index import LyricsInfo, lyrics_info
from music_desk.core.lyrics import FOUND, INSTRUMENTAL, NOT_FOUND, is_fresh, search_urls
from music_desk.services.lyrics_service import OFFLINE, PAUSED, UNREACHABLE, LyricsProgress, LyricsService

# states (also what tests check)
MESSAGE = "message"  # nothing to show for one track: none or several selected, a folder, not indexed
LOOKING_UP = "looking_up"
FOUND_STATE = "found"
INSTRUMENTAL_STATE = "instrumental"
NOT_FOUND_STATE = "not_found"
MISSING_TAGS = "missing_tags"
UNREADABLE = "unreadable"
ERROR = "error"
OFFLINE_STATE = "offline"
UNREACHABLE_STATE = "unreachable"
PAUSED_STATE = "paused"


TIMESTAMPS_TIP = "Show the time of each line in front of it, as LRCLIB's synced lyrics give it"

class LyricsTab(QWidget):
    urlRequested = Signal(str)  # a Genius or Google search, opened by the window

    def __init__(self, conn: Callable[[], sqlite3.Connection | None], service: LyricsService | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._conn = conn
        self.service = service
        self.today: Callable[[], date] = date.today  # replaced in tests
        self.entry_id: int | None = None
        self.info: LyricsInfo | None = None
        self.state = MESSAGE
        self._retrying: str | None = None  # a key asked again: shown as looking up until its result arrives
        self._service_state = ""

        self.spinner = QProgressBar()
        self.spinner.setRange(0, 0)  # busy
        self.spinner.setMaximumWidth(60)
        self.spinner.setTextVisible(False)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        status_row = QHBoxLayout()
        status_row.addWidget(self.spinner)
        status_row.addWidget(self.status, 1)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.timestamps = QCheckBox("Show timestamps")
        self.timestamps.setToolTip(TIMESTAMPS_TIP)
        self.timestamps.toggled.connect(self._show_text)
        self.source = QLabel()
        self.source.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        options = QHBoxLayout()
        options.addWidget(self.timestamps)
        options.addStretch(1)
        options.addWidget(self.source)

        self.copy_button = QPushButton("Copy")
        self.copy_button.clicked.connect(self.copy)
        self.copy_button.setToolTip("Copy the lyrics as shown here to the clipboard")
        self.genius_button = QPushButton("Search on Genius")
        self.genius_button.setToolTip("Search Genius for this track's lyrics in your web browser")
        self.genius_button.clicked.connect(lambda: self._search(0))
        self.google_button = QPushButton("Search on Google")
        self.google_button.setToolTip("Search Google for this track's lyrics in your web browser")
        self.google_button.clicked.connect(lambda: self._search(1))
        self.retry_button = QPushButton("Retry now")
        self.retry_button.setToolTip("Look this track up on LRCLIB again now")
        self.retry_button.clicked.connect(self.retry)
        buttons = QHBoxLayout()
        for button in (self.copy_button, self.genius_button, self.google_button, self.retry_button):
            buttons.addWidget(button)
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(status_row)
        layout.addWidget(self.text, 1)
        layout.addLayout(options)
        layout.addLayout(buttons)
        self.filler = QWidget()  # keeps a short state at the top; the lyrics text takes the room instead
        self.filler.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.filler, 1)

        if service is not None:
            service.entryReady.connect(self._entry_ready)
            service.progress.connect(self._progress)
            service.resumed.connect(self.refresh)
        self.show_message("Select a track to see its lyrics from LRCLIB.")

    # -- what is shown --

    def show_message(self, text: str) -> None:
        self.entry_id, self.info = None, None
        self._render(MESSAGE, text)

    def show_track(self, entry_id: int) -> None:
        if entry_id != self.entry_id:
            self._retrying = None
        self.entry_id = entry_id
        self.refresh()

    def refresh(self) -> None:
        """Re-read the shown track from the index and render its state."""
        if self.entry_id is None:
            return
        conn = self._conn()
        info = lyrics_info(conn, self.entry_id) if conn is not None else None
        self.info = info
        self._service_state = self.service.state if self.service is not None else OFFLINE
        if info is None:
            self._render(MESSAGE, "This file isn't in the library index yet.")
            return
        if info.read_error:
            self._render(UNREADABLE, "This file's tags can't be read, so its lyrics can't be looked up on LRCLIB.")
            return
        if info.key is None:
            self._render(MISSING_TAGS, "This file has no artist or title, in its tags or its file name, so it can't "
                                       "be looked up on LRCLIB.")
            return
        entry = info.entry
        status = entry.get("status") if entry else None
        error = self.service.last_error(info.key) if self.service is not None else None
        if self._retrying == info.key:
            self._render(LOOKING_UP, "Looking up on LRCLIB…")
        elif status == FOUND:
            self._render(FOUND_STATE, "")
        elif status == INSTRUMENTAL:
            self._render(INSTRUMENTAL_STATE, "LRCLIB lists this track as instrumental.")
        elif error and self._service_state not in (OFFLINE, UNREACHABLE):
            self._render(ERROR, f"The lookup failed ({error}). It is tried again later.")
        elif status == NOT_FOUND and entry is not None and is_fresh(entry, self.today()):
            self._render(NOT_FOUND_STATE, f"LRCLIB has no lyrics for this track (checked {entry['checked']}). "
                                          "It is looked up again after 30 days.")
        else:
            self._render_pending()

    def _render_pending(self) -> None:
        """No usable entry yet: why not, or the spinner."""
        state = self._service_state
        if self.service is None or state == OFFLINE:
            self._render(OFFLINE_STATE, "Lyrics lookups are off, so LRCLIB isn't contacted.")
        elif state == UNREACHABLE:
            resume = datetime.fromtimestamp(self.service.resume_at).strftime("%H:%M")
            self._render(UNREACHABLE_STATE, f"LRCLIB is unreachable. Lookups resume at {resume}.")
        elif state == PAUSED:
            self._render(PAUSED_STATE, "Lyrics lookups are paused.")
        else:
            self._render(LOOKING_UP, "Looking up on LRCLIB…")

    def _render(self, state: str, message: str) -> None:
        self.state = state
        found = state in (FOUND_STATE, INSTRUMENTAL_STATE)
        searches = state not in (MESSAGE, FOUND_STATE, UNREADABLE, LOOKING_UP)
        self.spinner.setVisible(state == LOOKING_UP)
        self.status.setText(message)
        self.status.setVisible(bool(message))
        self.text.setVisible(state == FOUND_STATE)
        self.filler.setVisible(state != FOUND_STATE)
        self.timestamps.setVisible(state == FOUND_STATE)
        self.source.setVisible(found)
        self.copy_button.setVisible(state == FOUND_STATE)
        self.genius_button.setVisible(searches)
        self.google_button.setVisible(searches)
        self.retry_button.setVisible(state in (NOT_FOUND_STATE, ERROR, UNREACHABLE_STATE))
        if found:
            _info, entry = self._found()
            self.source.setText(f"LRCLIB #{entry.get('lrclib_id')} · checked {entry.get('checked')}")
        if state == FOUND_STATE:
            _info, entry = self._found()
            synced = bool(entry.get("synced"))
            self.timestamps.setEnabled(synced)
            self.timestamps.setToolTip(TIMESTAMPS_TIP if synced else "LRCLIB has only plain lyrics for this track.")
            if not synced:
                self.timestamps.setChecked(False)
            self._show_text()

    def _show_text(self) -> None:
        if self.state != FOUND_STATE or self.info is None or self.info.entry is None:
            return
        entry = self.info.entry
        text = entry.get("synced") if self.timestamps.isChecked() and entry.get("synced") else entry.get("plain")
        self.text.setPlainText(text or "")

    def _found(self) -> tuple[LyricsInfo, dict]:
        """The track and its LRCLIB entry, in the states that show one (found, instrumental)."""
        assert self.info is not None and self.info.entry is not None
        return self.info, self.info.entry

    def shown_text(self) -> str:
        return self.text.toPlainText() if self.state == FOUND_STATE else ""

    def copy(self) -> None:
        if self.state == FOUND_STATE:
            QApplication.clipboard().setText(self.shown_text())

    def search_urls(self) -> tuple[str, str] | None:
        if self.info is None:
            return None
        return search_urls(self.info.track.artist, self.info.track.title)

    def _search(self, which: int) -> None:
        urls = self.search_urls()
        if urls:
            self.urlRequested.emit(urls[which])

    def retry(self) -> None:
        """Look the track up again now (it also ends the circuit breaker's pause)."""
        if self.service is None or self.info is None or self.info.key is None:
            return
        self._retrying = self.info.key
        self.service.retry(self.info.entry_id)
        self._service_state = self.service.state
        self._render(LOOKING_UP, "Looking up on LRCLIB…")

    # -- the service --

    def _entry_ready(self, key: str, _entry: dict) -> None:
        if self.info is None or key != self.info.key:
            return
        if self._retrying == key:
            self._retrying = None
        self.refresh()

    def _progress(self, progress: LyricsProgress) -> None:
        if self.info is not None and progress.state != self._service_state:
            self.refresh()
