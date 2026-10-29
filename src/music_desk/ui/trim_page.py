"""The Trim page (SPEC §23): one track, its waveform with two crop handles, the Start and End fields, the subfolder,
Preview in player, Trim and Stop, then the report. It shows what the controller gives it and reports what the user
does; the plan, the preview and the job live in ui/controllers/trim.py.

The handles and the fields are kept in step here, both ways and live. The waveform is painted from peaks computed
once on a worker (core/waveform.py), fitted to the widget's width only when the width changes, so dragging a
handle or resizing never decodes again (Ground rule 2).
"""

from PySide6.QtCore import QEvent, QLineF, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPen, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from music_desk.core import trim, waveform
from music_desk.core.trim import TrimPlan
from music_desk.core.waveform import Peaks
from music_desk.ui import theme

HANDLE_GRAB_PX = 8  # how near a handle a press must be to drag it
WAVEFORM_HEIGHT = 70  # px; filling the page, it was much too big (Hugo, M16)


TIPS = {  # the fields' hover texts, shown while they have no problem to tell (SPEC §24)
    "start": "Where the copy starts, as m:ss.mmm; Up and Down move it by 10 ms, Page Up and Page Down by 1 s",
    "end": "Where the copy ends, as m:ss.mmm; Up and Down move it by 10 ms, Page Up and Page Down by 1 s",
    "subfolder": "The copy goes in this subfolder of the track's folder, created if missing; the original isn't "
                 "changed",
}

