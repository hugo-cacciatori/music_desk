"""The Metadata tab (SPEC §7): one audio file, or several at once.

- One file: the editable fields, a header with the cover and a format line, and the read-only details and
  loudness values. Saving writes the changed fields only.
- Several files: the shared fields show their common value, or "(multiple values)" when the files differ, and
  are left alone unless edited. A clear button per field tells "clear on all" apart from "leave alone". The
  per-track fields (Title, Track #, Lyrics) are edited one file at a time and are disabled.

Ctrl+S saves and Esc reverts. Reads go through TagIO and writes through TagWriter, both off the UI thread.
"""

import html
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QImage, QIntValidator, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
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
    QStyle,
    QVBoxLayout,
    QWidget,
)

from music_desk.core.metadata import COVER, COVER_EXTENSIONS, LARGE_COVER, Snapshot, embedded_cover
from music_desk.core.tags import Fields
from music_desk.services.tag_io import ImageRead, Loaded, LoadedMany, TagIO
from music_desk.services.tag_writer import BatchWritten, TagWriter

TEXT_FIELDS = (("title", "Title"), ("artist", "Artist"), ("albumartist", "Album artist"), ("album", "Album"))
LATER_TEXT_FIELDS = (("year", "Year"), ("genre", "Genre"), ("composer", "Composer"), ("comment", "Comment"))
NUMBER_FIELDS = ("track", "track_total", "disc", "disc_total")
PER_TRACK_FIELDS = ("title", "track")  # and the lyrics: one file at a time (SPEC §7)
SHARED_FIELDS = ("artist", "albumartist", "album", "track_total", "disc", "disc_total", "year", "genre", "composer",
                 "comment")
MULTIPLE = object()  # the files of a multi-selection differ on this field
MULTIPLE_TEXT = "(multiple values)"
CLEARED_TEXT = "(cleared on all files)"


