"""The Metadata tab (SPEC §7): one audio file, or several at once.

- One file: the editable fields, a header with the cover and a format line, and the read-only details and
  loudness values. Saving writes the changed fields only.
- Several files: the shared fields show their common value, or "(multiple values)" when the files differ, and
  are left alone unless edited. A clear button per field tells "clear on all" apart from "leave alone". The
  per-track fields (Title, Track #, Lyrics) are edited one file at a time and are disabled.

Ctrl+S saves and Esc reverts. Reads go through TagIO and writes through TagWriter, both off the UI thread.
"""

import html
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QIntValidator, QKeySequence
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from refrain.core import trim
from refrain.core.formats import TagFamily
from refrain.core.metadata import Snapshot
from refrain.core.tag_edits import (
    MULTIPLE,
    PER_TRACK_FIELDS,
    SHARED_FIELDS,
    common_values,
    multi_changes,
    single_changes,
)
from refrain.services.tag_io import Loaded, LoadedMany, TagIO
from refrain.services.tag_writer import BatchWritten, TagWriter
from refrain.ui import theme
from refrain.ui.hover import exempt
from refrain.ui.qt_util import shortcut, standard_icon
from refrain.ui.side_panel.cover_editor import CoverEditor

TEXT_FIELDS = (("title", "Title"), ("artist", "Artist"), ("albumartist", "Album artist"), ("album", "Album"))
LATER_TEXT_FIELDS = (("year", "Year"), ("genre", "Genre"), ("composer", "Composer"), ("comment", "Comment"))
WMA_TRACK_TOTAL = "WMA has no standard field for the track total, so it can't be set on .wma files."
MULTIPLE_TEXT = "(multiple values)"
CLEARED_TEXT = "(cleared on all files)"


TRIM_TIP = "Write a copy of this track without its start or its end, in a subfolder; the original isn't changed"
NEW_CUE_TIP = "Start a cue sheet for this file on the Split page, to cut it into tracks"

