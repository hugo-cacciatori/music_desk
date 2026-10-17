"""The playlist builder's panel (SPEC §15): a narrow vertical column between the file list and the side panel,
showing the playlist in progress (core/playlist_builder.py) with customisable columns.

A header click sorts the playlist itself, since its order is what gets exported. A track that left the index is
dimmed, with its path as the tooltip. Rows come from the index (queries.track_rows), so no file is read.

Music comes in by dragging folders and songs from the tree, the list or the search results (entry ids, see
ui/entry_drag.py); rows are moved by dragging them, or with Ctrl+Up/Down. Play and Export… go out through the
window, which writes the files off the UI thread.
"""

import base64
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QByteArray, QMimeData, QModelIndex, QRect, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence, QPainter, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from music_desk.core.fsops import natural_key
from music_desk.core.index import queries
from music_desk.core.index.queries import TrackRow
from music_desk.core.index.scanner import find_folder
from music_desk.core.playlist_builder import Builder, Item
from music_desk.core.playlists import PlaylistEntry
from music_desk.ui.entry_drag import ENTRY_IDS_MIME, entry_ids
from music_desk.ui.file_view import format_duration
from music_desk.ui.qt_util import shortcut

PLACEHOLDER = "Drag folders or songs here to build a playlist"
PLAYLIST_ROWS_MIME = "application/x-musicdesk-playlist-rows"  # a drag of the playlist's own rows
_NO_PARENT = QModelIndex()


def _text(value) -> tuple:
    return (value is None or value == "", natural_key(value or ""))


def _number(value) -> tuple:
    return (value is None, value or 0)


@dataclass(frozen=True)
class PlaylistColumn:
    key: str
    title: str
    text: Callable[[TrackRow], str]
    sort_key: Callable[[TrackRow], object]
    numeric: bool = False
    shown_by_default: bool = False


COLUMNS: tuple[PlaylistColumn, ...] = (
    # the row's place, filled by the model; sorting by it restores the order tracks were added in
    PlaylistColumn("position", "#", lambda r: "", lambda r: 0, True, True),
    PlaylistColumn("title", "Title", lambda r: r.title or Path(r.name).stem, lambda r: _text(r.title or r.name),
                   shown_by_default=True),
    PlaylistColumn("artist", "Artist", lambda r: r.artist or "", lambda r: _text(r.artist), shown_by_default=True),
    PlaylistColumn("album", "Album", lambda r: r.album or "", lambda r: _text(r.album)),
    PlaylistColumn("albumartist", "Album artist", lambda r: r.albumartist or "", lambda r: _text(r.albumartist)),
    PlaylistColumn("track", "Track", lambda r: "" if r.track is None else str(r.track), lambda r: _number(r.track),
                   True),
    PlaylistColumn("disc", "Disc", lambda r: "" if r.disc is None else str(r.disc), lambda r: _number(r.disc), True),
    PlaylistColumn("year", "Year", lambda r: r.year or "", lambda r: _text(r.year)),
    PlaylistColumn("genre", "Genre", lambda r: r.genre or "", lambda r: _text(r.genre)),
    PlaylistColumn("duration", "Duration", lambda r: format_duration(r.duration), lambda r: _number(r.duration), True,
                   True),
    PlaylistColumn("format", "Format", lambda r: r.format or "", lambda r: _text(r.format)),
    PlaylistColumn("name", "File name", lambda r: r.name, lambda r: natural_key(r.name)),
    PlaylistColumn("folder", "Folder", lambda r: r.folder, lambda r: natural_key(r.folder)),
)
COLUMN_INDEX = {column.key: i for i, column in enumerate(COLUMNS)}