class CoverLabel(QLabel):
    """The cover in the header. A jpg or png dropped on it replaces the cover (a drop into the app is fine;
    only dragging files out is disabled)."""

    pictureDropped = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)

    @staticmethod
    def _picture(event) -> str | None:
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if len(urls) == 1 and urls[0].isLocalFile():
            path = urls[0].toLocalFile()
            if Path(path).suffix.lower() in COVER_EXTENSIONS:
                return path
        return None

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self.isEnabled() and self._picture(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        path = self._picture(event)
        if path and self.isEnabled():
            event.acceptProposedAction()
            self.pictureDropped.emit(path)
        else:
            event.ignore()


def _normalize_lyrics(text: str | None) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def _parse(key: str, text: str):
    if not text.strip():
        return None
    return int(text) if key in NUMBER_FIELDS else text


class MetadataTab(QWidget):
    dirtyChanged = Signal(bool)
    fileSaved = Signal(str)  # one file saved
    filesSaved = Signal(int)  # a multi-file save: how many files were saved
    saveFailed = Signal(str)

    def __init__(self, io: TagIO, writer: TagWriter, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.io = io
        self.writer = writer
        self.locked_by: str | None = None
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
        self._cover_image = None  # what the files have now (one file, or the common cover of several)
        self._cover_change: tuple | None = None  # ("set", bytes, QImage) or ("remove",), until saved
        self._image_token: int | None = None
        self._can_edit_cover = False
        io.loaded.connect(self._loaded)
        io.imageRead.connect(self._image_read)
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
        self.cover = CoverLabel()
        self.cover.setFixedSize(112, 112)
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.setWordWrap(True)
        self.cover.setFrameShape(QFrame.Shape.StyledPanel)
        self.cover.setToolTip("Drop a JPEG or PNG picture here to replace the cover.")
        self.cover.pictureDropped.connect(self.replace_cover_from)
        self.replace_cover_button = QPushButton("Replace…")
        self.replace_cover_button.clicked.connect(self.replace_cover)
        self.remove_cover_button = QPushButton("Remove")
        self.remove_cover_button.clicked.connect(self.remove_cover)
        cover_box = QVBoxLayout()
        cover_box.addWidget(self.cover)
        cover_buttons = QHBoxLayout()
        cover_buttons.addWidget(self.replace_cover_button)
        cover_buttons.addWidget(self.remove_cover_button)
        cover_box.addLayout(cover_buttons)
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
        header.addLayout(cover_box)
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
        self.lyrics = QPlainTextEdit()
        self.lyrics.setMinimumHeight(120)
        self.lyrics.textChanged.connect(lambda: self._edited("lyrics"))
        self.lyrics.textChanged.connect(self._update_import_link)
        self.import_link = QLabel()
        self.import_link.setTextFormat(Qt.TextFormat.RichText)
        self.import_link.linkActivated.connect(lambda _link: self.import_lrc())
        self.import_link.hide()
        lyrics_box = QVBoxLayout()
        lyrics_box.setContentsMargins(0, 0, 0, 0)
        lyrics_box.addWidget(self.lyrics)
        lyrics_box.addWidget(self.import_link)
        lyrics_widget = QWidget()
        lyrics_widget.setLayout(lyrics_box)
        form.addRow("Lyrics", lyrics_widget)
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
        edit.textEdited.connect(lambda _text, k=key: self._edited(k))
        self.edits[key] = edit
        if key in SHARED_FIELDS:
            action = edit.addAction(self.style().standardIcon(QStyle.StandardPixmap.SP_LineEditClearButton),
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

    def show_files(self, paths: list[str | Path], skipped: int = 0) -> None:
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
        self._cover_image = cover
        self._filling = True
        fields = snapshot.fields
        for key, edit in self.edits.items():
            value = getattr(fields, key)
            edit.setText("" if value is None else str(value))
            edit.setPlaceholderText("")
        for action in self.clear_actions.values():
            action.setVisible(False)
        self.lyrics.setPlainText(_normalize_lyrics(fields.lyrics))
        self.lyrics.setPlaceholderText("No lyrics in the tags. LRC timestamps are kept as typed.")
        self._filling = False
        self.name_label.setText(snapshot.path.name)
        self.format_label.setText(snapshot.format_line)
        self._cover_change = None
        self._show_cover()
        self.details_title.show()
        self.details_box.show()
        self.error_label.hide()
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
        self._cover_image = cover
        self._cover_change = None
        self._touched.clear()
        self._cleared.clear()
        editable = self.editable_snapshots
        self._common = {}
        for key in SHARED_FIELDS:
            values = {getattr(s.fields, key) for s in editable}
            self._common[key] = values.pop() if len(values) == 1 else MULTIPLE
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
        self.lyrics.setPlainText("")
        self.lyrics.setPlaceholderText("Lyrics are edited one file at a time.")
        self._filling = False

        self.name_label.setText(f"{len(snapshots)} files selected")
        counts: dict[str, int] = {}
        for s in snapshots:
            fmt = s.path.suffix.lstrip(".").upper()
            counts[fmt] = counts.get(fmt, 0) + 1
        self.format_label.setText(", ".join(f"{n} {fmt}" for fmt, n in sorted(counts.items())))
        self._show_cover()
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
        for key, edit in self.edits.items():
            per_track_in_multi = self.multi and key in PER_TRACK_FIELDS
            edit.setEnabled(can_tag and not per_track_in_multi)
            edit.setReadOnly(not editable)
            edit.setToolTip("Edited one file at a time." if per_track_in_multi else tooltip)
            if key in self.clear_actions:
                self.clear_actions[key].setEnabled(editable)
        self.lyrics.setEnabled(can_tag and not self.multi)
        self.lyrics.setReadOnly(not editable)
        self.lyrics.setToolTip(tooltip)
        self._can_edit_cover = editable
        self._update_cover_buttons()
        self._update_import_link()
        notes = self._multi_notes() if self.multi else []
        if not can_tag:
            notes.insert(0, f"{files[0].container} files have no tags, so they can't be edited here."
                         if not self.multi else "None of these files can be tagged.")
        elif self.locked_by:
            notes.insert(0, f"Read-only while {self.locked_by} is running.")
        self.note.setText("\n".join(notes))
        self.note.setVisible(bool(notes))
        self._set_dirty(self.dirty)

    # -- editing --

    def changes(self) -> dict:
        """The fields to write: for one file, those that differ from it; for several, the shared fields that
        were edited or cleared (a "(multiple values)" field left empty stays untouched). None clears a field."""
        if self.multi:
            return self._multi_changes()
        if self.snapshot is None or not self.snapshot.editable:
            return {}
        fields: Fields = self.snapshot.fields
        changed = {}
        for key, edit in self.edits.items():
            original = getattr(fields, key)
            if edit.text() == ("" if original is None else str(original)):
                continue  # untouched, even when the file holds blank text such as "  "
            value = _parse(key, edit.text())
            if value != original:
                changed[key] = value
        lyrics = self.lyrics.toPlainText()
        if lyrics != _normalize_lyrics(fields.lyrics):
            changed["lyrics"] = lyrics if lyrics.strip() else None
        self._add_cover_change(changed)
        return changed

    def _add_cover_change(self, changed: dict) -> None:
        if self._cover_change is None:
            return
        if self._cover_change[0] == "set":
            changed[COVER] = self._cover_change[1]
        elif self._has_embedded_cover():
            changed[COVER] = None

    def _multi_changes(self) -> dict:
        if not self.editable_snapshots:
            return {}
        changed = {}
        for key in SHARED_FIELDS:
            if key not in self._touched:
                continue
            text = self.edits[key].text()
            common = self._common[key]
            if not text.strip() and key not in self._cleared and common is MULTIPLE:
                continue  # emptied a "(multiple values)" field without the clear button: leave it alone
            value = _parse(key, text)
            if common is not MULTIPLE and value == common:
                continue
            changed[key] = value
        self._add_cover_change(changed)
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
            self._fill_many(self.snapshots, self._cover_image)
        elif self.snapshot is not None:
            self._fill(self.snapshot, self._cover_image)

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
        self.fileSaved.emit(str(result.snapshot.path))

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
            if self._cover_change is not None:  # the common cover changed: read it back
                self._load_token = self.io.load_many([s.path for s in self.snapshots])
            else:
                self._fill_many(self.snapshots, self._cover_image)

    def _show_error(self, message: str) -> None:
        """The edits stay (and stay dirty) so the user can retry."""
        if self.pages.currentIndex() == 1:
            self.error_label.setText(message)
            self.error_label.show()
        self._set_dirty(self.dirty)
        self.saveFailed.emit(message)

    # -- lyrics from a .lrc sidecar --

    def _update_import_link(self) -> None:
        """"Import from <name>.lrc" when the Lyrics field is empty and a same-stem .lrc sits next to the file."""
        s = self.snapshot
        show = (not self.multi and s is not None and s.lrc_text is not None and s.editable
                and self.locked_by is None and not self.lyrics.toPlainText().strip())
        if show:
            self.import_link.setText(f'<a href="import">Import from {html.escape(s.lrc_name)}</a>')
        self.import_link.setVisible(show)

    def import_lrc(self) -> None:
        """Fill the Lyrics field from the sidecar. Saving is the usual Save; the .lrc file is never touched."""
        if self.snapshot is not None and self.snapshot.lrc_text is not None:
            self.lyrics.setPlainText(_normalize_lyrics(self.snapshot.lrc_text).strip("\n"))

    # -- the cover --

    def _has_embedded_cover(self) -> bool:
        files = self.editable_snapshots if self.multi else [self.snapshot] if self.snapshot else []
        return any(embedded_cover(s) is not None for s in files)

    def _show_cover(self) -> None:
        """The header picture: a pending change first, then what the file(s) have."""
        image, text, source = None, "No cover", ""
        if self._cover_change is not None and self._cover_change[0] == "set":
            image, source = self._cover_change[2], "New cover (not saved yet)"
        elif self._cover_change is not None:
            text, source = "Cover removed", "The embedded cover will be removed on save."
        elif self.multi:
            covers = {embedded_cover(s) for s in self.editable_snapshots}
            if self._cover_image is not None:
                image, source = self._cover_image, "The same embedded cover on every file"
            elif covers == {None}:
                text = "No cover"
            else:
                text = MULTIPLE_TEXT.replace("values", "covers")
        elif self.snapshot is not None:
            image = self._cover_image
            source = {"embedded": "Embedded cover", "": ""}.get(
                self.snapshot.cover_source, f"Cover from {self.snapshot.cover_source}")
        if image is not None:
            self.cover.setPixmap(QPixmap.fromImage(image).scaled(
                self.cover.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            self.cover.setPixmap(QPixmap())
            self.cover.setText(text)
        self.cover_source.setText(source)
        self._update_cover_buttons()

    def _update_cover_buttons(self) -> None:
        can = self._can_edit_cover
        self.cover.setEnabled(can)
        self.replace_cover_button.setEnabled(can)
        removing = self._cover_change is not None and self._cover_change[0] == "remove"
        self.remove_cover_button.setEnabled(
            can and not removing and (self._has_embedded_cover() or self._cover_change is not None))

    def choose_cover_file(self) -> str:
        """The picture to use (a separate method so tests can answer it)."""
        path, _filter = QFileDialog.getOpenFileName(self, "Choose a cover picture", "",
                                                    "Pictures (*.jpg *.jpeg *.png)")
        return path

    def confirm_large_cover(self, size: int) -> bool:
        answer = QMessageBox.question(
            self, "Large cover",
            f"This picture is {size / (1024 * 1024):.1f} MB. Every file stores its own copy, so a large cover "
            "makes each file that much bigger.\n\nUse it anyway?")
        return answer == QMessageBox.StandardButton.Yes

    def replace_cover(self) -> None:
        path = self.choose_cover_file()
        if path:
            self.replace_cover_from(path)

    def replace_cover_from(self, path: str) -> None:
        """Read the picture on the worker thread; it becomes a pending change once read."""
        self.error_label.hide()
        self._image_token = self.io.read_image(path)

    def _image_read(self, result: ImageRead) -> None:
        if result.token != self._image_token:
            return
        self._image_token = None
        if result.data is None:
            self.error_label.setText(f"That picture can't be used: {result.error}")
            self.error_label.show()
            return
        if len(result.data) > LARGE_COVER and not self.confirm_large_cover(len(result.data)):
            return
        self._cover_change = ("set", result.data, result.image)
        self._show_cover()
        self._edited()

    def remove_cover(self) -> None:
        self._cover_change = ("remove",)
        self._show_cover()
        self._edited()

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