class WaveformView(QWidget):
    """The whole track's waveform, with a start and an end handle; outside them is dimmed. Positions are in
    milliseconds, as the fields show them."""

    startMoved = Signal(int)  # ms, while dragging
    endMoved = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(WAVEFORM_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.total_ms = 0
        self.start_ms = 0
        self.end_ms = 0
        self.peaks: Peaks | None = None
        self.message = ""
        self._columns: list[tuple[float, float]] = []  # the peaks fitted to the current width
        self._picture: QPixmap | None = None  # the waveform drawn once per size; a drag only draws the handles
        self._dragging: str | None = None  # "start" or "end"

    # -- what the page sets --

    def set_track(self, total_ms: int) -> None:
        self.total_ms, self.start_ms, self.end_ms = total_ms, 0, total_ms
        self.peaks, self._columns, self._picture = None, [], None
        self.message = "Reading the audio…"
        self.update()

    def set_peaks(self, peaks: Peaks | None, message: str = "") -> None:
        self.peaks, self.message = peaks, message
        self._fit()
        self.update()

    def set_range(self, start_ms: int | None, end_ms: int | None) -> None:
        """A field was typed: its handle follows (clamped to the track; a bad value leaves it where it is)."""
        if start_ms is not None:
            self.start_ms = max(0, min(start_ms, self.total_ms))
        if end_ms is not None:
            self.end_ms = max(0, min(end_ms, self.total_ms))
        self.update()

    # -- geometry --

    def _fit(self) -> None:
        self._columns = waveform.fit(self.peaks, max(1, self.width())) if self.peaks is not None else []
        self._picture = None

    def x_of(self, ms: int) -> float:
        return ms * (self.width() - 1) / self.total_ms if self.total_ms else 0.0

    def ms_at(self, x: float) -> int:
        if self.width() <= 1 or not self.total_ms:
            return 0
        return max(0, min(self.total_ms, round(x * self.total_ms / (self.width() - 1))))

    def resizeEvent(self, event: QResizeEvent) -> None:
        self._fit()
        super().resizeEvent(event)

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.PaletteChange:  # a light or dark theme: draw the waveform again
            self._picture = None
        super().changeEvent(event)

    # -- painting --

    def _draw_waveform(self) -> QPixmap:
        """The peaks as one vertical line a pixel column, centred, at the widget's current size."""
        picture = QPixmap(self.size())
        picture.fill(self.palette().base().color())
        painter = QPainter(picture)
        painter.setPen(QPen(self.palette().highlight().color(), 1))
        middle = self.height() / 2
        half = middle - 2
        painter.drawLines([QLineF(x + 0.5, middle - high * half, x + 0.5, max(middle - low * half,
                                                                              middle - high * half + 1))
                           for x, (low, high) in enumerate(self._columns)])
        painter.end()
        return picture

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        palette = self.palette()
        width, height = self.width(), self.height()
        middle = height / 2
        if self._columns:
            if self._picture is None or self._picture.size() != self.size():
                self._picture = self._draw_waveform()
            painter.drawPixmap(0, 0, self._picture)
        else:
            painter.fillRect(self.rect(), palette.base())
            painter.setPen(palette.placeholderText().color())
            painter.drawLine(QPointF(0, middle), QPointF(width, middle))
            if self.message:
                painter.drawText(self.rect().adjusted(8, 8, -8, -8),
                                 Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message)
        if self.total_ms:
            dim = QColor(palette.window().color())
            dim.setAlpha(170)
            left, right = self.x_of(self.start_ms), self.x_of(self.end_ms)
            painter.fillRect(QRectF(0, 0, left, height), dim)
            painter.fillRect(QRectF(right, 0, width - right, height), dim)
            handle = palette.text().color()
            for x in (left, right):
                painter.setPen(QPen(handle, 2))
                painter.drawLine(QPointF(x, 0), QPointF(x, height))
                tab = max(0.0, min(x - 4, width - 8))  # the grab tabs stay inside at the edges
                painter.fillRect(QRectF(tab, 0, 8, 10), handle)
                painter.fillRect(QRectF(tab, height - 10, 8, 10), handle)
        painter.end()

    # -- dragging --

    def _handle_near(self, x: float) -> str:
        """The handle a press at `x` takes: the nearer one (the start when they meet and the press is left)."""
        left, right = self.x_of(self.start_ms), self.x_of(self.end_ms)
        if abs(x - left) == abs(x - right):
            return "start" if x <= left else "end"
        return "start" if abs(x - left) < abs(x - right) else "end"

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self.total_ms or not self.isEnabled():
            return
        self._dragging = self._handle_near(event.position().x())
        self._move_to(event.position().x())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        x = event.position().x()
        if self._dragging:
            self._move_to(x)
            return
        near = min(abs(x - self.x_of(self.start_ms)), abs(x - self.x_of(self.end_ms))) <= HANDLE_GRAB_PX
        self.setCursor(Qt.CursorShape.SizeHorCursor if near else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._dragging = None

    def _move_to(self, x: float) -> None:
        """A handle can't pass the other one: they stay at least a millisecond apart."""
        ms = self.ms_at(x)
        if self._dragging == "start":
            ms = min(ms, self.end_ms - 1)
            if ms != self.start_ms and ms >= 0:
                self.start_ms = ms
                self.update()
                self.startMoved.emit(ms)
        elif self._dragging == "end":
            ms = max(ms, self.start_ms + 1)
            if ms != self.end_ms and ms <= self.total_ms:
                self.end_ms = ms
                self.update()
                self.endMoved.emit(ms)


class TimeEdit(QLineEdit):
    """`m:ss.mmm`, typed directly. Up / Down nudge by 10 ms, Page Up / Page Down by 1 s, within the track."""

    nudged = Signal(str)  # the new text, after a key changed it

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.total_ms = 0
        self.setPlaceholderText("m:ss.mmm")
        self.setMaximumWidth(110)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        steps = {Qt.Key.Key_Up: trim.NUDGE_MS, Qt.Key.Key_Down: -trim.NUDGE_MS,
                 Qt.Key.Key_PageUp: trim.PAGE_MS, Qt.Key.Key_PageDown: -trim.PAGE_MS}
        step = steps.get(Qt.Key(event.key()))
        if step is None:
            super().keyPressEvent(event)
            return
        current = trim.parse_time(self.text())
        if current is None:
            return
        text = trim.format_time(max(0, min(self.total_ms, current + step)))
        if text != self.text():
            self.setText(text)
            self.nudged.emit(text)


class TrimPage(QWidget):
    backRequested = Signal()
    doneRequested = Signal()
    trimRequested = Signal()
    stopRequested = Signal()
    previewRequested = Signal()
    valuesEdited = Signal()  # Start, End or the subfolder changed: plan again

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = "empty"  # loading, editing, running, result, error

        self.back_button = QPushButton("Close")
        self.back_button.setToolTip("Close this window without trimming; the times you chose aren't kept")
        self.back_button.clicked.connect(self.backRequested)
        self.heading = QLabel()
        self.heading.setWordWrap(True)
        self.heading.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        header = QHBoxLayout()
        header.addWidget(self.back_button)
        header.addWidget(self.heading, 1)
        self.format_label = QLabel()

        self.waveform = WaveformView()
        self.waveform.setToolTip("Drag the two handles to where the copy starts and ends; a click moves the "
                                 "nearer handle")
        self.waveform.startMoved.connect(lambda ms: self._handle_moved(self.start_edit, ms))
        self.waveform.endMoved.connect(lambda ms: self._handle_moved(self.end_edit, ms))

        self.start_edit = TimeEdit()
        self.end_edit = TimeEdit()
        for edit in (self.start_edit, self.end_edit):
            edit.textEdited.connect(self._time_typed)
            edit.nudged.connect(self._time_typed)
        self.start_edit.setToolTip(TIPS["start"])
        self.end_edit.setToolTip(TIPS["end"])
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        times = QGridLayout()
        times.addWidget(QLabel("Start:"), 0, 0)
        times.addWidget(self.start_edit, 0, 1)
        times.addWidget(QLabel("End:"), 0, 2)
        times.addWidget(self.end_edit, 0, 3)
        times.addWidget(self.summary, 1, 0, 1, 5)
        times.setColumnStretch(4, 1)

        self.subfolder_name = trim.DEFAULT_SUBFOLDER  # … > Options…, Trim (SPEC §12)
        self.subfolder = QLabel()
        self.subfolder.setToolTip(TIPS["subfolder"])
        self.copy_path = QLabel()
        self.copy_path.setWordWrap(True)
        self.copy_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        subfolder = QGridLayout()
        subfolder.addWidget(self.subfolder, 0, 0, 1, 3)
        subfolder.addWidget(self.copy_path, 1, 0, 1, 3)
        subfolder.setColumnStretch(2, 1)

        self.editor = QWidget()
        editor = QVBoxLayout(self.editor)
        editor.setContentsMargins(0, 0, 0, 0)
        editor.addWidget(self.format_label)
        editor.addWidget(self.waveform)
        editor.addLayout(times)
        editor.addLayout(subfolder)
        editor.addStretch(1)  # the spare height goes below the fields

        self.message = QLabel()
        self.message.setWordWrap(True)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setMaximumWidth(120)
        self.busy.setTextVisible(False)
        self.preview_button = QPushButton("Preview in player")
        self.preview_button.setToolTip("Open the cut start, the kept part and the cut end as three tracks in your "
                                       "default player, to hear the cuts before trimming")
        self.preview_button.clicked.connect(self.previewRequested)
        self.trim_button = QPushButton("Trim")
        self.trim_button.setToolTip("Write the kept part as a FLAC copy in the subfolder; the original isn't changed")
        self.trim_button.clicked.connect(self.trimRequested)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setToolTip("Stop the trim; nothing is left half-written")
        self.stop_button.clicked.connect(self.stopRequested)
        self.done_button = QPushButton("Done")
        self.done_button.setToolTip("Close this window")
        self.done_button.clicked.connect(self.doneRequested)
        self.trim_note = QLabel()
        self.trim_note.setWordWrap(True)
        theme.mark_danger(self.trim_note)
        buttons = QHBoxLayout()
        for button in (self.preview_button, self.trim_button, self.stop_button):
            buttons.addWidget(button)
        buttons.addWidget(self.trim_note, 1)
        buttons.addWidget(self.busy)
        buttons.addWidget(self.done_button)

        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(self.message)
        layout.addWidget(self.editor, 1)
        layout.addWidget(self.report, 1)
        layout.addLayout(buttons)
        self._show("empty", "")

    # -- states --

    def show_loading(self, path) -> None:
        self.heading.setText(f"<b>Trim</b> · {path}")
        self.report.clear()
        self._show("loading", "Reading the track…")

    def show_error(self, text: str) -> None:
        self._show("error", text)

    def show_running(self, name: str) -> None:
        self._show("running", f"Trimming {name}… Files in this folder can't be edited meanwhile.")

    def show_result(self, text: str, heading: str | None = None) -> None:
        if heading:
            self.heading.setText(heading)
        self.report.setPlainText(text)
        self._show("result", "")

    def _show(self, state: str, message: str) -> None:
        self.state = state
        self.message.setText(message)
        self.message.setVisible(bool(message))
        editing = state in ("editing", "running")
        self.editor.setVisible(editing)
        self.editor.setEnabled(state == "editing")
        self.report.setVisible(state == "result")
        self.busy.setVisible(state in ("loading", "running"))
        self.preview_button.setVisible(editing)
        self.trim_button.setVisible(editing)
        self.trim_note.setVisible(state == "editing")
        self.stop_button.setVisible(state == "running")
        self.done_button.setVisible(state in ("result", "error"))
        self.back_button.setVisible(state not in ("running", "result"))  # while it runs: Stop; then Done

    # -- what the controller shows --

    def show_track(self, path, format_line: str, total_ms: int, subfolder: str) -> None:
        """A track read: its values from scratch (the two times are the page's only state)."""
        self.heading.setText(f"<b>Trim</b> · {path}")
        self.format_label.setText(f"<b>{path.name}</b> · {format_line}")
        for name, edit in (("start", self.start_edit), ("end", self.end_edit)):
            edit.total_ms = total_ms
            theme.set_invalid(edit, False)
            edit.setToolTip(TIPS[name])
        self.start_edit.setText(trim.format_time(0))
        self.end_edit.setText(trim.format_time(total_ms))
        self.subfolder_name = subfolder
        self.subfolder.setText(f"Subfolder: {subfolder} (… > Options…, Trim)")
        theme.set_invalid(self.subfolder, False)
        self.waveform.set_track(total_ms)
        self._show("editing", "")

    def show_plan(self, plan: TrimPlan) -> None:
        """The marks, the summary, the copy's path and the reasons Trim is disabled."""
        for name, edit in (("start", self.start_edit), ("end", self.end_edit), ("subfolder", self.subfolder)):
            why = plan.problem(name)
            theme.set_invalid(edit, bool(why))
            edit.setToolTip(why or TIPS[name])  # a problem's reason first (SPEC §24)
        why = plan.problem("subfolder")
        line = f"Subfolder: {self.subfolder_name} (… > Options…, Trim)"
        self.subfolder.setText(f"{line}: {why}" if why else line)
        self.summary.setText(plan.summary if plan.times_ok and plan.length > 0 else "")
        if plan.name:
            path = f"The copy: {plan.shown_path}"
            self.copy_path.setText(f"{path}\n{plan.note}" if plan.note else path)
        else:
            self.copy_path.setText("")

    def set_buttons(self, *, trim_enabled: bool, preview: bool, note: str) -> None:
        self.trim_button.setEnabled(trim_enabled)
        self.preview_button.setEnabled(preview)
        self.trim_note.setText(note)

    def values(self) -> tuple[str, str, str]:
        return self.start_edit.text(), self.end_edit.text(), self.subfolder_name

    # -- the handles and the fields, in step --

    def _handle_moved(self, edit: TimeEdit, ms: int) -> None:
        edit.setText(trim.format_time(ms))
        self.valuesEdited.emit()

    def _time_typed(self, _text: str = "") -> None:
        self.waveform.set_range(trim.parse_time(self.start_edit.text()), trim.parse_time(self.end_edit.text()))
        self.valuesEdited.emit()
