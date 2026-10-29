"""The Metadata tab's cover (SPEC §7): the picture in its header, Replace… and Remove, and a picture dropped on it.
A replacement is read off the UI thread and stays a pending change until the tab saves."""

from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QPixmap
from PySide6.QtWidgets import QFileDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout

from refrain.core.metadata import COVER, COVER_EXTENSIONS, LARGE_COVER, embedded_cover
from refrain.services.tag_io import ImageRead

MULTIPLE_COVERS = "(multiple covers)"


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


class CoverEditor(QObject):
    def __init__(self, tab, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.tab = tab
        self.image = None  # what the files have now (one file, or the common cover of several)
        self.change: tuple | None = None  # ("set", bytes, QImage) or ("remove",), until saved
        self.can_edit = False
        self._token: int | None = None
        tab.io.imageRead.connect(self._image_read)

        self.label = CoverLabel()
        self.label.setFixedSize(112, 112)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setWordWrap(True)
        self.label.setFrameShape(QFrame.Shape.StyledPanel)
        self.label.setToolTip("Drop a JPEG or PNG picture here to replace the cover.")
        self.label.pictureDropped.connect(self.replace_from)
        self.replace_button = QPushButton("Replace…")
        self.replace_button.clicked.connect(self.replace)
        self.replace_button.setToolTip("Choose a JPEG or PNG picture as the embedded cover; it is written when you "
                                       "save")
        self.remove_button = QPushButton("Remove")
        self.remove_button.setToolTip("Remove the embedded cover when you save; picture files in the folder stay")
        self.remove_button.clicked.connect(self.remove)
        self.source = QLabel()

    def column(self) -> QVBoxLayout:
        """The picture over its two buttons, for the tab's header."""
        box = QVBoxLayout()
        box.addWidget(self.label)
        buttons = QHBoxLayout()
        buttons.addWidget(self.replace_button)
        buttons.addWidget(self.remove_button)
        box.addLayout(buttons)
        return box

    def reset(self, image) -> None:
        """The files were (re)read: their cover, and no pending change."""
        self.image = image
        self.change = None
        self.show()

    def _files(self) -> list:
        tab = self.tab
        return tab.editable_snapshots if tab.multi else [tab.snapshot] if tab.snapshot else []

    def has_embedded(self) -> bool:
        return any(embedded_cover(s) is not None for s in self._files())

    def add_change(self, changed: dict) -> None:
        """The pending change, as the save's COVER value."""
        if self.change is None:
            return
        if self.change[0] == "set":
            changed[COVER] = self.change[1]
        elif self.has_embedded():
            changed[COVER] = None

    def show(self) -> None:
        """The header picture: a pending change first, then what the file(s) have."""
        tab = self.tab
        image, text, source = None, "No cover", ""
        if self.change is not None and self.change[0] == "set":
            image, source = self.change[2], "New cover (not saved yet)"
        elif self.change is not None:
            text, source = "Cover removed", "The embedded cover will be removed on save."
        elif tab.multi:
            covers = {embedded_cover(s) for s in tab.editable_snapshots}
            if self.image is not None:
                image, source = self.image, "The same embedded cover on every file"
            elif covers == {None}:
                text = "No cover"
            else:
                text = MULTIPLE_COVERS
        elif tab.snapshot is not None:
            image = self.image
            source = {"embedded": "Embedded cover", "": ""}.get(
                tab.snapshot.cover_source, f"Cover from {tab.snapshot.cover_source}")
        if image is not None:
            self.label.setPixmap(QPixmap.fromImage(image).scaled(
                self.label.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            self.label.setPixmap(QPixmap())
            self.label.setText(text)
        self.source.setText(source)
        self.update_buttons()

    def update_buttons(self) -> None:
        can = self.can_edit
        self.label.setEnabled(can)
        self.replace_button.setEnabled(can)
        removing = self.change is not None and self.change[0] == "remove"
        self.remove_button.setEnabled(can and not removing and (self.has_embedded() or self.change is not None))

    def choose_cover_file(self) -> str:
        """The picture to use (a separate method so tests can answer it)."""
        path, _filter = QFileDialog.getOpenFileName(self.tab, "Choose a cover picture", "",
                                                    "Pictures (*.jpg *.jpeg *.png)")
        return path

    def confirm_large_cover(self, size: int) -> bool:
        answer = QMessageBox.question(
            self.tab, "Large cover",
            f"This picture is {size / (1024 * 1024):.1f} MB. Every file stores its own copy, so a large cover "
            "makes each file that much bigger.\n\nUse it anyway?")
        return answer == QMessageBox.StandardButton.Yes

    def replace(self) -> None:
        path = self.choose_cover_file()
        if path:
            self.replace_from(path)

    def replace_from(self, path: str) -> None:
        """Read the picture on the worker thread; it becomes a pending change once read."""
        self.tab.error_label.hide()
        self._token = self.tab.io.read_image(path)

    def _image_read(self, result: ImageRead) -> None:
        if result.token != self._token:
            return
        self._token = None
        if result.data is None:
            self.tab.error_label.setText(f"That picture can't be used: {result.error}")
            self.tab.error_label.show()
            return
        if len(result.data) > LARGE_COVER and not self.confirm_large_cover(len(result.data)):
            return
        self.change = ("set", result.data, result.image)
        self.show()
        self.tab._edited()

    def remove(self) -> None:
        self.change = ("remove",)
        self.show()
        self.tab._edited()