class MetadataTab(QWidget):
    dirtyChanged = Signal(bool)
    fileSaved = Signal(str)  # one file saved
    filesSaved = Signal(int)  # a multi-file save: how many files were saved
    saveFailed = Signal(str)
    renameSuggested = Signal(str, str)  # path, new name: the hint after a save (SPEC §7)
    trimRequested = Signal(str)  # Trim… on one audio file (SPEC §23)
    newCueRequested = Signal(str)  # New cue sheet… on one audio file (SPEC §22)

    def __init__(self, io: TagIO, writer: TagWriter, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.io = io
        self.writer = writer
        self.locked_by: str | None = None
        self.trim_unavailable: str | None = None  # flac's self-check failed
        self.path: Path | None = None  # one file
        self.paths: list[Path] = []  # several files
        self.snapshot: Snapshot | None = None  # one file
        self.snapshots: list[Snapshot] = []  # several files (readable ones)
        self.multi = False
        self._common: dict[str, object] = {}
        self._touched: set[str] = set()
        self._cleared: set[str] = set()
        self._unreadable: list[tuple[str, str]] = []
        self._skipped = 0  # selected items left out of a multi-edit (folders, non-audio files)
        self._load_token: int | None = None
        self._save_token: int | None = None
        self._dirty = False
        self._filling = False
        self._hint: tuple[str, str] = ("", "")  # (path, suggested name) of the rename hint
        io.loaded.connect(self._loaded)
        io.loadedMany.connect(self._loaded_many)
        writer.written.connect(self._saved)
        writer.batchWritten.connect(self._batch_saved)
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

        shortcut(QKeySequence.StandardKey.Save, self, self.save, Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut(QKeySequence(Qt.Key.Key_Escape), self, self.revert, Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.show_message("Select a file to see its tags.")

    # -- building --

    def _build_form(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(8, 8, 8, 8)

        header = QHBoxLayout()
        self.covers = CoverEditor(self, self)  # the cover, its buttons and its pending change
        self.cover, self.cover_source = self.covers.label, self.covers.source
        self.replace_cover_button, self.remove_cover_button = self.covers.replace_button, self.covers.remove_button
        names = QVBoxLayout()
        self.name_label = QLabel()
        self.name_label.setWordWrap(True)
        theme.strong(self.name_label)
        self.format_label = QLabel()
        self.format_label.setWordWrap(True)
        names.addWidget(self.name_label)
        names.addWidget(self.format_label)
        names.addWidget(self.cover_source)
        # In the room the cover column leaves (SPEC §23): nothing on screen is resized.
        self.trim_button = QPushButton("Trim…")
        self.trim_button.clicked.connect(lambda: self.trimRequested.emit(str(self.path)) if self.path else None)
        self.trim_button.setToolTip(TRIM_TIP)
        self.trim_button.hide()
        self.new_cue_button = QPushButton("New cue sheet…")
        self.new_cue_button.clicked.connect(
            lambda: self.newCueRequested.emit(str(self.path)) if self.path else None)
        self.new_cue_button.setToolTip(NEW_CUE_TIP)
        self.new_cue_button.hide()
        trim_row = QHBoxLayout()
        trim_row.addWidget(self.trim_button)
        trim_row.addWidget(self.new_cue_button)
        trim_row.addStretch(1)
        names.addLayout(trim_row)
        names.addStretch(1)
        header.addLayout(self.covers.column())
        header.addLayout(names, 1)
        outer.addLayout(header)

        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.hide()
        outer.addWidget(self.note)

        form = QFormLayout()
        self.edits: dict[str, QLineEdit] = {}
        self.clear_actions = {}
        for key, label in TEXT_FIELDS:
            form.addRow(label, self._line(key))
        form.addRow("Track # / total", self._pair("track", "track_total"))
        form.addRow("Disc # / total", self._pair("disc", "disc_total"))
        for key, label in LATER_TEXT_FIELDS:
            form.addRow(label, self._line(key))
        outer.addLayout(form)

        self.rename_hint = QLabel()
        self.rename_hint.setTextFormat(Qt.TextFormat.RichText)
        self.rename_hint.setWordWrap(True)
        self.rename_hint.linkActivated.connect(lambda _link: self._accept_rename_hint())
        self.rename_hint.hide()
        outer.addWidget(self.rename_hint)

        buttons = QHBoxLayout()
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        theme.mark_danger(self.error_label)
        self.error_label.hide()
        self.revert_button = QPushButton("Revert")
        self.revert_button.clicked.connect(self.revert)
        self.revert_button.setToolTip("Drop the unsaved edits and show the tags as they are in the files (Esc)")
        self.save_button = QPushButton("Save")
        self.save_button.setToolTip("Write the edited tags into the selected files (Ctrl+S)")
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
        self.details_box = QFrame()
        self.details_box.setFrameShape(QFrame.Shape.StyledPanel)
        self.details_box.setLayout(details)
        self.details_title = QLabel("Details (read-only; the loudness values are managed by the ReplayGain tool)")
        outer.addWidget(self.details_title)
        outer.addWidget(self.details_box)
        outer.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        return scroll

    def _line(self, key: str) -> QLineEdit:
        edit = QLineEdit()
        exempt(edit, "a tag field: its label says it all (SPEC §24)")
        edit.textEdited.connect(lambda _text, k=key: self._edited(k))
        self.edits[key] = edit
        if key in SHARED_FIELDS:
            action = edit.addAction(standard_icon(QStyle.StandardPixmap.SP_LineEditClearButton),
                                    QLineEdit.ActionPosition.TrailingPosition)
            action.setToolTip("Clear this field on all the selected files")
            action.triggered.connect(lambda _checked=False, k=key: self.clear_on_all(k))
            action.setVisible(False)
            self.clear_actions[key] = action
        return edit

    def _pair(self, number: str, total: str) -> QWidget:
        box = QWidget()
        layout = QHBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        for key in (number, total):
            edit = self._line(key)
            edit.setValidator(QIntValidator(0, 9999, edit))
            edit.setMaximumWidth(90)
            layout.addWidget(edit)
            if key == number:
                layout.addWidget(QLabel("/"))
        layout.addStretch(1)
        return box

    # -- what is shown --

    @property
    def dirty(self) -> bool:
        return self._dirty

    @property
    def saving(self) -> bool:
        return self._save_token is not None

    def show_message(self, text: str) -> None:
        """Nothing to edit (nothing selected, a folder, or not audio)."""
        self.path, self.snapshot, self.paths, self.snapshots, self.multi = None, None, [], [], False
        self._load_token = None
        self.message.setText(text)
        self.pages.setCurrentWidget(self.message)
        self._set_dirty(False)

    def show_file(self, path: str | Path) -> None:
        self.path, self.paths, self.snapshots, self.multi = Path(path), [], [], False
        self.snapshot = None
        self.message.setText(f"Reading the tags of {self.path.name}…")
        self.pages.setCurrentWidget(self.message)
        self._set_dirty(False)
        self._load_token = self.io.load(self.path)

    def show_files(self, paths: Sequence[str | Path], skipped: int = 0) -> None:
        """Several audio files; `skipped` other selected items (folders, non-audio files) are left out."""
        self.path, self.snapshot, self.multi = None, None, True
        self.paths = [Path(p) for p in paths]
        self.snapshots = []
        self._skipped = skipped
        self.message.setText(f"Reading the tags of {len(self.paths)} files…")
        self.pages.setCurrentWidget(self.message)
        self._set_dirty(False)
        self._load_token = self.io.load_many(self.paths)

    def _loaded(self, result: Loaded) -> None:
        if result.token != self._load_token:
            return  # the selection moved on
        self._load_token = None
        if result.snapshot is None:
            assert self.path is not None  # a single file's load
            self.message.setText(f"The tags of {self.path.name} couldn't be read.\n{result.error}")
            return
        self._fill(result.snapshot, result.cover)
        self.pages.setCurrentIndex(1)

    def _loaded_many(self, result: LoadedMany) -> None:
        if result.token != self._load_token:
            return
        self._load_token = None
        if not result.snapshots:
            self.message.setText("None of the selected files could be read.")
            return
        self._unreadable = result.failed
        self._fill_many(result.snapshots, result.cover)
        self.pages.setCurrentIndex(1)

    # -- one file --

    def _fill(self, snapshot: Snapshot, cover) -> None:
        self.snapshot = snapshot
        self._filling = True
        fields = snapshot.fields
        for key, edit in self.edits.items():
            value = getattr(fields, key)
            edit.setText("" if value is None else str(value))
            edit.setPlaceholderText("")
        for action in self.clear_actions.values():
            action.setVisible(False)
        self._filling = False
        self.name_label.setText(snapshot.path.name)
        self.format_label.setText(snapshot.format_line)
        self.covers.reset(cover)
        self.details_title.show()
        self.details_box.show()
        self.error_label.hide()
        self.rename_hint.hide()
        self._fill_details(snapshot)
        self._set_dirty(False)
        self._update_lock()

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

    # -- several files --

    @property
    def editable_snapshots(self) -> list[Snapshot]:
        return [s for s in self.snapshots if s.editable]

    def _fill_many(self, snapshots: list[Snapshot], cover: QImage | None = None) -> None:
        self.snapshots = snapshots
        self._touched.clear()
        self._cleared.clear()
        self._common = common_values([s.fields for s in self.editable_snapshots])
        self._filling = True
        for key, edit in self.edits.items():
            if key in SHARED_FIELDS:
                common = self._common[key]
                edit.setText("" if common is MULTIPLE or common is None else str(common))
                edit.setPlaceholderText(MULTIPLE_TEXT if common is MULTIPLE else "")
                self.clear_actions[key].setVisible(True)
            else:
                edit.setText("")
                edit.setPlaceholderText("(one file at a time)")
        self._filling = False

        self.name_label.setText(f"{len(snapshots)} files selected")
        counts: dict[str, int] = {}
        for s in snapshots:
            fmt = s.path.suffix.lstrip(".").upper()
            counts[fmt] = counts.get(fmt, 0) + 1
        self.format_label.setText(", ".join(f"{n} {fmt}" for fmt, n in sorted(counts.items())))
        self.covers.reset(cover)
        self.details_title.hide()
        self.details_box.hide()
        self.error_label.hide()
        self._set_dirty(False)
        self._update_lock()

    def clear_on_all(self, key: str) -> None:
        """The clear button: this field will be removed from every selected file."""
        edit = self.edits[key]
        edit.clear()
        edit.setPlaceholderText(CLEARED_TEXT)
        self._cleared.add(key)
        self._touched.add(key)
        self._edited()

    def _multi_notes(self) -> list[str]:
        notes = []
        not_editable = [s for s in self.snapshots if not s.editable]
        if not_editable:
            notes.append(f"{len(not_editable)} of these files have no tags and are left out.")
        if self._unreadable:
            notes.append(f"{len(self._unreadable)} files couldn't be read and are left out.")
        if self._skipped:
            notes.append(f"{self._skipped} other selected items aren't audio files and are left out.")
        return notes

    # -- read-only states --

    def _update_lock(self) -> None:
        """Files under a running job's lock are read-only, with the job named (SPEC §7)."""
        files = [self.snapshot] if self.snapshot is not None else self.snapshots
        if not files:
            return
        owners = [owner for owner in (self.writer.locked_by(s.path) for s in files) if owner]
        self.locked_by = owners[0] if owners else None
        can_tag = any(s.editable for s in files)
        editable = can_tag and self.locked_by is None
        tooltip = f"Read-only while {self.locked_by} is running." if self.locked_by else ""
        wma = any(s.family == TagFamily.ASF for s in files)  # WMA has no standard track total (SPEC §1)
        for key, edit in self.edits.items():
            per_track_in_multi = self.multi and key in PER_TRACK_FIELDS
            no_field = wma and key == "track_total"
            edit.setEnabled(can_tag and not per_track_in_multi and not no_field)
            edit.setReadOnly(not editable)
            edit.setToolTip("Edited one file at a time." if per_track_in_multi else WMA_TRACK_TOTAL if no_field
                            else tooltip)
            if key in self.clear_actions:
                self.clear_actions[key].setEnabled(editable)
        self.covers.can_edit = editable
        self.covers.update_buttons()
        notes = self._multi_notes() if self.multi else []
        if not can_tag:
            notes.insert(0, f"{files[0].container} files have no tags, so they can't be edited here."
                         if not self.multi else "None of these files can be tagged.")
        elif self.locked_by:
            notes.insert(0, f"Read-only while {self.locked_by} is running.")
        self.note.setText("\n".join(notes))
        self.note.setVisible(bool(notes))
        self._update_trim_button()
        self._set_dirty(self.dirty)

    def set_trim_unavailable(self, reason: str | None) -> None:
        self.trim_unavailable = reason
        self._update_trim_button()

    def _update_trim_button(self) -> None:
        """Trim… and New cue sheet… show for one audio file only; greyed, saying why, for a format flac doesn't
        read, while flac is unavailable, or while a job holds the file (SPEC §23)."""
        single = self.snapshot is not None and not self.multi
        self.trim_button.setVisible(single)
        self.new_cue_button.setVisible(single)
        if self.snapshot is None or not single:
            return
        reason = (trim.unsupported_reason(self.snapshot.path) or self.trim_unavailable
                  or (f"Unavailable while {self.locked_by} is running." if self.locked_by else None))
        self.trim_button.setEnabled(reason is None)
        self.trim_button.setToolTip(reason or TRIM_TIP)
        self.new_cue_button.setEnabled(reason is None)
        self.new_cue_button.setToolTip(reason or NEW_CUE_TIP)

    # -- editing --

    def changes(self) -> dict:
        """The fields to write: for one file, those that differ from it; for several, the shared fields that
        were edited or cleared (a "(multiple values)" field left empty stays untouched). None clears a field."""
        texts = {key: edit.text() for key, edit in self.edits.items()}
        if self.multi:
            if not self.editable_snapshots:
                return {}
            changed = multi_changes(texts, self._touched, self._cleared, self._common)
        elif self.snapshot is None or not self.snapshot.editable:
            return {}
        else:
            changed = single_changes(self.snapshot.fields, texts)
        self.covers.add_change(changed)
        return changed

    def _edited(self, key: str | None = None) -> None:
        if self._filling:
            return
        if key is not None and self.multi:
            self._touched.add(key)
            if self.edits[key].text():
                self._cleared.discard(key)
                common = self._common.get(key)
                self.edits[key].setPlaceholderText(MULTIPLE_TEXT if common is MULTIPLE else "")
        self._set_dirty(bool(self.changes()))

    def _set_dirty(self, dirty: bool) -> None:
        if dirty != self._dirty:
            self._dirty = dirty
            self.dirtyChanged.emit(dirty)
        self.save_button.setEnabled(dirty and self._save_token is None and self.locked_by is None)
        self.revert_button.setEnabled(dirty)

    def revert(self) -> None:
        if self._save_token is not None:
            return
        if self.multi and self.snapshots:
            self._fill_many(self.snapshots, self.covers.image)
        elif self.snapshot is not None:
            self._fill(self.snapshot, self.covers.image)

    def save(self) -> None:
        changes = self.changes()
        if not changes or self._save_token is not None:
            return
        self.error_label.hide()
        if self.multi:
            self._save_token = self.writer.write_many([(s.path, changes) for s in self.editable_snapshots])
        elif self.path is not None:
            self._save_token = self.writer.write(self.path, changes)
        else:
            return
        self.save_button.setEnabled(False)
        self.save_button.setText("Saving…")

    def _saved(self, result: Loaded) -> None:
        if result.token != self._save_token:
            return
        self._save_token = None
        self.save_button.setText("Save")
        if result.snapshot is None:
            self._show_error(f"The tags couldn't be saved: {result.error}")
            return
        if not self.multi and self.path == result.snapshot.path and self.pages.currentIndex() == 1:
            self._fill(result.snapshot, result.cover)
            self._show_rename_hint(result.snapshot)
        self.fileSaved.emit(str(result.snapshot.path))

    def _show_rename_hint(self, snapshot: Snapshot) -> None:
        """After a save: the tags no longer match the file name the rename masks give."""
        name = snapshot.suggested_name
        if name:
            self._hint = (str(snapshot.path), name)
            self.rename_hint.setText(f'The name no longer matches the tags. <a href="rename">Rename to '
                                     f'"{html.escape(name)}"?</a>')
        self.rename_hint.setVisible(bool(name))

    def _accept_rename_hint(self) -> None:
        self.rename_hint.hide()
        self.renameSuggested.emit(*self._hint)

    def _batch_saved(self, result: BatchWritten) -> None:
        if result.token != self._save_token:
            return
        self._save_token = None
        self.save_button.setText("Save")
        fresh = {s.path: s for s in result.saved}
        if self.multi:
            self.snapshots = [fresh.get(s.path, s) for s in self.snapshots]
        if result.saved:
            self.filesSaved.emit(len(result.saved))
        if result.failed:
            total = len(result.saved) + len(result.failed)
            reasons = "\n".join(message for _path, message in result.failed)
            self._show_error(f"Saved {len(result.saved)} of {total} files. Not saved:\n{reasons}")
            return
        if self.multi and self.pages.currentIndex() == 1:
            if self.covers.change is not None:  # the common cover changed: read it back
                self._load_token = self.io.load_many([s.path for s in self.snapshots])
            else:
                self._fill_many(self.snapshots, self.covers.image)

    def _show_error(self, message: str) -> None:
        """The edits stay (and stay dirty) so the user can retry."""
        if self.pages.currentIndex() == 1:
            self.error_label.setText(message)
            self.error_label.show()
        self._set_dirty(self.dirty)
        self.saveFailed.emit(message)

    # -- leaving with unsaved edits --

    def ask_leave(self) -> str:
        """'save', 'discard' or 'cancel' (a separate method so tests can answer it)."""
        name = self.path.name if self.path else f"these {len(self.snapshots)} files" if self.multi else "this file"
        box = QMessageBox(QMessageBox.Icon.Question, "Unsaved changes",
                          f"Save the tag changes to {name}?", parent=self)
        save = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save)
        box.exec()
        return "save" if box.clickedButton() is save else "discard" if box.clickedButton() is discard else "cancel"

    def discard_edits(self) -> None:
        """Forget the unsaved edits without asking (their file was sent to the Recycle Bin, after a confirmation
        that said so)."""
        self._set_dirty(False)

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
