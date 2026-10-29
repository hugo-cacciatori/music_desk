"""The file preview (SPEC §20): a rip log, cue sheet, note or scan, shown read-only in the side panel.

Reading and decoding happen on the TagIO worker; this page only shows the result. Nothing on it can write.
"""

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase, QImage, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from refrain.core import preview
from refrain.services.tag_io import PreviewRead, TagIO
from refrain.ui import theme
from refrain.ui.file_view import format_size


class FilePreview(QWidget):
    openRequested = Signal(str)  # the file, to open in its default app
    splitRequested = Signal(str)  # a .cue: Split, at once with the defaults (SPEC §22)
    editRequested = Signal(str)  # a .cue: Edit…, the Split page

    def __init__(self, io: TagIO, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.io = io
        self.path: Path | None = None
        self.result: PreviewRead | None = None  # what is shown
        self._token: int | None = None
        self._stamp: tuple | None = None  # the index's (size, mtime) when it was asked for
        self._scroll: tuple[int, int] | None = None  # kept across a reload
        self._image: QImage | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        header = QHBoxLayout()
        names = QVBoxLayout()
        self.name_label = QLabel()
        self.name_label.setWordWrap(True)
        theme.strong(self.name_label)
        self.details = QLabel()
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        names.addWidget(self.name_label)
        names.addWidget(self.details)
        self.open_button = QPushButton("Open")
        self.open_button.setToolTip("Open it in its default app")
        self.open_button.clicked.connect(lambda: self.path is not None and self.openRequested.emit(str(self.path)))
        # A cue sheet's three buttons (Hugo, M19): Play (Open, renamed), Split at once, and Edit… (the Split page).
        self.split_button = QPushButton("Split")
        self.split_button.setToolTip("Split the album into one FLAC file per track now, with the defaults: every "
                                     "track, into this folder, named by the File names masks")
        self.split_button.clicked.connect(lambda: self.path is not None and self.splitRequested.emit(str(self.path)))
        self.edit_button = QPushButton("Edit…")
        self.edit_button.setToolTip("Open the Split page: check and edit the tracks, the names and the destination "
                                    "before splitting")
        self.edit_button.clicked.connect(lambda: self.path is not None and self.editRequested.emit(str(self.path)))
        for button in (self.split_button, self.edit_button):
            button.hide()
        header.addLayout(names, 1)
        for button in (self.open_button, self.split_button, self.edit_button):
            header.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)  # selecting and Ctrl+C still work
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)  # logs and cue sheets are column-aligned
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse
                                          | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.image_area = QScrollArea()
        self.image_area.setWidget(self.image_label)
        self.image_area.setWidgetResizable(True)
        self.image_area.setFrameShape(QFrame.Shape.NoFrame)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.pages = QStackedWidget()
        for page in (self.message, self.text, self.image_area):
            self.pages.addWidget(page)
        layout.addWidget(self.pages, 1)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.hide()
        layout.addWidget(self.note)
        io.previewRead.connect(self._read)

    def show_file(self, path: str | Path, stamp: tuple | None = None) -> None:
        """Preview a file; `stamp` is the index's (size, mtime). Shown again with an unchanged stamp, nothing is
        read; with a new one (a log being written), it reloads and keeps the scroll position."""
        path = Path(path)
        if path == self.path and (stamp is None or stamp == self._stamp):
            return
        reload = path == self.path and self.result is not None
        self.path, self._stamp = path, stamp
        cue = path.suffix.lower() == ".cue"
        self.split_button.setVisible(cue)
        self.edit_button.setVisible(cue)
        self.open_button.setText("Play" if cue else "Open")
        self.open_button.setToolTip("Play the album in your default player" if cue else "Open it in its default app")
        self._scroll = (self.text.horizontalScrollBar().value(), self.text.verticalScrollBar().value()) \
            if reload and self.pages.currentWidget() is self.text else None
        if not reload:
            self.result = None
            self.name_label.setText(path.name)
            self.details.setText("")
            self.note.hide()
            self._show_message(f"Reading {path.name}…")
        self._token = self.io.read_preview(path)

    def clear(self) -> None:
        """Another page took the panel: drop the shown file, so selecting it again reads it afresh."""
        self.path, self.result, self._token, self._stamp, self._image = None, None, None, None, None
        self.text.clear()
        self.image_label.clear()

    def _read(self, result: PreviewRead) -> None:
        if result.token != self._token:
            return  # stale: the selection moved on
        self._token = None
        self.result = result
        modified = datetime.fromtimestamp(result.mtime).strftime("%Y-%m-%d %H:%M") if result.mtime else ""
        parts = [format_size(result.size), f"modified {modified}" if modified else ""]
        if result.pixels:
            parts.insert(0, f"{result.pixels[0]} × {result.pixels[1]} px")
        if result.text is not None:
            parts.append(result.text.encoding)
        self.details.setText(" · ".join(p for p in parts if p))
        self.note.hide()
        if result.error is not None:
            self._image = None
            self._show_message(result.error)
        elif result.text is not None:
            self._image = None
            self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth if result.text.wrap
                                      else QPlainTextEdit.LineWrapMode.NoWrap)  # long lines: SPEC §20
            self.text.setPlainText(result.text.text)
            if self._scroll is not None:
                self.text.horizontalScrollBar().setValue(self._scroll[0])
                self.text.verticalScrollBar().setValue(self._scroll[1])
            if result.text.truncated:
                self.note.setText(f"Showing the first {format_size(preview.TEXT_LIMIT)} of "
                                  f"{format_size(result.size)}. Open it to see the rest.")
                self.note.show()
            self.pages.setCurrentWidget(self.text)
        else:
            self._image = result.image
            self.pages.setCurrentWidget(self.image_area)
            self._fit_image()

    def _show_message(self, text: str) -> None:
        self.message.setText(text)
        self.pages.setCurrentWidget(self.message)

    def _fit_image(self) -> None:
        """Fit the panel's width, never upscaled, keeping the ratio."""
        if self._image is None:
            return
        width = max(1, self.image_area.viewport().width())
        pixmap = QPixmap.fromImage(self._image)
        if pixmap.width() > width:
            pixmap = pixmap.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
        self.image_label.setPixmap(pixmap)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.pages.currentWidget() is self.image_area:
            self._fit_image()