class PlaylistModel(QAbstractTableModel):
    """The builder's items as rows. `changed` follows every change of the playlist itself (to save it)."""

    changed = Signal()
    added = Signal(int, int)  # a drop of library items: (tracks added, tracks skipped as already there)
    rowsDropped = Signal(list)  # rows moved by a drag, at their new positions (the view selects them)

    def __init__(self, conn: Callable[[], sqlite3.Connection | None], builder: Builder | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self._conn = conn
        self.builder = builder or Builder()
        self.rows: dict[int, TrackRow] = {}  # the index's view of the items that are in it
        self._added: dict[object, int] = {}  # item key -> the order it was added in: "#" sorts by it
        self._number_added(self.builder.items)
        self._dim = QApplication.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)

    # -- the index --

    def refresh(self) -> None:
        """Bring every item up to date with the index: renames (ids kept) give new paths, a rebuilt index finds
        the file again by path, and a file gone from the index stays listed, dimmed."""
        conn = self._conn()
        if conn is None:
            return
        ids = [i for i in self.builder.ids if i is not None]
        rows = queries.track_rows(conn, ids)

        def lookup(item: Item) -> Item:
            if item.entry_id is not None and item.entry_id in rows:
                return Item(item.entry_id, str(rows[item.entry_id].path))
            found = _find_track(conn, item.path)
            return Item(found, item.path) if found is not None else Item(None, item.path)

        before = list(self.builder.items)
        self.builder.resolve(lookup)
        new_ids = [i.entry_id for i in self.builder.items if i.entry_id is not None and i.entry_id not in rows]
        if new_ids:
            rows.update(queries.track_rows(conn, new_ids))
        self.rows = rows
        if self.rowCount():
            self.dataChanged.emit(self.index(0, 0), self.index(self.rowCount() - 1, len(COLUMNS) - 1))
        if self.builder.items != before:
            self.changed.emit()

    def row_for(self, item: Item) -> TrackRow | None:
        return self.rows.get(item.entry_id) if item.entry_id is not None else None

    def missing(self, row: int) -> bool:
        return self.row_for(self.builder.items[row]) is None

    # -- changes to the playlist --

    def _number_added(self, items: list[Item]) -> None:
        for item in items:
            self._added.setdefault(item.key, len(self._added))

    def add(self, items: list[Item], position: int | None = None) -> tuple[int, int]:
        self.beginResetModel()
        result = self.builder.add(items, position)
        self._number_added(items)
        self.endResetModel()
        self.refresh()
        self.changed.emit()
        return result

    def add_entries(self, ids: list[int], position: int | None = None) -> tuple[int, int]:
        """Dropped library items (folders and files, in the view's order): their tracks, in Explorer's order."""
        conn = self._conn()
        if conn is None:
            return 0, 0
        tracks = queries.track_ids_for(conn, ids)
        rows = queries.track_rows(conn, tracks)
        result = self.add([Item(i, str(rows[i].path)) for i in tracks if i in rows], position) if tracks else (0, 0)
        self.added.emit(*result)
        return result

    def remove_rows(self, rows: list[int]) -> None:
        """Delete: out of the playlist (the files are untouched)."""
        if not rows:
            return
        self.beginResetModel()
        self.builder.remove(rows)
        self.endResetModel()
        self.changed.emit()

    def clear(self) -> None:
        self.beginResetModel()
        self.builder.clear()
        self._added = {}
        self.endResetModel()
        self.changed.emit()

    def move_rows(self, rows: list[int], delta: int) -> list[int]:
        """Ctrl+Up / Ctrl+Down. Returns the rows' new positions (to keep them selected)."""
        return self._move(lambda: self.builder.move_rows(rows, delta))

    def move_rows_to(self, rows: list[int], position: int) -> list[int]:
        """A drag inside the list: the rows go before the row at `position`."""
        return self._move(lambda: self.builder.move_to(rows, position))

    def _move(self, move: Callable[[], list[int]]) -> list[int]:
        before = list(self.builder.items)
        self.layoutAboutToBeChanged.emit()
        moved = move()
        self.layoutChanged.emit()
        if self.builder.items != before:
            self.changed.emit()
        return moved

    def set_builder(self, builder: Builder) -> None:
        self.beginResetModel()
        self.builder = builder
        self.rows = {}
        self._added = {}
        self._number_added(builder.items)
        self.endResetModel()
        self.refresh()

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        """A header click: the playlist's order itself changes. "#" sorts by the order tracks were added in (so a
        sort by another column can be undone); missing tracks sort last, by path. The same request always gives
        the same order, since Qt may ask twice for one click (QTreeView.sortByColumn does)."""
        if not 0 <= column < len(COLUMNS) or not self.builder.items:
            return
        reverse = order == Qt.SortOrder.DescendingOrder
        self.layoutAboutToBeChanged.emit()
        if COLUMNS[column].key == "position":
            self.builder.sort(lambda item: self._added.get(item.key, len(self._added)), reverse=reverse)
        else:
            sort_key = COLUMNS[column].sort_key

            def key(item: Item):
                row = self.row_for(item)
                return (0, sort_key(row)) if row is not None else (1, natural_key(item.path))
            self.builder.sort(key, reverse=reverse)
        self.layoutChanged.emit()
        self.changed.emit()

    # -- Qt model API --

    def rowCount(self, parent: QModelIndex = _NO_PARENT) -> int:
        return 0 if parent.isValid() else len(self.builder.items)

    def columnCount(self, parent: QModelIndex = _NO_PARENT) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section].title
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        item = self.builder.items[index.row()]
        column = COLUMNS[index.column()]
        row = self.row_for(item)
        if role == Qt.ItemDataRole.DisplayRole:
            if column.key == "position":
                return str(index.row() + 1)
            if row is None:  # not in the index any more: its file name where the title goes
                return Path(item.path).stem if column.key == "title" else Path(item.path).name \
                    if column.key == "name" else ""
            return column.text(row)
        if role == Qt.ItemDataRole.ForegroundRole and row is None:
            return self._dim
        if role == Qt.ItemDataRole.ToolTipRole:
            if row is None:
                return f"Not in the library any more: {item.path}. It is left out of exports and playback."
            return str(row.path)
        if role == Qt.ItemDataRole.TextAlignmentRole and column.numeric:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.ItemIsDropEnabled  # drops land between rows, or at the end
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled

    # -- drag and drop: copies only, so Qt never removes rows after a drop (the moves are done here) --

    def mimeTypes(self) -> list[str]:  # noqa: N802 - Qt's name
        return [ENTRY_IDS_MIME, PLAYLIST_ROWS_MIME]

    def supportedDropActions(self) -> Qt.DropAction:  # noqa: N802 - Qt's name
        return Qt.DropAction.CopyAction

    def supportedDragActions(self) -> Qt.DropAction:  # noqa: N802 - Qt's name
        return Qt.DropAction.CopyAction

    def mimeData(self, indexes: list[QModelIndex]) -> QMimeData:  # noqa: N802 - Qt's name
        mime = QMimeData()
        rows = sorted({index.row() for index in indexes if index.isValid()})
        mime.setData(PLAYLIST_ROWS_MIME, json.dumps(rows).encode("ascii"))
        return mime

    def dropMimeData(self, data: QMimeData, action: Qt.DropAction, row: int, column: int,  # noqa: N802
                     parent: QModelIndex) -> bool:
        if action == Qt.DropAction.IgnoreAction:
            return True
        position = row if row >= 0 else parent.row() if parent.isValid() else len(self.builder.items)
        if data.hasFormat(PLAYLIST_ROWS_MIME):
            try:
                rows = json.loads(bytes(data.data(PLAYLIST_ROWS_MIME).data()).decode("ascii"))
            except ValueError:
                return False
            moved = self.move_rows_to([r for r in rows if isinstance(r, int)], position)
            self.rowsDropped.emit(moved)
            return bool(moved)
        ids = entry_ids(data)
        if not ids:
            return False
        self.add_entries(ids, position)
        return True

    # -- totals --

    def total_duration(self) -> float:
        return sum((r.duration or 0) for item in self.builder.items if (r := self.row_for(item)) is not None)


