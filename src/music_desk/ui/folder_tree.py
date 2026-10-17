"""The folder tree (SPEC §5): roots at the top, folders only, in natural order, loaded lazily from the index.

Updates are incremental (rows inserted, removed, moved or changed; never a model reset), so the selection, the
expanded folders and the scroll position stay put while the indexer works.
"""

import os
import sqlite3
from collections.abc import Callable
from typing import cast

from PySide6.QtCore import QAbstractItemModel, QModelIndex, QPersistentModelIndex, Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QAbstractItemView, QApplication, QStyle, QWidget

from music_desk.core.index import queries
from music_desk.core.index.roots import Root
from music_desk.ui.entry_drag import EntryDragView

ENTRY_ID_ROLE = Qt.ItemDataRole.UserRole
PATH_ROLE = Qt.ItemDataRole.UserRole + 1
_NO_PARENT = QModelIndex()  # Qt's "top level", used as a default argument


class _Node:
    __slots__ = ("id", "name", "parent", "children", "fetched", "has_subfolders", "root", "n_tracks")

    def __init__(self, entry_id: int, name: str, parent: "_Node | None", has_subfolders: bool,
                 root: Root | None = None, n_tracks: int = 0) -> None:
        self.id = entry_id
        self.name = name
        self.parent = parent
        self.children: list[_Node] = []
        self.fetched = False
        self.has_subfolders = has_subfolders
        self.root = root  # set on top-level nodes
        self.n_tracks = n_tracks

    @property
    def path(self) -> str:
        parts = []
        node = self
        while node.parent is not None:
            parts.append(node.name)
            node = node.parent
        assert node.root is not None  # set on top-level nodes
        return os.path.join(node.root.path, *reversed(parts))

    @property
    def top(self) -> "_Node":
        node = self
        while node.parent is not None:
            node = node.parent
        return node


