"""The split's preview and report (SPEC §22): "Split: check the tracks", always shown before anything is written.

One block per album: the source, the cue sheet with its encoding picker, the tracks it would write, and the
warnings or the reason it is left alone. After the run, the report says what each album became.
"""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from music_desk.core import cue
from music_desk.core.cue import AlbumPlan
from music_desk.core.split import SplitOptions
from music_desk.services.split_job import SplitFinished

COLUMNS = ("#", "Start", "Length", "New name", "Title", "Artist")


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def clock(samples: int, rate: int) -> str:
    seconds = samples / rate if rate else 0
    return f"{int(seconds // 60)}:{int(seconds % 60):02}"


def afterwards(options: SplitOptions) -> str:
    if not options.recycle:
        return "The album files and their cue sheets stay where they are."
    text = "The album file and its .cue go to the Recycle Bin once its tracks are verified."
    return text + " Then ReplayGain runs on each album." if options.replaygain else text


def short_result(finished: SplitFinished) -> str:
    """One line for the jobs panel (after its state word)."""
    if finished.error:
        return finished.error
    split = [a for a in finished.albums if a.split]
    if finished.cancelled:
        return f"{plural(len(split), 'album')} split, nothing left half-done"
    text = f"{plural(len(split), 'album')} split"
    problems = len(finished.albums) - len(split) + len(finished.refused)
    return text + (f", {plural(problems, 'album')} not split" if problems else "")


def report_text(finished: SplitFinished) -> str:
    split = [a for a in finished.albums if a.split]
    tracks = sum(len(a.finished.tracks) for a in split)
    if finished.error:
        lines = [f"The split failed: {finished.error}"]
    elif finished.cancelled:
        lines = [f"Stopped: {plural(len(split), 'album')} split, nothing left half-done."]
    elif not finished.albums and finished.refused:
        lines = ["Nothing was split."]
    else:
        lines = [f"Done: {plural(len(split), 'album')} split into {plural(tracks, 'track')}, all identical to "
                 "their originals."]
    for album in finished.albums:
        name = album.plan.cue.parent.name
        lines.append("")
        if album.cut is not None and album.cut.cancelled:
            lines.append(f"{name}: stopped before it finished; its partly written tracks were deleted.")
            continue
        if not album.split:
            reason = album.finished.error if album.finished else album.cut.error if album.cut else "not started"
            lines.append(f"{name}: not split. {reason}")
            continue
        lines.append(f"{name}: {plural(len(album.finished.tracks), 'track')} written, identical to the original "
                     f"(MD5 {album.cut.md5[:8]}…).")
        lines += [f"    {path.name}" for path in album.finished.tracks]
        if album.finished.recycled:
            lines.append("    The album file and its .cue are in the Recycle Bin.")
        elif album.finished.kept_reason:
            lines.append(f"    The originals stayed: {album.finished.kept_reason}")
        else:
            lines.append("    The originals stay where they are.")
        lines += [f"    Warning: {w}" for w in album.finished.warnings]
        if album.replaygain is not None:
            lines.append(f"    ReplayGain: {album.replaygain.result()}")
        elif album.replaygain_note:
            lines.append(f"    {album.replaygain_note}")
    if finished.refused:
        lines += ["", f"Left alone ({len(finished.refused)}):"]
        lines += [f"    {p.cue.relative_to(finished.folder) if p.cue.is_relative_to(finished.folder) else p.cue}: "
                  f"{p.refusal}" for p in finished.refused]
    return "\n".join(lines)


class AlbumBlock(QFrame):
    """One album in the preview."""

    encodingChanged = Signal(object, object)  # the .cue path, the codec (None: Auto)

    def __init__(self, plan: AlbumPlan, root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.encoding = QComboBox()
        self.encoding.setToolTip("The cue sheet's text encoding: pick another if the titles look garbled.")
        self.encoding.currentIndexChanged.connect(
            lambda _i: self.encodingChanged.emit(self.plan.cue, self.encoding.currentData()))
        top = QHBoxLayout()
        top.addWidget(self.title, 1)
        top.addWidget(QLabel("Cue sheet encoding:"))
        top.addWidget(self.encoding)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.notes = QLabel()
        self.notes.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.notes)
        layout.addWidget(self.table)
        self.root = root
        self.plan = plan
        self.codec: str | None = None  # the encoding picked (None: Auto)
        self.set_plan(plan)

    def set_plan(self, plan: AlbumPlan) -> None:
        self.plan = plan
        where = plan.cue.relative_to(self.root) if plan.cue.is_relative_to(self.root) else plan.cue
        source = ""
        if plan.info is not None:
            source = f" · {plan.source.name} ({plan.info.format}, {clock(plan.info.samples, plan.info.rate)})"
        self.title.setText(f"<b>{where}</b>{source}")
        self.encoding.blockSignals(True)
        self.encoding.clear()
        for codec, label in cue.ENCODINGS:
            auto = codec is None and plan.sheet is not None and self.codec is None
            self.encoding.addItem(f"Auto ({plan.sheet.encoding})" if auto else label, codec)
        self.encoding.setCurrentIndex(max(0, self.encoding.findData(self.codec)))
        self.encoding.setEnabled(plan.sheet is not None)
        self.encoding.blockSignals(False)
        notes = list(plan.warnings)
        if plan.refusal:
            notes.insert(0, f"Left alone: {plan.refusal}")
        self.notes.setText("\n".join(notes))
        self.notes.setStyleSheet("color: #c42b1c;" if plan.refusal else "")
        self.notes.setVisible(bool(notes))
        rate = plan.info.rate if plan.info else 0
        self.table.setRowCount(len(plan.tracks))
        for row, track in enumerate(plan.tracks):
            values = (str(track.number), clock(track.start, rate), clock(track.length, rate), track.name,
                      track.fields.get("title", ""), track.fields.get("artist", ""))
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.table.setVisible(bool(plan.tracks))
        self.table.setFixedHeight(self.table.horizontalHeader().height() + 4
                                  + sum(self.table.rowHeight(r) for r in range(len(plan.tracks))))


