"""The cue editor's page (SPEC §22, §35): one cue sheet, every control, in the "Edit this cue sheet…" window. It
shows what the controller gives it and reports what the user does; the editing and saving live in
ui/controllers/split.py.

Top to bottom: the source (which audio file the sheet points to), the encoding picker, the album fields and their
other lines, the track table (the editable values, the computed length and new name, validation marks), the
selected track's other lines, Add and Delete, then Save, Save as… and Preview in player.

The page edits only (M20, Hugo 2026-10-17): splitting is "Split this cue sheet…"'s recap window
(context_menu/split_recap.py), from the right-click menu or the cue sheet's preview (SPEC §35).
"""

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from music_desk.context_menu.entries import OPTIONS_WHERE
from music_desk.core.cue import ENCODINGS, FRAMES_PER_SECOND, AlbumPlan
from music_desk.core.cue_edit import CueDocument, Problem, format_time
from music_desk.ui import theme
from music_desk.ui.hover import exempt
from music_desk.ui.qt_util import cell_text

# The table's columns: (key, header). Keys are cue_edit.TRACK_FIELDS' where the cell is editable.
COLUMNS = (("number", "#"), ("start", "Start"), ("gap", "Gap"), ("length", "Length"),
           ("title", "Title"), ("performer", "Performer"), ("songwriter", "Songwriter"), ("isrc", "ISRC"),
           ("flags", "Flags"), ("name", "New name"))
COLUMN = {key: i for i, (key, _header) in enumerate(COLUMNS)}
EDITABLE = ("start", "gap", "title", "performer", "songwriter", "isrc", "flags")
ALBUM_LABELS = (("performer", "Performer"), ("title", "Title"), ("genre", "Genre"), ("date", "Date"),
                ("disc", "Disc"), ("disc_total", "Total discs"), ("catalog", "Catalog"),
                ("songwriter", "Songwriter"))


