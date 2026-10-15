"""The sidebar's LIBRARY section: the roots, "Add folder…" and "Remove from library" (SPEC §3, §5).

The lazy folder tree (M2 task 5) grows from these root items.
"""

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QMenu, QPushButton, QVBoxLayout, QWidget

from music_desk.core.index.roots import Root

ROOT_ID_ROLE = Qt.ItemDataRole.UserRole


class Sidebar(QWidget):
    addFolderRequested = Signal()
    removeRequested = Signal(object)  # Root
    rootActivated = Signal(object)  # Root

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.header = QLabel("LIBRARY")
        self.header.setStyleSheet("font-weight: 600; letter-spacing: 1px;")
        self.list = QListWidget()
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.list.itemActivated.connect(lambda item: self.rootActivated.emit(self._root(item)))
        self.empty_hint = QLabel("Add a music folder, or drop one on the window.")
        self.empty_hint.setWordWrap(True)
        self.add_button = QPushButton("Add folder…")
        self.add_button.clicked.connect(self.addFolderRequested)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.header)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.empty_hint)
        layout.addWidget(self.add_button)
        self._roots: dict[int, Root] = {}
        self.set_roots([])

    def set_roots(self, roots: list[Root]) -> None:
        selected = self.selected_root()
        self._roots = {root.id: root for root in roots}
        self.list.clear()
        dim = self.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
        for root in roots:
            item = QListWidgetItem(root.path if root.online else f"{root.path}  (offline)")
            item.setData(ROOT_ID_ROLE, root.id)
            if root.online:
                item.setToolTip(root.path)
            else:
                item.setForeground(dim)
                item.setToolTip(f"{root.path}\nNot reachable: its last index stays browsable, read-only. "
                                "It reconnects by itself when the drive is back.")
            self.list.addItem(item)
            if selected and selected.id == root.id:
                self.list.setCurrentItem(item)
        self.empty_hint.setVisible(not roots)

    def selected_root(self) -> Root | None:
        item = self.list.currentItem()
        return self._root(item) if item else None

    def select_root(self, root_id: int) -> None:
        for row in range(self.list.count()):
            if self.list.item(row).data(ROOT_ID_ROLE) == root_id:
                self.list.setCurrentRow(row)
                return

    def _root(self, item: QListWidgetItem) -> Root:
        return self._roots[item.data(ROOT_ID_ROLE)]

    def context_menu_for(self, item: QListWidgetItem | None) -> QMenu:
        menu = QMenu(self)
        if item is not None:
            root = self._root(item)
            menu.addAction("Remove from library", lambda: self.removeRequested.emit(root))
            menu.addSeparator()
        menu.addAction("Add folder…", self.addFolderRequested.emit)
        return menu

    def _context_menu(self, pos: QPoint) -> None:
        self.context_menu_for(self.list.itemAt(pos)).exec(self.list.viewport().mapToGlobal(pos))
