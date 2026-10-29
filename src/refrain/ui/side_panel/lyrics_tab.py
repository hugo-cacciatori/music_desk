"""The Lyrics tab (SPEC §8): what LRCLIB returned for the selected track. The file's own lyrics tag plays no part
(M12, SPEC §26: no function relies on it).

Everything comes from the index on the UI's read connection, so showing a track reads no file. The tab follows the
lyrics service: a result for the shown key, a change of state (offline, paused, unreachable) or a retry re-renders
it. Opening the Genius and Google searches is left to the window, which launches off the UI thread.

The lyrics are for reading while listening (SPEC §33): large text, generous line spacing, and a text size the user
sets with A− / A+ (or Ctrl+− / Ctrl+= / Ctrl+0, Ctrl+wheel), which the window keeps between sessions.
"""

import sqlite3
from collections.abc import Callable
from datetime import date, datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QKeySequence, QTextBlockFormat, QTextCursor, QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from refrain.core.index.lyrics_index import LyricsInfo, lyrics_info
from refrain.core.lyrics import FOUND, INSTRUMENTAL, NOT_FOUND, is_fresh, search_urls
from refrain.services.lyrics_service import OFFLINE, PAUSED, UNREACHABLE, LyricsProgress, LyricsService

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
TEXT_SIZES = (9, 10, 11, 12, 13, 14, 16, 18, 20, 22, 24, 28, 32, 36)  # points, A− / A+ steps
DEFAULT_TEXT_SIZE = 14  # Settings.lyrics_text_size's default too
LINE_HEIGHT = 140  # percent of the font's line: room between the lines, for reading
TEXT_MARGIN = 12


class LyricsText(QTextEdit):
    """The lyrics, read-only. Ctrl+wheel and the zoom keys (Ctrl+= / Ctrl+−, Ctrl+0) step the text size as A− / A+
    do, so the size is always one the tab keeps (QTextEdit's own zoom would change it behind the setting)."""

    stepRequested = Signal(int)  # +1 larger, -1 smaller, 0 the default size

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt's name
        control = event.modifiers() & Qt.KeyboardModifier.ControlModifier
        if event.matches(QKeySequence.StandardKey.ZoomIn) or (control and event.key() == Qt.Key.Key_Equal):
            self.stepRequested.emit(1)
        elif event.matches(QKeySequence.StandardKey.ZoomOut):
            self.stepRequested.emit(-1)
        elif control and event.key() == Qt.Key.Key_0:
            self.stepRequested.emit(0)
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - Qt's name
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta:
                self.stepRequested.emit(1 if delta > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)


class LyricsTab(QWidget):
    urlRequested = Signal(str)  # a Genius or Google search, opened by the window
    textSizeChanged = Signal(int)  # points: the window keeps it (Settings.lyrics_text_size)

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

        self.text = LyricsText()
        self.text.setReadOnly(True)
        self.text.setAcceptRichText(False)
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse
                                          | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.text.document().setDocumentMargin(TEXT_MARGIN)
        self.text.stepRequested.connect(self.step_text_size)
        self.text_size = DEFAULT_TEXT_SIZE
        self.smaller = QToolButton()
        self.smaller.setText("A−")
        self.smaller.setToolTip("Smaller lyrics text (Ctrl+−)")
        self.smaller.clicked.connect(lambda: self.step_text_size(-1))
        self.larger = QToolButton()
        self.larger.setText("A+")
        self.larger.setToolTip("Larger lyrics text (Ctrl+=)")
        self.larger.clicked.connect(lambda: self.step_text_size(1))
        self.timestamps = QCheckBox("Show timestamps")
        self.timestamps.setToolTip(TIMESTAMPS_TIP)
        self.timestamps.toggled.connect(self._show_text)
        self.source = QLabel()
        self.source.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        options = QHBoxLayout()
        options.addWidget(self.timestamps)
        options.addSpacing(8)
        options.addWidget(self.smaller)
        options.addWidget(self.larger)
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
        self.set_text_size(DEFAULT_TEXT_SIZE)
        self.show_message("Select a track to see its lyrics from LRCLIB.")

    # -- the text size --

    def set_text_size(self, points: int, *, emit: bool = False) -> None:
        """The lyrics' size, the nearest of TEXT_SIZES (a hand-edited settings.json may hold any number)."""
        points = min(TEXT_SIZES, key=lambda size: (abs(size - points), size))
        changed = points != self.text_size
        self.text_size = points
        font = self.text.font()
        font.setPointSize(points)
        self.text.setFont(font)
        self.smaller.setEnabled(points > TEXT_SIZES[0])
        self.larger.setEnabled(points < TEXT_SIZES[-1])
        self._space_lines()
        if emit and changed:
            self.textSizeChanged.emit(points)

    def step_text_size(self, step: int) -> None:
        """One size larger (+1) or smaller (-1); 0 goes back to the default size."""
        if step == 0:
            self.set_text_size(DEFAULT_TEXT_SIZE, emit=True)
            return
        i = TEXT_SIZES.index(self.text_size) + step
        if 0 <= i < len(TEXT_SIZES):
            self.set_text_size(TEXT_SIZES[i], emit=True)

    def _space_lines(self) -> None:
        cursor = QTextCursor(self.text.document())
        cursor.select(QTextCursor.SelectionType.Document)
        block = QTextBlockFormat()
        block.setLineHeight(LINE_HEIGHT, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        cursor.mergeBlockFormat(block)

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
        self.smaller.setVisible(state == FOUND_STATE)
        self.larger.setVisible(state == FOUND_STATE)
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
        self._space_lines()

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
