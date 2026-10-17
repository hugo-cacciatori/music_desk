"""The sidebar's LIBRARY section: the folder tree with the roots at the top, "Add folder…" and "Remove from library"
(SPEC §3, §5)."""

import sqlite3
from collections.abc import Callable

from PySide6.QtCore import QModelIndex, QPoint, Qt, Signal
from PySide6.QtWidgets import QLabel, QMenu, QPushButton, QVBoxLayout, QWidget

from music_desk.core.index.roots import Root
from music_desk.ui.folder_tree import ENTRY_ID_ROLE, FolderTreeModel, FolderTreeView
from music_desk.ui.hover import exempt


class Sidebar(QWidget):
    addFolderRequested = Signal()
    removeRequested = Signal(object)  # Root
    folderSelected = Signal(int)  # entry id
    renameRequested = Signal(int, str)

    def __init__(self, conn: Callable[[], sqlite3.Connection | None], expanded: list[str] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.header = QLabel("LIBRARY")
        self.header.setStyleSheet("font-weight: 600; letter-spacing: 1px;")
        self.model = FolderTreeModel(conn, self)
        self.tree = FolderTreeView(self.model, expanded)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.folderSelected.connect(self.folderSelected)
        self.model.renameRequested.connect(self.renameRequested)
        self.empty_hint = QLabel("Add a music folder, or drop one on the window.")
        self.empty_hint.setWordWrap(True)
        self.add_button = QPushButton("Add folder…")
        self.add_button.setToolTip("Add a music folder to the library and index it; adding only reads the folder "
                                   "(Ctrl+O)")
        self.add_button.clicked.connect(self.addFolderRequested)
        exempt(self.tree, "the folder tree explains itself (SPEC §24)")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.header)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.empty_hint)
        layout.addWidget(self.add_button)
        self._roots: dict[int, Root] = {}
        # Set by the window: builds the folder actions for an entry (Open, Play, Explorer…).
        self.folder_menu_provider: Callable[[QMenu, int], None] | None = None
        self.set_roots([])

    def set_roots(self, roots: list[Root]) -> None:
        self._roots = {root.id: root for root in roots}
        self.model.set_roots(roots)
        self.empty_hint.setVisible(not roots)

    def root_texts(self) -> list[str]:
        return [self.model.index(row, 0).data() for row in range(self.model.rowCount())]

    def selected_root(self) -> Root | None:
        """The root the current folder belongs to."""
        index = self.tree.currentIndex()
        while index.isValid() and index.parent().isValid():
            index = index.parent()
        return self.model.root_for(index)

    def select_root(self, root_id: int) -> None:
        root = self._roots.get(root_id)
        if root is not None:
            self.tree.select_entry([root.top_entry_id])

    def context_menu_for(self, index: QModelIndex) -> QMenu:
        menu = QMenu(self)
        if index.isValid() and self.folder_menu_provider is not None:
            self.folder_menu_provider(menu, index.data(ENTRY_ID_ROLE))
            menu.addSeparator()
        root = self.model.root_for(index)
        if root is not None:
            menu.addAction("Remove from library", lambda: self.removeRequested.emit(root))
            menu.addSeparator()
        menu.addAction("Add folder…", self.addFolderRequested.emit)
        return menu

    def _context_menu(self, pos: QPoint) -> None:
        self.context_menu_for(self.tree.indexAt(pos)).exec(self.tree.viewport().mapToGlobal(pos))