def frames_text(samples: int, rate: int) -> str:
    return format_time(samples * FRAMES_PER_SECOND // rate) if rate else ""


SAVE_TIP = "Save the edits into this .cue; its old version goes to the Recycle Bin first"
NEW_SAVE_TIP = "Save this new cue sheet, beside its audio file unless you choose another place"

HEADER_TIPS = {  # the columns whose header doesn't say it all (SPEC §24)
    "start": "Where the track starts (INDEX 01), as mm:ss:ff, with 75 frames a second",
    "gap": "Where the gap before the track starts (INDEX 00); never a cut point",
    "length": "Computed from the starts",
    "flags": "The track's cue FLAGS (DCP, 4CH, PRE, SCMS)",
    "name": "The file name the track gets, from the File names masks",
}

class SplitPage(QWidget):
    saveRequested = Signal()
    saveAsRequested = Signal()
    doneRequested = Signal()
    encodingPicked = Signal(object)  # a codec, None for Auto
    sourcePicked = Signal(str)  # an audio file's name: the FILE line
    albumEdited = Signal(str, str)  # field, value
    albumOtherEdited = Signal(str)
    trackEdited = Signal(int, str, str)  # sheet row, field, value
    trackOtherEdited = Signal(int, str)  # sheet row, text
    addRequested = Signal(object)  # the sheet row to add after, or None
    fillRequested = Signal()  # Fill from track lengths…
    previewRequested = Signal()  # Preview in player
    deleteRequested = Signal(list)  # sheet rows

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = "empty"  # loading, editing, error
        self.rows: list[tuple[int | None, int]] = []  # per table row: (sheet row or None for a hidden track, number)
        self._refreshing = False
        self._table_stale = False  # a refresh came while a cell was being edited

        self.heading = QLabel()
        self.heading.setWordWrap(True)
        self.heading.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        header = QHBoxLayout()
        header.addWidget(self.heading, 1)

        # -- the source and the encoding --
        self.source = QComboBox()
        self.source.setToolTip("The audio file this cue sheet points to (its FILE line): a FLAC, WAV or AIFF file in "
                               "the .cue's folder.")
        self.source.activated.connect(
            lambda i: None if self._refreshing else self.sourcePicked.emit(self.source.itemData(i) or ""))
        self.source_note = QLabel()
        self.source_note.setWordWrap(True)
        self.encoding = QComboBox()
        self.encoding.setToolTip("The cue sheet's text encoding: pick another if the titles look garbled.")
        for codec, label in ENCODINGS:
            self.encoding.addItem(label, codec)
        self.encoding.activated.connect(lambda i: self.encodingPicked.emit(self.encoding.itemData(i)))
        self.encoding_note = QLabel("If the titles look wrong, pick the cue sheet's encoding.")
        self.encoding_note.hide()
        source_row = QGridLayout()
        source_row.addWidget(QLabel("Source:"), 0, 0)
        source_row.addWidget(self.source, 0, 1)
        source_row.addWidget(QLabel("Encoding:"), 0, 2)
        source_row.addWidget(self.encoding, 0, 3, 1, 2)
        source_row.addWidget(self.source_note, 1, 1)
        source_row.addWidget(self.encoding_note, 1, 2, 1, 3)
        source_row.setColumnStretch(1, 1)

        # -- the album --
        self.album_edits: dict[str, QLineEdit] = {}
        album = QGridLayout()
        for i, (key, label) in enumerate(ALBUM_LABELS):
            edit = QLineEdit()
            exempt(edit, "an album field of the sheet: its label says it all; a problem shows as its tooltip")
            edit.textEdited.connect(lambda text, key=key: self.albumEdited.emit(key, text))
            self.album_edits[key] = edit
            album.addWidget(QLabel(f"{label}:"), i // 2, (i % 2) * 2)
            album.addWidget(edit, i // 2, (i % 2) * 2 + 1)
        album.setColumnStretch(1, 1)
        album.setColumnStretch(3, 1)
        self.album_other = QPlainTextEdit()
        self.album_other.setToolTip("The album lines with no field of their own (REM DISCID, REM COMMENT, …).")
        self.album_other.setMaximumHeight(60)
        self.album_other.textChanged.connect(
            lambda: None if self._refreshing else self.albumOtherEdited.emit(self.album_other.toPlainText()))
        album.addWidget(QLabel("Other lines:"), 4, 0, Qt.AlignmentFlag.AlignTop)
        album.addWidget(self.album_other, 4, 1, 1, 3)

        # -- the tracks --
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([header for _key, header in COLUMNS])
        for key, tip in HEADER_TIPS.items():
            header_item = self.table.horizontalHeaderItem(COLUMN[key])
            if header_item is not None:
                header_item.setToolTip(tip)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                   | QAbstractItemView.EditTrigger.EditKeyPressed
                                   | QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for key, width in (("number", 32), ("start", 80), ("gap", 80), ("length", 80),
                           ("title", 200), ("performer", 140), ("songwriter", 110), ("isrc", 110), ("flags", 50)):
            self.table.setColumnWidth(COLUMN[key], width)
        self.table.itemChanged.connect(self._item_changed)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.track_other = QPlainTextEdit()
        self.track_other.setToolTip("The selected track's other lines (INDEX 02 and later, PREGAP, POSTGAP, REM, …).")
        self.track_other.setMaximumHeight(60)
        self.track_other.textChanged.connect(self._track_other_changed)
        self.add_button = QPushButton("Add track")
        self.add_button.setToolTip("A new track after the selected one, starting halfway through it.")
        self.add_button.clicked.connect(lambda: self.addRequested.emit(self._selected_rows()[-1]
                                                                       if self._selected_rows() else None))
        self.delete_button = QPushButton("Delete track")
        self.delete_button.setToolTip("Its audio joins the track before it (or the next one, for track 1).")
        self.delete_button.clicked.connect(lambda: self.deleteRequested.emit(self._selected_rows()))
        track_buttons = QHBoxLayout()
        track_buttons.addWidget(QLabel("Other lines of the selected track:"))
        track_buttons.addStretch(1)
        track_buttons.addWidget(self.add_button)
        track_buttons.addWidget(self.delete_button)
        self.fill_button = QPushButton("Fill from track lengths…")
        self.fill_button.setToolTip("Set the starts from each track's length, as a tracklist gives them.")
        self.fill_button.clicked.connect(self.fillRequested)
        track_buttons.addWidget(self.fill_button)
        self.problems = QLabel()
        self.problems.setWordWrap(True)
        theme.mark_danger(self.problems)
        self.notes = QLabel()
        self.notes.setWordWrap(True)

        # -- the buttons --
        self.save_button = QPushButton("Save")
        self.save_button.setToolTip(SAVE_TIP)
        self.save_button.clicked.connect(self.saveRequested)
        self.save_as_button = QPushButton("Save as…")
        self.save_as_button.setToolTip("Save the sheet as another .cue file; a file it replaces goes to the Recycle "
                                       "Bin first")
        self.save_as_button.clicked.connect(self.saveAsRequested)
        self.preview_button = QPushButton("Preview in player")
        self.preview_button.setToolTip("Open the sheet as it is now, unsaved edits included, in the default player.")
        self.preview_button.clicked.connect(self.previewRequested)
        self.save_note = QLabel()  # a new sheet saved beside its audio file at once (the Split options)
        self.save_note.setToolTip(f"Set in the Split options ({OPTIONS_WHERE}): new sheets are saved beside their "
                                  "audio file without the Save dialog")
        self.save_note.hide()
        self.new = False  # a new sheet, not saved yet

        self.editor = QWidget()
        editor = QVBoxLayout(self.editor)
        editor.setContentsMargins(0, 0, 0, 0)
        editor.addLayout(source_row)
        editor.addLayout(album)
        editor.addWidget(self.table, 1)
        editor.addLayout(track_buttons)
        editor.addWidget(self.track_other)
        editor.addWidget(self.problems)
        editor.addWidget(self.notes)

        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setMaximumWidth(120)
        self.busy.setTextVisible(False)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.done_button = QPushButton("Close")  # after an error: the window has nothing else to show
        self.done_button.setToolTip("Close this window")
        self.done_button.clicked.connect(self.doneRequested)
        buttons = QHBoxLayout()
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.save_note)
        for button in (self.save_as_button, self.preview_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self.busy)
        buttons.addWidget(self.done_button)

        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(self.message)
        layout.addWidget(self.editor, 1)
        layout.addLayout(buttons)
        self._show("empty", "")

    # -- states --

    def show_loading(self, path) -> None:
        self.heading.setText(f"<b>Cue sheet</b> · {path}")
        self._show("loading", "Reading the cue sheet…")

    def show_error(self, text: str) -> None:
        self._show("error", text)

    def _show(self, state: str, message: str) -> None:
        self.state = state
        self.message.setText(message)
        self.message.setVisible(bool(message))
        editing = state == "editing"
        self.editor.setVisible(editing)
        self.busy.setVisible(state == "loading")
        for button in (self.save_button, self.save_as_button, self.preview_button):
            button.setVisible(editing)
        self.save_note.setVisible(editing and self.new and bool(self.save_note.text()))
        self.done_button.setVisible(state == "error")

    # -- what the controller shows --

    def show_document(self, doc: CueDocument, path, auto_label: str | None) -> None:
        """The sheet's values, after it was read or edited. Fields being typed in keep their cursor."""
        self.heading.setText(f"<b>Cue sheet</b> · {path}")
        self._refreshing = True
        try:
            album = doc.album()
            for key, edit in self.album_edits.items():
                _set_text(edit, album[key])
            if self.album_other.toPlainText() != album["other"]:
                self.album_other.setPlainText(album["other"])
            self.encoding.setItemText(0, f"Auto ({auto_label})" if auto_label else "Auto")
            self.encoding_note.setVisible(doc.fell_back)
        finally:
            self._refreshing = False
        if self.state != "editing":
            self._show("editing", "")

    def show_sources(self, names: tuple[str, ...], current: str, problem: str | None) -> None:
        """The audio files beside the sheet; the FILE line's own name is listed too when it isn't one of them,
        marked with `problem` ("not found", "not supported")."""
        self._refreshing = True
        try:
            self.source.clear()
            if current and not any(n.casefold() == current.casefold() for n in names):
                self.source.addItem(f"{current} ({problem or 'not found'})", current)
            elif not current:
                self.source.addItem("(no audio file)", "")
            for name in names:
                self.source.addItem(name, name)
            index = next((i for i in range(self.source.count())
                          if (self.source.itemData(i) or "").casefold() == current.casefold()), 0)
            self.source.setCurrentIndex(index)
        finally:
            self._refreshing = False

    def set_new(self, new: bool, audio_name: str = "", save_beside: bool = False) -> None:
        """A new sheet (New cue sheet…): the encoding is UTF-8 until saved, and with the Options window's Split box
        on, Save says it saves beside the audio file."""
        self.new = new
        self.encoding.setEnabled(not new)
        beside = f"Saves beside {audio_name} (the Split options)" if new and save_beside else ""
        self.save_button.setToolTip(beside or (NEW_SAVE_TIP if new else SAVE_TIP))
        self.save_note.setText(beside)
        self.save_note.setVisible(bool(beside) and self.state == "editing")

    def set_encoding(self, codec) -> None:
        self.encoding.setCurrentIndex(max(0, self.encoding.findData(codec)))

    def show_tracks(self, doc: CueDocument, plan: AlbumPlan | None, problems: list[Problem],
                    hidden: bool = False) -> None:
        """The table: the sheet's tracks, a hidden track's row 0 (`hidden`: the last plan that had tracks found
        one, so the rows don't move while a bad value is fixed), the plan's lengths and names, and each bad cell
        marked with its reason."""
        if self.table.state() == QAbstractItemView.State.EditingState:
            self._table_stale = True
            self._pending = (doc, plan, problems, hidden)
            return
        self._table_stale = False
        views = doc.tracks()
        planned = {t.row: t for t in plan.tracks} if plan is not None else {}
        rows: list[tuple[int | None, int]] = [(None, 0)] if hidden else []
        rows += [(v.row, v.number if v.number is not None else v.row + 1) for v in views]
        rate = plan.info.rate if plan is not None and plan.info is not None else 0
        marks: dict[tuple[int | None, str], list[str]] = {}
        for problem in problems:
            if problem.row is not None:
                marks.setdefault((problem.row, problem.column), []).append(problem.message)
        selected = self._selected_rows()
        self._refreshing = True
        self.table.blockSignals(True)
        try:
            self.rows = rows
            self.table.setRowCount(len(rows))
            for r, (sheet_row, number) in enumerate(rows):
                track = planned.get(sheet_row)
                values = views[sheet_row].values if sheet_row is not None else {}
                cells = {
                    "number": str(number),
                    "start": values.get("start", frames_text(track.start, rate) if track else ""),
                    "gap": values.get("gap", ""),
                    "length": frames_text(track.length, rate) if track and rate else "",
                    "title": values.get("title", track.fields.get("title", "") if track else ""),
                    "performer": values.get("performer", ""),
                    "songwriter": values.get("songwriter", ""),
                    "isrc": values.get("isrc", ""),
                    "flags": values.get("flags", ""),
                    "name": track.name if track else "",
                }
                for key, text in cells.items():
                    item = QTableWidgetItem(text)
                    flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                    if key in EDITABLE and sheet_row is not None:
                        flags |= Qt.ItemFlag.ItemIsEditable
                    item.setFlags(flags)
                    reasons = marks.get((sheet_row, key), []) if sheet_row is not None else []
                    if reasons:
                        item.setBackground(QBrush(theme.invalid_background()))
                        item.setToolTip("\n".join(reasons))
                    elif key == "name" and track is not None:
                        note = next((n for num, n in (plan.notes if plan else []) if num == track.number), "")
                        item.setToolTip(note)
                    self.table.setItem(r, COLUMN[key], item)
            self._select_rows(selected)
        finally:
            self.table.blockSignals(False)
            self._refreshing = False
        self._selection_changed(doc)

    def show_problems(self, problems: list[Problem], plan: AlbumPlan | None, source_text: str,
                      source_bad: bool) -> None:
        """The album fields' marks, the line under the table, the plan's notes and the source line."""
        album_marks: dict[str, list[str]] = {}
        for problem in problems:
            if problem.row is None and problem.column in self.album_edits:
                album_marks.setdefault(problem.column, []).append(problem.message)
        for key, edit in self.album_edits.items():
            theme.set_invalid(edit, key in album_marks)
            edit.setToolTip("\n".join(album_marks.get(key, [])))
        messages = list(dict.fromkeys(p.message for p in problems))
        if plan is not None:
            messages += [message for _number, message in plan.problems]
        self.problems.setText("\n".join(messages[:6]) + ("\n…" if len(messages) > 6 else ""))
        self.problems.setVisible(bool(messages))
        notes = [f"Track {number}: {note}" for number, note in plan.notes] if plan is not None else []
        if plan is not None:
            notes += plan.warnings
        self.notes.setText("\n".join(notes))
        self.notes.setVisible(bool(notes))
        # A source the split can't use draws the eye: the control is marked like a bad cell, the reason in bold.
        self.source_note.setText(f"<b>{html.escape(source_text)}</b>" if source_bad else source_text)
        theme.mark_danger(self.source_note, source_bad)
        theme.set_invalid(self.source, source_bad)

    def set_buttons(self, *, save: bool, save_as: bool, can_delete: bool, preview: bool = False,
                    fill: bool = False) -> None:
        self.save_button.setEnabled(save)
        self.preview_button.setEnabled(preview)
        self.fill_button.setEnabled(fill)
        self.save_as_button.setEnabled(save_as)
        self.delete_button.setEnabled(can_delete)

    # -- what the user does --

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if self._refreshing or item.row() >= len(self.rows):
            return
        sheet_row, _number = self.rows[item.row()]
        key = COLUMNS[item.column()][0]
        if key in EDITABLE and sheet_row is not None:
            self.trackEdited.emit(sheet_row, key, item.text())
        if self._table_stale:
            self.show_tracks(*self._pending)

    def _selected_rows(self) -> list[int]:
        """The selected rows' sheet rows (a hidden track has none)."""
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        return [row for r in rows if r < len(self.rows) and (row := self.rows[r][0]) is not None]

    def _select_rows(self, sheet_rows: list[int]) -> None:
        selection = self.table.selectionModel()
        selection.clearSelection()
        flags = selection.SelectionFlag.Select | selection.SelectionFlag.Rows
        for r, (sheet_row, _number) in enumerate(self.rows):
            if sheet_row in sheet_rows:
                selection.select(self.table.model().index(r, 0), flags)

    def _selection_changed(self, doc: CueDocument | None = None) -> None:
        self._doc = doc if isinstance(doc, CueDocument) else getattr(self, "_doc", None)
        rows = self._selected_rows()
        self._refreshing = True
        try:
            if len(rows) == 1 and self._doc is not None and rows[0] < len(self._doc.tracks()):
                text = "\n".join(line.strip() for line in self._doc.tracks()[rows[0]].other.split("\n"))
                if self.track_other.toPlainText() != text:
                    self.track_other.setPlainText(text)
                self.track_other.setEnabled(True)
            else:
                self.track_other.setPlainText("")
                self.track_other.setEnabled(False)
        finally:
            self._refreshing = False

    def _track_other_changed(self) -> None:
        rows = self._selected_rows()
        if not self._refreshing and len(rows) == 1:
            self.trackOtherEdited.emit(rows[0], self.track_other.toPlainText())

    # -- for tests --

    def cell(self, row: int, key: str) -> QTableWidgetItem:
        item = self.table.item(row, COLUMN[key])
        assert item is not None  # every cell of a shown track has an item
        return item

    def column_texts(self, key: str) -> list[str]:
        return [cell_text(self.table, r, COLUMN[key]) for r in range(self.table.rowCount())]


def _set_text(edit: QLineEdit, text: str) -> None:
    if edit.text() != text:
        edit.setText(text)
