"""The Metadata tab for one audio file (SPEC §7): the editable fields, a header with the cover and a format line,
and the read-only details and loudness values. Ctrl+S saves the changed fields only; Esc reverts.

Reads and writes go through TagIO on its worker thread; this widget only shows results.
"""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIntValidator, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from music_desk.core.metadata import Snapshot
from music_desk.core.tags import Fields
from music_desk.services.tag_io import Loaded, TagIO
from music_desk.services.tag_writer import TagWriter

TEXT_FIELDS = (("title", "Title"), ("artist", "Artist"), ("albumartist", "Album artist"), ("album", "Album"))
LATER_TEXT_FIELDS = (("year", "Year"), ("genre", "Genre"), ("composer", "Composer"), ("comment", "Comment"))
NUMBER_FIELDS = ("track", "track_total", "disc", "disc_total")


def _normalize_lyrics(text: str | None) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


class MetadataTab(QWidget):
    dirtyChanged = Signal(bool)
    fileSaved = Signal(str)  # path: its folder's index rows need a refresh
    saveFailed = Signal(str)

    def __init__(self, io: TagIO, writer: TagWriter, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.io = io
        self.writer = writer
        self.locked_by: str | None = None
        self.path: Path | None = None
        self.snapshot: Snapshot | None = None
        self._load_token: int | None = None
        self._save_token: int | None = None
        self._dirty = False
        self._filling = False
        self._cover_image = None
        io.loaded.connect(self._loaded)
        writer.written.connect(self._saved)
        writer.locksChanged.connect(self._update_lock)

        self.pages = QStackedWidget()
        self.message = QLabel()
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setWordWrap(True)
        self.pages.addWidget(self.message)
        self.pages.addWidget(self._build_form())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.pages)

        QShortcut(QKeySequence.StandardKey.Save, self, activated=self.save,
                  context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.revert,
                  context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.show_message("Select a file to see its tags.")

    # -- building --

    def _build_form(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(8, 8, 8, 8)

        header = QHBoxLayout()
        self.cover = QLabel()
        self.cover.setFixedSize(96, 96)
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.setFrameShape(QFrame.Shape.StyledPanel)
        names = QVBoxLayout()
        self.name_label = QLabel()
        self.name_label.setWordWrap(True)
        self.name_label.setStyleSheet("font-weight: 600;")
        self.format_label = QLabel()
        self.format_label.setWordWrap(True)
        self.cover_source = QLabel()
        names.addWidget(self.name_label)
        names.addWidget(self.format_label)
        names.addWidget(self.cover_source)
        names.addStretch(1)
        header.addWidget(self.cover)
        header.addLayout(names, 1)
        outer.addLayout(header)

        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.hide()
        outer.addWidget(self.note)

        form = QFormLayout()
        self.edits: dict[str, QLineEdit] = {}
        for key, label in TEXT_FIELDS:
            form.addRow(label, self._line(key))
        form.addRow("Track # / total", self._pair("track", "track_total"))
        form.addRow("Disc # / total", self._pair("disc", "disc_total"))
        for key, label in LATER_TEXT_FIELDS:
            form.addRow(label, self._line(key))
        self.lyrics = QPlainTextEdit()
        self.lyrics.setPlaceholderText("No lyrics in the tags. LRC timestamps are kept as typed.")
        self.lyrics.setMinimumHeight(120)
        self.lyrics.textChanged.connect(self._edited)
        form.addRow("Lyrics", self.lyrics)
        outer.addLayout(form)

        buttons = QHBoxLayout()
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #c42b1c;")
        self.error_label.hide()
        self.revert_button = QPushButton("Revert")
        self.revert_button.clicked.connect(self.revert)
        self.save_button = QPushButton("Save")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self.save)
        buttons.addWidget(self.error_label, 1)
        buttons.addWidget(self.revert_button)
        buttons.addWidget(self.save_button)
        outer.addLayout(buttons)

        details = QGridLayout()
        self.details: dict[str, QLabel] = {}
        rows = (("path", "Path"), ("size", "Size"), ("codec", "Codec"), ("duration", "Duration"),
                ("bitrate", "Bitrate"), ("sample_rate", "Sample rate"), ("bit_depth", "Bit depth"),
                ("channels", "Channels"), ("tags", "Tags"),
                ("rg_track", "Track gain / peak"), ("rg_album", "Album gain / peak"), ("lufs", "LUFS comment"))
        for row, (key, label) in enumerate(rows):
            value = QLabel()
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setWordWrap(True)
            details.addWidget(QLabel(label), row, 0, Qt.AlignmentFlag.AlignTop)
            details.addWidget(value, row, 1)
            self.details[key] = value
        details.setColumnStretch(1, 1)
        box = QFrame()
        box.setFrameShape(QFrame.Shape.StyledPanel)
        box.setLayout(details)
        outer.addWidget(QLabel("Details (read-only; the loudness values are managed by the ReplayGain tool)"))
        outer.addWidget(box)
        outer.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        return scroll

    def _line(self, key: str) -> QLineEdit:
        edit = QLineEdit()
        edit.textEdited.connect(self._edited)
        self.edits[key] = edit
        return edit

    def _pair(self, number: str, total: str) -> QWidget:
        box = QWidget()
        layout = QHBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        for key in (number, total):
            edit = self._line(key)
            edit.setValidator(QIntValidator(0, 9999, edit))
            edit.setMaximumWidth(70)
            layout.addWidget(edit)
            if key == number:
                layout.addWidget(QLabel("/"))
        layout.addStretch(1)
        return box

    # -- what is shown --

    @property
    def dirty(self) -> bool:
        return self._dirty

    def show_message(self, text: str) -> None:
        """No single audio file to edit (nothing selected, a folder, several files, or not audio)."""
        self.path = None
        self.snapshot = None
        self._load_token = None
        self.message.setText(text)
        self.pages.setCurrentWidget(self.message)
        self._set_dirty(False)

    def show_file(self, path: str | Path) -> None:
        self.path = Path(path)
        self.snapshot = None
        self.message.setText(f"Reading the tags of {self.path.name}…")
        self.pages.setCurrentWidget(self.message)
        self._set_dirty(False)
        self._load_token = self.io.load(self.path)

    def _loaded(self, result: Loaded) -> None:
        if result.token != self._load_token:
            return  # the selection moved on
        self._load_token = None
        if result.snapshot is None:
            self.message.setText(f"The tags of {self.path.name} couldn't be read.\n{result.error}")
            return
        self._fill(result.snapshot, result.cover)
        self.pages.setCurrentIndex(1)

    def _fill(self, snapshot: Snapshot, cover) -> None:
        self.snapshot = snapshot
        self._cover_image = cover
        self._filling = True
        fields = snapshot.fields
        for key, edit in self.edits.items():
            value = getattr(fields, key)
            edit.setText("" if value is None else str(value))
        self.lyrics.setPlainText(_normalize_lyrics(fields.lyrics))
        self._filling = False
        self.name_label.setText(snapshot.path.name)
        self.format_label.setText(snapshot.format_line)
        if cover is not None:
            self.cover.setPixmap(QPixmap.fromImage(cover).scaled(
                self.cover.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            self.cover.setPixmap(QPixmap())
            self.cover.setText("No cover")
        self.cover_source.setText({"embedded": "Embedded cover", "": ""}.get(
            snapshot.cover_source, f"Cover from {snapshot.cover_source}"))
        self.error_label.hide()
        self._fill_details(snapshot)
        self._set_dirty(False)
        self._update_lock()

    def _update_lock(self) -> None:
        """Files under a running job's lock are read-only, with the job named (SPEC §7)."""
        if self.snapshot is None:
            return
        self.locked_by = self.writer.locked_by(self.snapshot.path)
        editable = self.snapshot.editable and self.locked_by is None
        tooltip = f"Read-only while {self.locked_by} is running." if self.locked_by else ""
        for edit in [*self.edits.values(), self.lyrics]:
            edit.setReadOnly(not editable)
            edit.setEnabled(self.snapshot.editable)
            edit.setToolTip(tooltip)
        if not self.snapshot.editable:
            self.note.setText(f"{self.snapshot.container} files have no tags, so they can't be edited here.")
        elif self.locked_by:
            self.note.setText(f"Read-only while {self.locked_by} is running.")
        self.note.setVisible(not editable)
        self._set_dirty(self.dirty)

    def _fill_details(self, s: Snapshot) -> None:
        def rg(gain_key: str, peak_key: str) -> str:
            gain, peak = s.replaygain[gain_key], s.replaygain[peak_key]
            return "–" if gain is None and peak is None else f"{gain or '–'} / {peak or '–'}"

        values = {
            "path": str(s.path), "size": f"{s.size:,} bytes", "codec": s.codec,
            "duration": f"{s.duration:.2f} s" if s.duration else "–",
            "bitrate": f"{round(s.bitrate / 1000)} kbps" if s.bitrate else "–",
            "sample_rate": f"{s.sample_rate:,} Hz" if s.sample_rate else "–",
            "bit_depth": f"{s.bits_per_sample}-bit" if s.bits_per_sample and s.lossless else "–",
            "channels": str(s.channels or "–"),
            "tags": s.family + (f" (ID3v{s.id3_version})" if s.id3_version else ""),
            "rg_track": rg("replaygain_track_gain", "replaygain_track_peak"),
            "rg_album": rg("replaygain_album_gain", "replaygain_album_peak"),
            "lufs": s.lufs_comment or "–",
        }
        for key, value in values.items():
            self.details[key].setText(value)

    # -- editing --

    def changes(self) -> dict:
        """The fields that differ from the file: only these are written (None clears a field)."""
        if self.snapshot is None or not self.snapshot.editable:
            return {}
        fields: Fields = self.snapshot.fields
        changed = {}
        for key, edit in self.edits.items():
            text = edit.text()
            if key in NUMBER_FIELDS:
                value = int(text) if text.strip() else None
            else:
                value = text if text.strip() else None
            if value != getattr(fields, key):
                changed[key] = value
        lyrics = self.lyrics.toPlainText()
        if lyrics != _normalize_lyrics(fields.lyrics):
            changed["lyrics"] = lyrics if lyrics.strip() else None
        return changed

    def _edited(self, *_args) -> None:
        if not self._filling:
            self._set_dirty(bool(self.changes()))

    def _set_dirty(self, dirty: bool) -> None:
        if dirty != self._dirty:
            self._dirty = dirty
            self.dirtyChanged.emit(dirty)
        self.save_button.setEnabled(dirty and self._save_token is None and self.locked_by is None)
        self.revert_button.setEnabled(dirty)

    def revert(self) -> None:
        if self.snapshot is not None and self._save_token is None:
            self._fill(self.snapshot, self._cover_image)

    def save(self) -> None:
        changes = self.changes()
        if not changes or self.path is None or self._save_token is not None:
            return
        self.error_label.hide()
        self._save_token = self.writer.write(self.path, changes)
        self.save_button.setEnabled(False)
        self.save_button.setText("Saving…")

    def _saved(self, result: Loaded) -> None:
        if result.token != self._save_token:
            return
        self._save_token = None
        self.save_button.setText("Save")
        if result.snapshot is None:
            message = f"The tags couldn't be saved: {result.error}"
            if self.snapshot is not None:
                self.error_label.setText(message)
                self.error_label.show()
            self._set_dirty(self.dirty)  # the fields stay dirty
            self.saveFailed.emit(message)
            return
        if self.path == result.snapshot.path and self.pages.currentIndex() == 1:
            self._fill(result.snapshot, result.cover)
        self.fileSaved.emit(str(result.snapshot.path))

    @property
    def saving(self) -> bool:
        return self._save_token is not None

    # -- leaving with unsaved edits --

    def ask_leave(self) -> str:
        """'save', 'discard' or 'cancel' (a separate method so tests can answer it)."""
        name = self.path.name if self.path else "this file"
        box = QMessageBox(QMessageBox.Icon.Question, "Unsaved changes",
                          f"Save the tag changes to {name}?", parent=self)
        save = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save)
        box.exec()
        return "save" if box.clickedButton() is save else "discard" if box.clickedButton() is discard else "cancel"

    def can_leave(self) -> bool:
        """True if the selection may move on: no edits, or they were saved or discarded."""
        if not self.dirty:
            return True
        answer = self.ask_leave()
        if answer == "cancel":
            return False
        if answer == "save":
            self.save()  # the write goes on in the background; a failure is reported by saveFailed
        self._set_dirty(False)
        return True

