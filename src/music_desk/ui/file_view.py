"""The file list (SPEC §5): every entry of the open folder, folders first, in natural order, with tag and health
columns. Columns can be moved, resized, hidden and sorted, and the layout persists. Ctrl+F filters by name.

Rows come from the index (instant, even on slow drives). After the indexer updates the folder, rows are
inserted, removed or changed in place, never reset, so the selection and scroll position stay put.
"""

import base64
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import (
    QAbstractTableModel,
    QByteArray,
    QModelIndex,
    QPoint,
    QSortFilterProxyModel,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QAction, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QLineEdit,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from music_desk.core.fsops import natural_key
from music_desk.core.index import queries
from music_desk.core.index.queries import FileRow
from music_desk.ui.entry_drag import EntryDragView
from music_desk.ui.hover import exempt, with_status_tips
from music_desk.ui.qt_util import shortcut, standard_icon

ENTRY_ID_ROLE = Qt.ItemDataRole.UserRole
IS_DIR_ROLE = Qt.ItemDataRole.UserRole + 1
_NO_PARENT = QModelIndex()

RG_MARKS = {"full": "✓", "partial": "◐", "none": "–"}


def format_duration(seconds: float | None) -> str:
    if not seconds:
        return ""
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def format_size(size: int | None) -> str:
    if size is None:
        return ""
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KB", "MB", "GB"):
        value /= 1024
        if value < 1024 or unit == "GB":
            return f"{value:,.1f} {unit}" if value < 100 else f"{value:,.0f} {unit}"
    return ""


def _text_key(value: str | None):
    return (value is None or value == "", natural_key(value or ""))


def _number_key(value):
    return (value is None, value or 0)


@dataclass(frozen=True)
class Column:
    key: str
    title: str
    text: Callable[[Any], str]  # of a FileRow, or a SearchRow in the search's columns
    sort_key: Callable[[Any], Any]
    numeric: bool = False
    hidden_by_default: bool = False


COLUMNS: tuple[Column, ...] = (
    Column("name", "Name", lambda r: r.name, lambda r: natural_key(r.name)),
    Column("track", "#", lambda r: "" if r.track is None else str(r.track), lambda r: _number_key(r.track), True),
    Column("title", "Title",
           lambda r: (f"{r.n_tracks} tracks" if r.n_tracks else "") if r.is_dir else (r.title or ""),
           lambda r: _number_key(r.n_tracks) if r.is_dir else _text_key(r.title)),
    Column("artist", "Artist", lambda r: r.artist or "", lambda r: _text_key(r.artist)),
    Column("album", "Album", lambda r: r.album or "", lambda r: _text_key(r.album)),
    Column("year", "Year", lambda r: r.year or "", lambda r: _text_key(r.year)),
    Column("duration", "Duration", lambda r: format_duration(r.duration), lambda r: _number_key(r.duration), True),
    Column("format", "Format", lambda r: r.format or "", lambda r: _text_key(r.format)),
    Column("bitrate", "Bitrate", lambda r: f"{round(r.bitrate / 1000)} kbps" if r.bitrate else "",
           lambda r: _number_key(r.bitrate), True, hidden_by_default=True),
    Column("size", "Size", lambda r: "" if r.is_dir else format_size(r.size), lambda r: _number_key(r.size), True),
    Column("modified", "Modified",
           lambda r: datetime.fromtimestamp(r.mtime).strftime("%Y-%m-%d %H:%M") if r.mtime else "",
           lambda r: _number_key(r.mtime), hidden_by_default=True),
    Column("rg", "RG", lambda r: RG_MARKS.get(r.rg or "", ""), lambda r: _text_key(r.rg)),
    Column("lrclib", "LRCLIB", lambda r: r.lrclib or "", lambda r: _text_key(r.lrclib)),
)
COLUMN_INDEX = {column.key: i for i, column in enumerate(COLUMNS)}
LAYOUT_VERSION = "v2:"  # the saved header layout; v2 since the ♪ column went (M12)


class FileListModel(QAbstractTableModel):
    """The open folder's rows. The search results model reuses it with its own columns and rows."""

    renameRequested = Signal(int, str)  # entry id, the new name as typed
    columns: tuple[Column, ...] = COLUMNS

    def __init__(self, conn: Callable[[], sqlite3.Connection | None], parent=None) -> None:
        super().__init__(parent)
        self._conn = conn
        # The lyrics service's word on a pending key ("error", "offline"), or None. Set by the window.
        self.lrclib_status: Callable[[str], str | None] = lambda _key: None
        self.folder_id: int | None = None
        self.rows: list[FileRow] = []
        self.keys: list[tuple] = []  # precomputed sort keys, per row and column
        self._icons = {
            "dir": standard_icon(QStyle.StandardPixmap.SP_DirIcon),
            "audio": standard_icon(QStyle.StandardPixmap.SP_MediaVolume),
            "file": standard_icon(QStyle.StandardPixmap.SP_FileIcon),
        }
        self._dim = QApplication.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)

    # -- loading --

    def set_folder(self, folder_id: int | None) -> None:
        """Show another folder (a reset is right here: everything changes)."""
        self.beginResetModel()
        self.folder_id = folder_id
        conn = self._conn()
        self.rows = queries.folder_listing(conn, folder_id) if folder_id is not None and conn else []
        self.keys = [self._keys(row) for row in self.rows]
        self.endResetModel()

    def refresh(self) -> None:
        """Bring the open folder up to date in place: rows removed, changed and appended."""
        conn = self._conn()
        if self.folder_id is None or conn is None:
            return
        self._apply(queries.folder_listing(conn, self.folder_id))

    def _apply(self, rows: list[FileRow]) -> None:
        """Rows removed, changed (their data or their sort keys) and appended, in place: the selection stays."""
        fresh = {row.id: row for row in rows}
        for i in range(len(self.rows) - 1, -1, -1):
            if self.rows[i].id not in fresh:
                self.beginRemoveRows(_NO_PARENT, i, i)
                del self.rows[i], self.keys[i]
                self.endRemoveRows()
        for i, row in enumerate(self.rows):
            new = fresh.pop(row.id)
            keys = self._keys(new)
            if new != row or keys != self.keys[i]:
                self.rows[i], self.keys[i] = new, keys
                self.dataChanged.emit(self.index(i, 0), self.index(i, len(self.columns) - 1))
        if fresh:
            start = len(self.rows)
            self.beginInsertRows(_NO_PARENT, start, start + len(fresh) - 1)
            for row in fresh.values():
                self.rows.append(row)
                self.keys.append(self._keys(row))
            self.endInsertRows()

    def refresh_column(self, key: str) -> None:
        """A column whose text depends on something outside the rows (the LRCLIB column on the service's state)."""
        if self.rows:
            column = next(i for i, c in enumerate(self.columns) if c.key == key)
            self.dataChanged.emit(self.index(0, column), self.index(len(self.rows) - 1, column))

    def _keys(self, row: FileRow) -> tuple:
        return tuple(column.sort_key(row) for column in self.columns)

    def row_for(self, entry_id: int) -> int | None:
        return next((i for i, row in enumerate(self.rows) if row.id == entry_id), None)

    # -- Qt model API --

    def rowCount(self, parent: QModelIndex = _NO_PARENT) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = _NO_PARENT) -> int:
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.columns[section].title
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        column = self.columns[index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            if column.key == "lrclib" and row.lrclib == "pending" and row.lyrics_key:
                return self.lrclib_status(row.lyrics_key) or "pending"
            return column.text(row)
        if role == Qt.ItemDataRole.EditRole and index.column() == 0:
            return row.name
        if role == Qt.ItemDataRole.DecorationRole and index.column() == 0:
            return self._icons["dir" if row.is_dir else "audio" if row.is_audio else "file"]
        if role == Qt.ItemDataRole.ForegroundRole and not row.is_dir and not row.is_audio:
            return self._dim  # every file is shown; non-audio files are dimmed
        if role == Qt.ItemDataRole.TextAlignmentRole and column.numeric:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole and row.read_error:
            return f"The tags couldn't be read: {row.read_error}"
        if role == ENTRY_ID_ROLE:
            return row.id
        if role == IS_DIR_ROLE:
            return row.is_dir
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        # dragged into the playlist builder as entry ids only (ui/entry_drag.py): a drop in Explorer does nothing
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled
        if index.column() == 0:
            flags |= Qt.ItemFlag.ItemIsEditable  # inline rename (SPEC §11)
        return flags

    def setData(self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole) -> bool:
        """An inline rename: asked of the window, which renames on disk; the row follows from the index."""
        if role == Qt.ItemDataRole.EditRole and index.column() == 0 and isinstance(value, str):
            row = self.rows[index.row()]
            if value != row.name:
                self.renameRequested.emit(row.id, value)
        return False


class FileSortProxy(QSortFilterProxyModel):
    """Folders first in both directions, then the column's precomputed key; filtered by name."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setFilterKeyColumn(0)
        self.setDynamicSortFilter(True)

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        model = cast(FileListModel, self.sourceModel())
        a, b = model.rows[left.row()], model.rows[right.row()]
        if a.is_dir != b.is_dir:
            ascending = self.sortOrder() == Qt.SortOrder.AscendingOrder
            return a.is_dir if ascending else b.is_dir
        key_a, key_b = model.keys[left.row()][left.column()], model.keys[right.row()][right.column()]
        if key_a != key_b:
            return key_a < key_b
        return model.keys[left.row()][0] < model.keys[right.row()][0]  # ties: by name


class RenameDelegate(QStyledItemDelegate):
    """The inline editor selects only the stem of a file name, as Explorer does."""

    def setEditorData(self, editor, index: QModelIndex) -> None:
        super().setEditorData(editor, index)
        if isinstance(editor, QLineEdit) and not index.data(IS_DIR_ROLE):
            stem = len(Path(editor.text()).stem) if Path(editor.text()).suffix else len(editor.text())
            QTimer.singleShot(0, lambda: _select(editor, stem))


def _select(editor: QLineEdit, length: int) -> None:
    try:
        editor.setSelection(0, length)
    except RuntimeError:  # the editor closed meanwhile
        pass


class FileView(QWidget):
    folderActivated = Signal(int)  # a folder row was opened (Enter or double-click)
    fileActivated = Signal(int)  # a file row was opened: the app hands it to its default app
    selectionChanged = Signal(list)  # entry ids
    menuRequested = Signal(list, QPoint)  # selected entry ids in view order (empty: the background), global pos
    renameRequested = Signal(int, str)

    def __init__(self, conn: Callable[[], sqlite3.Connection | None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = FileListModel(conn, self)
        self.proxy = FileSortProxy(self)
        self.proxy.setSourceModel(self.model)

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter this folder by name (Esc to close)")
        self.filter.setClearButtonEnabled(True)
        self.filter.setToolTip("Show only the entries of this folder whose name contains this text (Ctrl+F)")
        self.filter.textChanged.connect(self.proxy.setFilterFixedString)
        self.filter.hide()

        self.view = EntryDragView()
        exempt(self.view, "the file list explains itself (SPEC §24)")
        self.view.setModel(self.proxy)
        self.view.setRootIsDecorated(False)
        self.view.setUniformRowHeights(True)
        self.view.setSortingEnabled(True)
        self.view.setAlternatingRowColors(True)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # Drags carry entry ids, never files (a drop in Explorer would move them: Ground rule 1), for the playlist.
        self.view.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.view.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.view.drag_ids = self.selected_ids
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._menu_requested)
        # Rename: F2 (below, always on the Name column) or a slow second click on a selected name.
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.SelectedClicked)
        self.view.setItemDelegateForColumn(0, RenameDelegate(self.view))
        self.model.renameRequested.connect(self.renameRequested)
        shortcut(QKeySequence(Qt.Key.Key_F2), self.view, self._rename_current, Qt.ShortcutContext.WidgetShortcut)
        self.view.activated.connect(self._activated)
        self.view.selectionModel().selectionChanged.connect(
            lambda *_: self.selectionChanged.emit(self.selected_ids()))
        header = self.view.header()
        header.setSectionsMovable(True)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(lambda pos: self.header_menu().exec(header.mapToGlobal(pos)))
        self.reset_layout()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.filter)
        layout.addWidget(self.view)

        shortcut(QKeySequence.StandardKey.Find, self, self.show_filter)
        shortcut(QKeySequence(Qt.Key.Key_Escape), self.filter, self.close_filter)

    # -- folder --

    def set_folder(self, folder_id: int | None) -> None:
        if folder_id != self.model.folder_id:
            self.close_filter()
        self.model.set_folder(folder_id)
        self.view.scrollToTop()

    def refresh(self) -> None:
        self.model.refresh()

    def selected_ids(self) -> list[int]:
        """The selected entries, top to bottom as shown (Play selection follows the view's sort order)."""
        rows = sorted(self.view.selectionModel().selectedRows(), key=lambda index: index.row())
        return [index.data(ENTRY_ID_ROLE) for index in rows]

    def visible_ids(self) -> list[int]:
        """Every row's entry id, top to bottom as shown (the selection after a delete)."""
        return [self.proxy.index(row, 0).data(ENTRY_ID_ROLE) for row in range(self.proxy.rowCount())]

    def is_dir(self, entry_id: int) -> bool:
        row = self.model.row_for(entry_id)
        return row is not None and self.model.rows[row].is_dir

    def _menu_requested(self, pos: QPoint) -> None:
        index = self.view.indexAt(pos)
        if index.isValid() and not self.view.selectionModel().isRowSelected(index.row()):
            self.view.setCurrentIndex(index)  # a right-click on an unselected row selects it, as in Explorer
        ids = self.selected_ids() if index.isValid() else []
        self.menuRequested.emit(ids, self.view.viewport().mapToGlobal(pos))

    def select_ids(self, entry_ids: list[int], reveal: bool = False) -> None:
        """Selects these rows, the first one current. `reveal` scrolls it into view."""
        selection = self.view.selectionModel()
        selection.clearSelection()
        flags = selection.SelectionFlag.Select | selection.SelectionFlag.Rows
        for n, entry_id in enumerate(entry_ids):
            row = self.model.row_for(entry_id)
            if row is None:
                continue
            index = self.proxy.mapFromSource(self.model.index(row, 0))
            if n == 0:
                selection.setCurrentIndex(index, flags)
                if reveal:
                    self.view.scrollTo(index)
            else:
                selection.select(index, flags)

    def visible_names(self) -> list[str]:
        """The Name column as shown, top to bottom (tests, and Play selection in the view's order later)."""
        return [self.proxy.index(row, 0).data() for row in range(self.proxy.rowCount())]

    def start_rename(self, entry_id: int) -> None:
        row = self.model.row_for(entry_id)
        if row is not None:
            index = self.proxy.mapFromSource(self.model.index(row, 0))
            self.view.setCurrentIndex(index)
            self.view.edit(index)

    def _rename_current(self) -> None:
        index = self.view.currentIndex()
        if index.isValid():
            self.start_rename(index.data(ENTRY_ID_ROLE))

    def _activated(self, index: QModelIndex) -> None:
        if index.data(IS_DIR_ROLE):
            self.folderActivated.emit(index.data(ENTRY_ID_ROLE))
        else:
            self.fileActivated.emit(index.data(ENTRY_ID_ROLE))

    # -- filter --

    def show_filter(self) -> None:
        self.filter.show()
        self.filter.setFocus()
        self.filter.selectAll()

    def close_filter(self) -> None:
        self.filter.clear()
        self.filter.hide()
        self.view.setFocus()

    # -- layout --

    def reset_layout(self) -> None:
        header = self.view.header()
        for i, column in enumerate(COLUMNS):
            header.moveSection(header.visualIndex(i), i)
            header.setSectionHidden(i, column.hidden_by_default)
        header.resizeSection(0, 280)
        self.view.sortByColumn(0, Qt.SortOrder.AscendingOrder)

    def save_layout(self) -> str:
        return LAYOUT_VERSION + base64.b64encode(self.view.header().saveState().data()).decode("ascii")

    def restore_layout(self, state: str) -> bool:
        """A layout saved for these columns. One saved before a column was removed would map the columns after it
        to the wrong sections (Qt restores by position), so it is dropped for the defaults."""
        if not state.startswith(LAYOUT_VERSION):
            return False
        try:
            data = QByteArray(base64.b64decode(state.removeprefix(LAYOUT_VERSION), validate=True))
        except ValueError:
            return False
        if not self.view.header().restoreState(data):
            return False
        header = self.view.header()
        self.proxy.sort(header.sortIndicatorSection(), header.sortIndicatorOrder())
        return True

    def header_menu(self) -> QMenu:
        header = self.view.header()
        menu = QMenu(self)
        for i, column in enumerate(COLUMNS):
            if i == 0:
                continue  # Name always stays
            action = QAction(column.title, menu, checkable=True)
            action.setChecked(not header.isSectionHidden(i))
            action.toggled.connect(lambda shown, section=i: header.setSectionHidden(section, not shown))
            menu.addAction(action)
        menu.addSeparator()
        menu.addAction("Reset columns", self.reset_layout)
        return with_status_tips(menu)