class SplitPreview(QWidget):
    backRequested = Signal()
    applyRequested = Signal()
    cancelRequested = Signal()
    doneRequested = Signal()
    encodingChanged = Signal(object, object)  # the .cue path, the codec

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = "idle"  # planning, preview, running, result, error
        self.plans: list[AlbumPlan] = []
        self.blocks: list[AlbumBlock] = []
        self.options = SplitOptions()
        self.folder: Path | None = None
        self.result: SplitFinished | None = None

        self.back_button = QPushButton("← Back to options")
        self.back_button.clicked.connect(self.backRequested)
        self.heading = QLabel()
        self.heading.setWordWrap(True)
        header = QHBoxLayout()
        header.addWidget(self.back_button)
        header.addWidget(self.heading, 1)
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setMaximumWidth(120)
        self.busy.setTextVisible(False)
        self.message = QLabel()
        self.message.setWordWrap(True)
        status_row = QHBoxLayout()
        status_row.addWidget(self.busy)
        status_row.addWidget(self.message, 1)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.albums = QWidget()
        self.albums_layout = QVBoxLayout(self.albums)
        self.albums_layout.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.albums)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.apply_button = QPushButton()
        self.apply_button.clicked.connect(self.applyRequested)
        self.back_bottom = QPushButton("← Back to options")
        self.back_bottom.clicked.connect(self.backRequested)
        self.cancel_button = QPushButton("Stop")
        self.cancel_button.clicked.connect(self.cancelRequested)
        self.done_button = QPushButton("Done")
        self.done_button.clicked.connect(self.doneRequested)
        buttons = QHBoxLayout()
        for button in (self.apply_button, self.back_bottom, self.cancel_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self.done_button)
        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addLayout(status_row)
        layout.addWidget(self.summary)
        layout.addWidget(self.scroll, 1)
        layout.addWidget(self.report, 1)
        layout.addLayout(buttons)
        self._show("idle", "")

    # -- states --

    def show_planning(self, folder: Path, options: SplitOptions) -> None:
        self.folder, self.options, self.result = folder, options, None
        self.heading.setText(f"<b>Split: check the tracks</b> · {folder}")
        self._set_plans([])
        self._show("planning", "Reading the cue sheets… Nothing is written until you split.")

    def show_plans(self, plans: list[AlbumPlan]) -> None:
        self._set_plans(plans)
        self._update_summary()

    def replace_plan(self, plan: AlbumPlan) -> None:
        """An album read again with another encoding."""
        for i, old in enumerate(self.plans):
            if old.cue == plan.cue:
                self.plans[i] = plan
                self.blocks[i].set_plan(plan)
        self._update_summary()

    def show_running(self) -> None:
        self._show("running", "Splitting… Files in this folder can't be edited meanwhile.")

    def set_progress(self, done: int, total: int, label: str) -> None:
        if self.state == "running" and label != "done":
            self.message.setText(f"Splitting ({done + 1} of {total}): {label}…")

    def show_result(self, finished: SplitFinished) -> None:
        self.result = finished
        self.report.setPlainText(report_text(finished))
        self._show("result", "Stopped." if finished.cancelled else "Done.")

    def show_error(self, text: str) -> None:
        self._show("error", text)

    def ready(self) -> list[AlbumPlan]:
        return [p for p in self.plans if p.refusal is None]

    def _set_plans(self, plans: list[AlbumPlan]) -> None:
        for block in self.blocks:
            block.setParent(None)
            block.deleteLater()
        self.plans, self.blocks = list(plans), []
        for plan in plans:
            block = AlbumBlock(plan, self.folder or plan.cue.parent)
            block.encodingChanged.connect(self._encoding_changed)
            self.albums_layout.insertWidget(self.albums_layout.count() - 1, block)
            self.blocks.append(block)

    def _encoding_changed(self, path: Path, codec) -> None:
        for block in self.blocks:
            if block.plan.cue == path:
                block.codec = codec
        self.encodingChanged.emit(path, codec)

    def _update_summary(self) -> None:
        ready = self.ready()
        refused = len(self.plans) - len(ready)
        tracks = sum(len(p.tracks) for p in ready)
        if not self.plans:
            self.summary.setText("")
            self._show("preview", "No cue sheet in this folder.")
            self.apply_button.setEnabled(False)
            return
        text = f"{plural(len(ready), 'album')} → {plural(tracks, 'track')}."
        if refused:
            text += f" {plural(refused, 'album')} left alone."
        self.summary.setText(f"{text} {afterwards(self.options)}")
        self.apply_button.setText(f"Split {plural(len(ready), 'album')}")
        self.apply_button.setEnabled(bool(ready))
        message = "Nothing has been written yet. Back to options changes nothing." if ready else \
            "No album here can be split; each one says why."
        self._show("preview", message)

    def _show(self, state: str, message: str) -> None:
        self.state = state
        self.message.setText(message)
        self.busy.setVisible(state in ("planning", "running"))
        preview = state == "preview"
        self.summary.setVisible(preview and bool(self.summary.text()))
        self.scroll.setVisible(preview)
        self.report.setVisible(state == "result")
        self.apply_button.setVisible(preview)
        self.back_bottom.setVisible(preview)
        self.back_button.setVisible(state != "running")
        self.cancel_button.setVisible(state in ("planning", "running"))
        self.done_button.setVisible(state in ("result", "error"))