def _find_track(conn: sqlite3.Connection, path: str) -> int | None:
    """An item's entry by its path (after an index rebuild, or a file back in the library); tracks only."""
    found = find_folder(conn, Path(path).parent)
    if found is None or not found[1]:
        return None
    entry_id = queries.find_child(conn, found[0], Path(path).name)
    if entry_id is None:
        return None
    return entry_id if conn.execute("SELECT 1 FROM tracks WHERE entry_id = ?", (entry_id,)).fetchone() else None


class PlaylistView(QTreeView):
    """The list, with the placeholder drawn on its empty background."""

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt's name
        super().paintEvent(event)
        if self.model() is not None and self.model().rowCount() == 0:
            painter = QPainter(self.viewport())
            painter.setPen(self.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text))
            area = QRect(self.viewport().rect()).adjusted(16, 16, -16, -16)
            painter.drawText(area, int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap), PLACEHOLDER)
            painter.end()


class PlaylistPanel(QWidget):
    """The panel: a title with Clear, the list, a footer with the count and the total duration, and the buttons
    that play or export it."""

    playRequested = Signal()
    exportRequested = Signal()

    def __init__(self, conn: Callable[[], sqlite3.Connection | None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = PlaylistModel(conn, parent=self)
        self.title = QLabel("PLAYLIST")
        self.title.setStyleSheet("font-weight: 600; letter-spacing: 1px;")
        self.view = PlaylistView()
        self.view.setModel(self.model)
        self.view.setRootIsDecorated(False)
        self.view.setUniformRowHeights(True)
        self.view.setAlternatingRowColors(True)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setSortingEnabled(True)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.view.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.view.setDropIndicatorShown(True)
        self.model.rowsDropped.connect(self.select_rows)
        for key, action in ((Qt.Modifier.CTRL | Qt.Key.Key_Up, lambda: self.move_selected(-1)),
                            (Qt.Modifier.CTRL | Qt.Key.Key_Down, lambda: self.move_selected(1)),
                            (Qt.Key.Key_Delete, self.remove_selected)):
            shortcut(QKeySequence(key), self.view, action, Qt.ShortcutContext.WidgetShortcut)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        header = self.view.header()
        header.setSectionsMovable(True)
        header.setSortIndicatorShown(False)  # the playlist has no standing sort: a click reorders it once
        header.sectionClicked.connect(lambda _section: header.setSortIndicatorShown(True))
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(lambda pos: self.header_menu().exec(header.mapToGlobal(pos)))
        self.reset_layout()
        self.footer = QLabel()
        self.clear_button = QToolButton()
        self.clear_button.setText("Clear")
        self.clear_button.setToolTip("Empty the playlist (the files are untouched)")
        self.clear_button.clicked.connect(self.clear)
        self.play_button = QPushButton("Play")
        self.play_button.setToolTip("Open the playlist with the default player")
        self.play_button.clicked.connect(self.playRequested)
        self.export_button = QPushButton("Export…")
        self.export_button.setToolTip("Save the playlist as a .m3u8 file")
        self.export_button.clicked.connect(self.exportRequested)
        self.relative_paths = QCheckBox("Relative paths")
        self.relative_paths.setToolTip("Paths relative to the saved file, so the playlist keeps working when it "
                                       "moves together with the music. Songs on another drive stay absolute.")
        self.model.modelReset.connect(self._update_footer)
        self.model.changed.connect(self._update_footer)
        self.model.layoutChanged.connect(self._update_footer)

        top = QHBoxLayout()
        top.addWidget(self.title)
        top.addStretch(1)
        top.addWidget(self.clear_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 4)
        layout.setSpacing(4)
        layout.addLayout(top)
        layout.addWidget(self.view, 1)
        layout.addWidget(self.footer)
        buttons = QHBoxLayout()
        buttons.addWidget(self.play_button)
        buttons.addWidget(self.export_button)
        buttons.addWidget(self.relative_paths)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self._update_footer()

    def _update_footer(self) -> None:
        count = self.model.rowCount()
        self.clear_button.setEnabled(count > 0)
        playable = any(not self.model.missing(row) for row in range(count))
        self.play_button.setEnabled(playable)
        self.export_button.setEnabled(playable)
        if not count:
            self.footer.setText("Empty")
            return
        duration = format_duration(self.model.total_duration()) or "0:00"
        missing = sum(1 for row in range(count) if self.model.missing(row))
        text = f"{count:,} track{'s' if count != 1 else ''} · {duration}"
        if missing:
            text += f" · {missing} not in the library"
        self.footer.setText(text)

    def entries(self) -> list[PlaylistEntry]:
        """What Play and Export write: the tracks in the playlist's order, without those gone from the library."""
        return [PlaylistEntry(row.path, row.duration, row.artist, row.title)
                for item in self.model.builder.items if (row := self.model.row_for(item)) is not None]

    # -- arranging --

    def selected_rows(self) -> list[int]:
        return sorted(index.row() for index in self.view.selectionModel().selectedRows())

    def select_rows(self, rows: list[int]) -> None:
        selection = self.view.selectionModel()
        selection.clearSelection()
        flags = selection.SelectionFlag.Select | selection.SelectionFlag.Rows
        for row in rows:
            selection.select(self.model.index(row, 0), flags)
        if rows:
            selection.setCurrentIndex(self.model.index(rows[0], 0), selection.SelectionFlag.NoUpdate)
            self.view.scrollTo(self.model.index(rows[-1], 0))
            self.view.scrollTo(self.model.index(rows[0], 0))

    def move_selected(self, delta: int) -> None:
        rows = self.selected_rows()
        if rows:
            self.select_rows(self.model.move_rows(rows, delta))

    def remove_selected(self) -> None:
        rows = self.selected_rows()
        if not rows:
            return
        self.model.remove_rows(rows)
        if self.model.rowCount():  # the row after the removed ones, as a list does
            self.select_rows([min(rows[0], self.model.rowCount() - 1)])

    def clear(self) -> None:
        count = self.model.rowCount()
        if count and self.ask_clear(count):
            self.model.clear()

    def ask_clear(self, count: int) -> bool:
        """Replaced in tests."""
        answer = QMessageBox.question(
            self, "Clear the playlist",
            f"Remove all {count:,} track{'s' if count != 1 else ''} from the playlist? The files are untouched.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
        return answer == QMessageBox.StandardButton.Yes

    # -- columns --

    def reset_layout(self) -> None:
        header = self.view.header()
        for i, column in enumerate(COLUMNS):
            header.moveSection(header.visualIndex(i), i)
            header.setSectionHidden(i, not column.shown_by_default)
        # fit the default columns in the panel's 300 px, so nothing scrolls sideways at first
        for key, width in (("position", 32), ("title", 112), ("artist", 84), ("duration", 48)):
            header.resizeSection(COLUMN_INDEX[key], width)

    def save_layout(self) -> str:
        return base64.b64encode(self.view.header().saveState().data()).decode("ascii")

    def restore_layout(self, state: str) -> bool:
        if not state:
            return False
        try:
            data = QByteArray(base64.b64decode(state, validate=True))
        except ValueError:
            return False
        restored = self.view.header().restoreState(data)
        self.view.header().setSortIndicatorShown(False)
        return restored

    def header_menu(self) -> QMenu:
        header = self.view.header()
        menu = QMenu(self)
        for i, column in enumerate(COLUMNS):
            if column.key == "title":
                continue  # the title always stays
            action = QAction(column.title, menu, checkable=True)
            action.setChecked(not header.isSectionHidden(i))
            action.toggled.connect(lambda shown, section=i: header.setSectionHidden(section, not shown))
            menu.addAction(action)
        menu.addSeparator()
        menu.addAction("Reset columns", self.reset_layout)
        return menu

    # -- tests --

    def column_texts(self, key: str) -> list[str]:
        column = COLUMN_INDEX[key]
        return [self.model.index(row, column).data() for row in range(self.model.rowCount())]