class FolderTreeModel(QAbstractItemModel):
    renameRequested = Signal(int, str)

    def __init__(self, conn: Callable[[], sqlite3.Connection | None], parent=None) -> None:
        super().__init__(parent)
        self._conn = conn
        self._tops: list[_Node] = []
        self._nodes: dict[int, _Node] = {}
        self.only_music = False
        style = QApplication.style()
        self._folder_icon = style.standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        self._drive_icon = style.standardIcon(QStyle.StandardPixmap.SP_DriveHDIcon)

    # -- Qt model API --

    def index(self, row: int, column: int, parent: QModelIndex = _NO_PARENT) -> QModelIndex:
        siblings = self._children_of(parent)
        if column != 0 or not 0 <= row < len(siblings):
            return QModelIndex()
        return self.createIndex(row, 0, siblings[row])

    def parent(self, index: QModelIndex = _NO_PARENT) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()
        node: _Node = index.internalPointer()
        if node.parent is None:
            return QModelIndex()
        return self.createIndex(self._row_of(node.parent), 0, node.parent)

    def rowCount(self, parent: QModelIndex = _NO_PARENT) -> int:
        return len(self._children_of(parent))

    def columnCount(self, parent: QModelIndex = _NO_PARENT) -> int:
        return 1

    def hasChildren(self, parent: QModelIndex = _NO_PARENT) -> bool:
        if not parent.isValid():
            return bool(self._tops)
        node: _Node = parent.internalPointer()
        return bool(node.children) if node.fetched else node.has_subfolders

    def canFetchMore(self, parent: QModelIndex) -> bool:
        if not parent.isValid():
            return False
        node: _Node = parent.internalPointer()
        return not node.fetched and node.has_subfolders

    def fetchMore(self, parent: QModelIndex) -> None:
        if not parent.isValid():
            return
        node: _Node = parent.internalPointer()
        if node.fetched:
            return
        rows = self._query(node.id)
        node.fetched = True
        if not rows:
            node.has_subfolders = False
            self.dataChanged.emit(parent, parent)
            return
        self.beginInsertRows(parent, 0, len(rows) - 1)
        for row in rows:
            child = _Node(row.id, row.name, node, row.has_subfolders, n_tracks=row.n_tracks)
            node.children.append(child)
            self._nodes[child.id] = child
        self.endInsertRows()

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        node: _Node = index.internalPointer()
        offline = node.top.root is not None and not node.top.root.online
        if role == Qt.ItemDataRole.DisplayRole:
            if node.root is not None:
                return node.root.path if node.root.online else f"{node.root.path}  (offline)"
            return node.name
        if role == Qt.ItemDataRole.EditRole:
            return node.name
        if role == Qt.ItemDataRole.ToolTipRole:
            if offline:
                return (f"{node.path}\nNot reachable: its last index stays browsable, read-only. "
                        "It reconnects by itself when the drive is back.")
            return node.path
        if role == Qt.ItemDataRole.DecorationRole:
            return self._drive_icon if node.root is not None else self._folder_icon
        if role == Qt.ItemDataRole.ForegroundRole and offline:
            return QApplication.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
        if role == ENTRY_ID_ROLE:
            return node.id
        if role == PATH_ROLE:
            return node.path
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        # dragged into the playlist builder as entry ids only (ui/entry_drag.py): the app never moves files
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled
        if index.internalPointer().root is None:
            flags |= Qt.ItemFlag.ItemIsEditable  # folders rename inline; library roots don't (SPEC §11)
        return flags

    def setData(self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if role == Qt.ItemDataRole.EditRole and index.isValid() and isinstance(value, str):
            node: _Node = index.internalPointer()
            if node.root is None and value != node.name:
                self.renameRequested.emit(node.id, value)
        return False

    # -- lookups --

    def index_for(self, entry_id: int) -> QModelIndex:
        node = self._nodes.get(entry_id)
        return self.createIndex(self._row_of(node), 0, node) if node else QModelIndex()

    def root_for(self, index: QModelIndex) -> Root | None:
        """The Root of a top-level item, else None."""
        return index.internalPointer().root if index.isValid() else None

    def reveal(self, entry_ids: list[int]) -> QModelIndex:
        """Load the tree down a chain of ids (top entry first) and return the last one's index."""
        found = QModelIndex()
        for entry_id in entry_ids:
            index = self.index_for(entry_id)
            if not index.isValid():
                return found
            found = index
            if entry_id != entry_ids[-1] and self.canFetchMore(index):
                self.fetchMore(index)
        return found

    # -- incremental updates --

    def set_roots(self, roots: list[Root]) -> None:
        """Show these roots (in path order), diffing against the current ones."""
        wanted = [_Node(root.top_entry_id, root.path, None, True, root=root) for root in roots]
        self._sync_children(None, wanted)
        for node in self._tops:  # online state and path changes
            fresh = next(r for r in roots if r.top_entry_id == node.id)
            if fresh != node.root:
                node.root = fresh
                self._changed(node, recursive=True)

    def refresh(self, entry_id: int) -> None:
        """Re-read a loaded folder's subfolders (after the indexer updated it)."""
        node = self._nodes.get(entry_id)
        if node is None:
            return
        if not node.fetched:
            rows = self._query(entry_id)
            if bool(rows) != node.has_subfolders:
                node.has_subfolders = bool(rows)
                self._changed(node)
            return
        self._sync_children(node, [_Node(r.id, r.name, node, r.has_subfolders, n_tracks=r.n_tracks)
                                   for r in self._query(entry_id)])

    def refresh_loaded(self) -> None:
        """Re-read every loaded folder (after a full scan, or when 'hide folders without music' changes)."""
        for node in [n for n in self._nodes.values() if n.fetched]:
            if self._nodes.get(node.id) is node:  # not removed by an earlier refresh in this loop
                self.refresh(node.id)

    def set_only_music(self, only_music: bool) -> None:
        if only_music != self.only_music:
            self.only_music = only_music
            self.refresh_loaded()

    # -- internals --

    def _query(self, entry_id: int) -> list[queries.FolderRow]:
        conn = self._conn()
        if conn is None:  # the library is closed (shutdown)
            return []
        return queries.child_folders(conn, entry_id, only_music=self.only_music)

    def _children_of(self, parent: QModelIndex) -> list[_Node]:
        return parent.internalPointer().children if parent.isValid() else self._tops

    def _row_of(self, node: _Node) -> int:
        siblings = node.parent.children if node.parent else self._tops
        return next(i for i, sibling in enumerate(siblings) if sibling is node)

    def _index_of(self, node: _Node | None) -> QModelIndex:
        return QModelIndex() if node is None else self.createIndex(self._row_of(node), 0, node)

    def _changed(self, node: _Node, recursive: bool = False) -> None:
        index = self._index_of(node)
        self.dataChanged.emit(index, index)
        if recursive:
            for child in node.children:
                self._changed(child, recursive=True)

    def _forget(self, node: _Node) -> None:
        self._nodes.pop(node.id, None)
        for child in node.children:
            self._forget(child)

    def _sync_children(self, parent: _Node | None, wanted: list[_Node]) -> None:
        """Make parent's children match `wanted` (in order) with row removals, moves and inserts."""
        siblings = parent.children if parent else self._tops
        parent_index = self._index_of(parent)
        wanted_ids = {node.id for node in wanted}
        for row in range(len(siblings) - 1, -1, -1):
            if siblings[row].id not in wanted_ids:
                self.beginRemoveRows(parent_index, row, row)
                self._forget(siblings.pop(row))
                self.endRemoveRows()
        for target, fresh in enumerate(wanted):
            current = next((i for i in range(target, len(siblings)) if siblings[i].id == fresh.id), None)
            if current is None:
                self.beginInsertRows(parent_index, target, target)
                siblings.insert(target, fresh)
                self._nodes[fresh.id] = fresh
                self.endInsertRows()
                continue
            if current != target:
                self.beginMoveRows(parent_index, current, current, parent_index, target)
                siblings.insert(target, siblings.pop(current))
                self.endMoveRows()
            node = siblings[target]
            if (node.name, node.has_subfolders, node.n_tracks) != (fresh.name, fresh.has_subfolders, fresh.n_tracks):
                if not node.fetched:
                    node.has_subfolders = fresh.has_subfolders
                node.name, node.n_tracks = fresh.name, fresh.n_tracks
                self._changed(node)
        if parent is not None and not siblings:
            parent.has_subfolders = False


class FolderTreeView(EntryDragView):
    """The tree, remembering which folders are expanded (by path, so it survives rescans and restarts). Several
    folders can be selected (Ctrl/Shift+click) to drag them into the playlist; the current one is the open folder."""

    folderSelected = Signal(int)  # entry id

    def __init__(self, model: FolderTreeModel, expanded: list[str] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setModel(model)
        self.setHeaderHidden(True)
        self.setUniformRowHeights(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.drag_ids = self.selected_ids
        self.setEditTriggers(QAbstractItemView.EditTrigger.EditKeyPressed
                             | QAbstractItemView.EditTrigger.SelectedClicked)
        self._expanded = {os.path.normcase(p) for p in (expanded or [])}
        self.expanded.connect(lambda index: self._remember(index, True))
        self.collapsed.connect(lambda index: self._remember(index, False))
        model.rowsInserted.connect(self._restore_expansion)
        self.selectionModel().currentChanged.connect(self._current_changed)

    def start_rename(self, entry_id: int) -> None:
        index = cast(FolderTreeModel, self.model()).index_for(entry_id)
        if index.isValid():
            self.setCurrentIndex(index)
            self.edit(index)

    def selected_ids(self) -> list[int]:
        """The selected folders, top to bottom as shown."""
        def place(index: QModelIndex) -> list[int] | None:  # rows from the top; None under a collapsed folder
            rows = []
            while index.isValid():
                rows.append(index.row())
                index = index.parent()
                if index.isValid() and not self.isExpanded(index):
                    return None
            return rows[::-1]

        shown = [(rows, i) for i in self.selectionModel().selectedRows() if (rows := place(i)) is not None]
        return [i.data(ENTRY_ID_ROLE) for _rows, i in sorted(shown, key=lambda pair: pair[0])]

    def expanded_paths(self) -> list[str]:
        return sorted(self._expanded)

    def select_entry(self, entry_ids: list[int]) -> None:
        """Reveal and select a folder, given its ids from the top entry down."""
        model = cast(FolderTreeModel, self.model())
        index = model.reveal(entry_ids)
        if not index.isValid():
            return
        parent = index.parent()
        while parent.isValid():
            self.expand(parent)
            parent = parent.parent()
        if self.currentIndex() != index:
            self.setCurrentIndex(index)
        self.scrollTo(index)

    def _remember(self, index: QModelIndex, expanded: bool) -> None:
        key = os.path.normcase(index.data(PATH_ROLE))
        if expanded:
            self._expanded.add(key)
        else:
            self._expanded.discard(key)

    def _restore_expansion(self, parent: QModelIndex, first: int, last: int) -> None:
        if not self._expanded:
            return
        model = self.model()
        pending = [QPersistentModelIndex(model.index(row, 0, parent)) for row in range(first, last + 1)]
        for persistent in pending:
            index = model.index(persistent.row(), persistent.column(), persistent.parent())  # where it is now
            if index.isValid() and os.path.normcase(index.data(PATH_ROLE)) in self._expanded:
                self.expand(index)

    def _current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if current.isValid():
            self.folderSelected.emit(current.data(ENTRY_ID_ROLE))
