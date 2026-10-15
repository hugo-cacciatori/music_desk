"""The address bar (SPEC §5): a breadcrumb of the open folder whose segments are clickable and have a dropdown
of their subfolders. Clicking the empty part of the bar, or Ctrl+L, turns it into an editable path."""

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QWidget,
)

SEPARATOR = "›"


class _Crumbs(QFrame):
    """The breadcrumb page. A click on its empty part asks for the text mode."""

    clickedEmpty = Signal()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clickedEmpty.emit()
        super().mousePressEvent(event)


class AddressBar(QStackedWidget):
    folderRequested = Signal(int)  # entry id
    pathEntered = Signal(str)  # typed or pasted text

    def __init__(self, subfolders: Callable[[int], list[tuple[int, str]]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._subfolders = subfolders  # entry id -> [(id, name)] in natural order
        self._segments: list[tuple[int, str]] = []
        self._path = ""

        self.crumbs = _Crumbs()
        self.crumbs.setFrameShape(QFrame.Shape.StyledPanel)
        self.crumbs.clickedEmpty.connect(self.edit)
        self._layout = QHBoxLayout(self.crumbs)
        self._layout.setContentsMargins(2, 0, 2, 0)
        self._layout.setSpacing(0)
        self._layout.addStretch(1)

        self.line = QLineEdit()
        self.line.returnPressed.connect(self._entered)
        self.line.installEventFilter(self)

        self.addWidget(self.crumbs)
        self.addWidget(self.line)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self.line.sizeHint().height() + 4)
        self.buttons: list[QToolButton] = []

    # -- location --

    def set_location(self, segments: list[tuple[int, str]], path: str) -> None:
        """segments: (entry id, label) from the root down; path: the folder's full path, for the text mode."""
        self._segments = segments
        self._path = path
        for button in self.buttons:
            self._layout.removeWidget(button)
            button.deleteLater()
        self.buttons = []
        for n, (entry_id, label) in enumerate(segments):
            if n:
                self._add_button(SEPARATOR, None, menu_for=segments[n - 1][0])
            self._add_button(label, entry_id)
        if segments:
            self._add_button(SEPARATOR, None, menu_for=segments[-1][0])
        self.setCurrentWidget(self.crumbs)

    def segment_labels(self) -> list[str]:
        return [label for _id, label in self._segments]

    def _add_button(self, text: str, entry_id: int | None, menu_for: int | None = None) -> None:
        button = QToolButton()
        button.setText(text)
        button.setAutoRaise(True)
        if entry_id is not None:
            button.clicked.connect(lambda: self.folderRequested.emit(entry_id))
        else:
            button.setToolTip("Subfolders")
            button.clicked.connect(lambda: self.subfolder_menu(menu_for).exec(
                button.mapToGlobal(button.rect().bottomLeft())))
        self._layout.insertWidget(self._layout.count() - 1, button)
        self.buttons.append(button)

    def subfolder_menu(self, entry_id: int) -> QMenu:
        menu = QMenu(self)
        children = self._subfolders(entry_id)
        if not children:
            menu.addAction("(no subfolders)").setEnabled(False)
        for child_id, name in children:
            menu.addAction(name, lambda child=child_id: self.folderRequested.emit(child))
        return menu

    # -- text mode --

    def edit(self) -> None:
        self.line.setText(self._path)
        self.setCurrentWidget(self.line)
        self.line.setFocus()
        self.line.selectAll()

    def show_crumbs(self) -> None:
        self.setCurrentWidget(self.crumbs)

    def _entered(self) -> None:
        text = self.line.text().strip().strip('"').strip()
        self.show_crumbs()
        if text:
            self.pathEntered.emit(text)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.line:
            if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                self.show_crumbs()
                return True
            if event.type() == QEvent.Type.FocusOut and self.currentWidget() is self.line:
                self.show_crumbs()
        return False
