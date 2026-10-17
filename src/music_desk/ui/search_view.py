"""The library search page (SPEC §6): seek_lyrics' songs.html search box inside the app. The query is typed in
the toolbar's search field; this page shows the count and the results (folders and audio files), with the
match highlighted in the Title, Artist and Lyrics cells. The side panel is the detail pane.
"""

from typing import cast

from PySide6.QtCore import QModelIndex, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QKeySequence, QPainter, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from music_desk.core.fsops import natural_key
from music_desk.core.index.search import LIMIT, SearchRow
from music_desk.ui.entry_drag import EntryDragView
from music_desk.ui.file_view import COLUMNS, ENTRY_ID_ROLE, Column, FileListModel
from music_desk.ui.qt_util import shortcut

HIGHLIGHT_ROLE = Qt.ItemDataRole.UserRole + 10  # ((start, end), ...) in the cell's text

_BY_KEY = {column.key: column for column in COLUMNS}
# Folder sorts by the result order (natural folder, then name order), which is also the default order.
SEARCH_COLUMNS: tuple[Column, ...] = (
    _BY_KEY["name"],
    # A folder row's Title is empty (not its track count), and sorts with the other empty titles.
    Column("title", "Title", lambda r: r.title or "", lambda r: (not r.title, natural_key(r.title or ""))),
    _BY_KEY["artist"],
    Column("snippet", "Lyrics", lambda r: r.snippet, lambda r: natural_key(r.snippet)),
    _BY_KEY["album"],
    Column("folder", "Folder", lambda r: r.folder, lambda r: 0),  # the key comes from the model's rank
    _BY_KEY["track"],
    _BY_KEY["year"],
    _BY_KEY["duration"],
    _BY_KEY["format"],
    Column("bitrate", "Bitrate", _BY_KEY["bitrate"].text, _BY_KEY["bitrate"].sort_key, True, hidden_by_default=True),
    Column("size", "Size", _BY_KEY["size"].text, _BY_KEY["size"].sort_key, True, hidden_by_default=True),
    _BY_KEY["modified"],
    _BY_KEY["rg"],
    _BY_KEY["lrclib"],
)
SEARCH_COLUMN_INDEX = {column.key: i for i, column in enumerate(SEARCH_COLUMNS)}
_NO_PARENT = QModelIndex()
_ROW_FLAGS = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled
_SPANS = {"title": "title_spans", "artist": "artist_spans", "snippet": "snippet_spans"}


def sort_keys(rows: list[SearchRow]) -> list[tuple]:
    """Every row's sort key per column, computed on the search worker (thousands of rows would stall the UI).
    The Folder column's key is the result order: natural folder, then name order."""
    folder, name = SEARCH_COLUMN_INDEX["folder"], SEARCH_COLUMN_INDEX["name"]
    raw = []
    for rank, row in enumerate(rows):
        row_keys = [column.sort_key(row) for column in SEARCH_COLUMNS]
        row_keys[folder] = rank
        raw.append(row_keys)
    # Each column's key becomes the row's rank in that column's order (ties by name), so a header click on the
    # UI thread sorts integers. The ranks of one run are only compared among themselves (set_rows takes all).
    ranks = [[0] * len(SEARCH_COLUMNS) for _ in rows]
    order = sorted(range(len(rows)), key=lambda i: raw[i][name])
    for c in range(len(SEARCH_COLUMNS)):
        if c != name:
            # A stable sort of the name order keeps ties by name, comparing only this column's keys.
            order_c = sorted(order, key=lambda i: raw[i][c]) if c != folder else range(len(rows))
        else:
            order_c = order
        for position, i in enumerate(order_c):
            ranks[i][c] = position
    return [tuple(r) for r in ranks]


class SearchResultsModel(FileListModel):
    """Up to 5,000 rows, so it sorts itself (one list sort on precomputed keys) instead of going through a sort
    proxy, which would call Python for every comparison."""

    columns = SEARCH_COLUMNS

    def __init__(self, parent=None) -> None:
        super().__init__(lambda: None, parent)
        self._sort = (SEARCH_COLUMN_INDEX["folder"], Qt.SortOrder.AscendingOrder)

    def set_rows(self, rows: list[SearchRow], keys: list[tuple] | None = None, *, in_place: bool = False) -> None:
        """New results. `in_place` (the same query run again after the index changed): rows are removed, changed
        and added without a reset, so the selection stays. Otherwise the model is reset."""
        keys = keys if keys is not None else sort_keys(rows)
        if not in_place or not self.rows:
            self.beginResetModel()
            self.rows, self.keys = list(rows), list(keys)
            self._sort_rows()
            self.endResetModel()
            return
        fresh = {row.id: (row, key) for row, key in zip(rows, keys, strict=True)}
        gone = [i for i, row in enumerate(self.rows) if row.id not in fresh]
        for start, end in reversed(_ranges(gone)):  # contiguous runs, bottom up: one signal per run
            self.beginRemoveRows(_NO_PARENT, start, end)
            del self.rows[start:end + 1], self.keys[start:end + 1]
            self.endRemoveRows()
        for i, row in enumerate(self.rows):
            new, key = fresh.pop(row.id)
            if new != row or key != self.keys[i]:
                self.rows[i], self.keys[i] = new, key
                self.dataChanged.emit(self.index(i, 0), self.index(i, len(self.columns) - 1))
        if fresh:
            start = len(self.rows)
            self.beginInsertRows(_NO_PARENT, start, start + len(fresh) - 1)
            for row, key in fresh.values():
                self.rows.append(row)
                self.keys.append(key)
            self.endInsertRows()
        self.sort(*self._sort)

    def clear(self) -> None:
        self.beginResetModel()
        self.rows, self.keys = [], []
        self.endResetModel()

    def refresh(self) -> None:
        pass  # the window re-runs the query instead

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        """The header's sort: the selection follows its rows."""
        self._sort = (column, order)
        self.layoutAboutToBeChanged.emit()
        persistent = self.persistentIndexList()
        ids = [self.rows[index.row()].id if index.isValid() else None for index in persistent]
        self._sort_rows()
        position = {row.id: i for i, row in enumerate(self.rows)}
        self.changePersistentIndexList(persistent, [
            self.index(position[i], index.column()) if i in position else QModelIndex()
            for i, index in zip(ids, persistent, strict=True)])
        self.layoutChanged.emit()

    def _sort_rows(self) -> None:
        column, order = self._sort
        keys = self.keys
        ranked = sorted(range(len(self.rows)), key=lambda i: keys[i][column],
                        reverse=order == Qt.SortOrder.DescendingOrder)
        self.rows = [self.rows[i] for i in ranked]
        self.keys = [self.keys[i] for i in ranked]

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if role == HIGHLIGHT_ROLE and index.isValid():
            attr = _SPANS.get(self.columns[index.column()].key)
            return getattr(self.rows[index.row()], attr) if attr else ()
        if role == Qt.ItemDataRole.ToolTipRole and index.isValid() and not self.rows[index.row()].read_error:
            key = self.columns[index.column()].key
            if key in ("folder", "snippet"):
                return self.columns[index.column()].text(self.rows[index.row()]) or None
        return super().data(index, role)

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        # Asked for every row on a reset, so a constant (enum arithmetic is slow). Dragged as entry ids only, into
        # the playlist (ui/entry_drag.py); no rename (done from the file's folder).
        return _ROW_FLAGS if index.isValid() else Qt.ItemFlag.NoItemFlags


class HighlightDelegate(QStyledItemDelegate):
    """Paints a cell's text with its HIGHLIGHT_ROLE spans in bold, on the highlight colour (as the page did)."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        spans = index.data(HIGHLIGHT_ROLE)
        if not spans:
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""
        style = opt.widget.style() if opt.widget else QStyle()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        rect: QRect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, opt.widget).adjusted(3, 0, -3, 0)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        colour = opt.palette.color(QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text)
        mark = QColor(opt.palette.color(QPalette.ColorRole.Highlight))
        mark.setAlpha(70)
        bold = QFont(opt.font)
        bold.setBold(True)
        painter.save()
        painter.setClipRect(rect)
        x, end = rect.left(), rect.right()
        for piece, highlighted in _pieces(text, spans):
            font = bold if highlighted else opt.font
            metrics = QFontMetrics(font)
            width = metrics.horizontalAdvance(piece)
            if x + width > end:
                piece = metrics.elidedText(piece, Qt.TextElideMode.ElideRight, max(0, end - x))
                width = metrics.horizontalAdvance(piece)
            if highlighted and not selected:
                painter.fillRect(QRect(x, rect.top() + 1, width, rect.height() - 2), mark)
            painter.setFont(font)
            painter.setPen(colour)
            painter.drawText(QRect(x, rect.top(), width + 1, rect.height()),
                             int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), piece)
            x += width
            if x >= end:
                break
        painter.restore()


class ElideLeftDelegate(QStyledItemDelegate):
    """Folder paths keep their end (the album folder) when the column is narrow."""

    def initStyleOption(self, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        super().initStyleOption(option, index)
        option.textElideMode = Qt.TextElideMode.ElideLeft


def _ranges(indexes: list[int]) -> list[tuple[int, int]]:
    """Sorted indexes as (first, last) runs of consecutive values."""
    runs: list[tuple[int, int]] = []
    for i in indexes:
        if runs and i == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], i)
        else:
            runs.append((i, i))
    return runs


def _pieces(text: str, spans) -> list[tuple[str, bool]]:
    pieces, position = [], 0
    for start, stop in spans:
        if start > position:
            pieces.append((text[position:start], False))
        pieces.append((text[start:stop], True))
        position = stop
    if position < len(text):
        pieces.append((text[position:], False))
    return [p for p in pieces if p[0]]


class SearchView(QWidget):
    """The results page. The window owns the query and runs it; this page reports what the user does."""

    resultActivated = Signal(int)  # Enter or a double-click: go to the file in its folder, or open the folder
    selectionChanged = Signal(list)  # entry ids
    menuRequested = Signal(list, QPoint)
    playRequested = Signal(list)  # the shown results, in view order
    artistRequested = Signal(str)  # add artist:"…" to the query
    closeRequested = Signal()  # Esc

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = SearchResultsModel(self)

        count_row = QHBoxLayout()
        count_row.setSpacing(4)
        count_row.addStretch(1)
        self.count_label = QLabel()
        count_row.addWidget(self.count_label)
        self.play_button = QPushButton("Play results")
        self.play_button.setEnabled(False)
        self.play_button.clicked.connect(lambda: self.playRequested.emit(self.shown_ids()))
        count_row.addWidget(self.play_button)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.hide()

        self.view = EntryDragView()
        self.view.setModel(self.model)
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
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._menu_requested)
        delegate = HighlightDelegate(self.view)
        for key in _SPANS:
            self.view.setItemDelegateForColumn(SEARCH_COLUMN_INDEX[key], delegate)
        self.view.setItemDelegateForColumn(SEARCH_COLUMN_INDEX["folder"], ElideLeftDelegate(self.view))
        self.view.activated.connect(lambda index: self.resultActivated.emit(index.data(ENTRY_ID_ROLE)))
        self.view.clicked.connect(self._clicked)
        self.view.selectionModel().selectionChanged.connect(
            lambda *_: self.selectionChanged.emit(self.selected_ids()))
        header = self.view.header()
        header.setSectionsMovable(True)
        for i, column in enumerate(SEARCH_COLUMNS):
            header.setSectionHidden(i, column.hidden_by_default)
        for key, width in (("name", 200), ("title", 160), ("snippet", 260), ("folder", 240)):
            header.resizeSection(SEARCH_COLUMN_INDEX[key], width)
        self.view.sortByColumn(SEARCH_COLUMN_INDEX["folder"], Qt.SortOrder.AscendingOrder)
        shortcut(QKeySequence(Qt.Key.Key_Escape), self.view, self.closeRequested.emit,
                 Qt.ShortcutContext.WidgetShortcut)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addLayout(count_row)
        layout.addWidget(self.note)
        layout.addWidget(self.view)
        self.show_prompt()

    # -- results --

    def show_prompt(self) -> None:
        self.model.clear()
        self.count_label.setText("")
        self.play_button.setEnabled(False)
        self._set_note("Type to search folder and file names, titles, artists, albums and lyrics. Filters: "
                       "artist: album: year: genre: ext: lrclib:synced|plain|instrumental|"
                       "notfound|pending|error|notags rg:none|partial|full, and \"quoted text\".")

    def show_results(self, rows: list[SearchRow], total: int, errors: tuple[str, ...] = (),
                     failure: str | None = None, *, keys: list[tuple] | None = None, in_place: bool = False) -> None:
        """`keys` from sort_keys() (computed off the UI thread); `in_place` for the same query run again."""
        self.model.set_rows(rows, keys, in_place=in_place)
        shown = len(rows)
        text = f"{shown:,} of {total:,} results"
        if shown < total and shown >= LIMIT:
            text += f" (showing the first {LIMIT:,})"
        self.count_label.setText(text)
        self.play_button.setEnabled(bool(rows))
        self._set_note(failure or " ".join(errors))

    def _set_note(self, text: str) -> None:
        self.note.setText(text)
        self.note.setVisible(bool(text))

    def shown_ids(self) -> list[int]:
        """Every result, top to bottom as shown (Play results follows the view's sort order; it skips folders)."""
        return [row.id for row in self.model.rows]

    def column_texts(self, key: str) -> list[str]:
        """A column as shown, top to bottom (tests)."""
        column = SEARCH_COLUMN_INDEX[key]
        return [self.model.index(row, column).data() for row in range(self.model.rowCount())]

    def highlights(self, entry_id: int, key: str) -> tuple:
        row = self.model.row_for(entry_id)
        if row is None:
            return ()
        return self.model.index(row, SEARCH_COLUMN_INDEX[key]).data(HIGHLIGHT_ROLE)

    def row(self, entry_id: int) -> SearchRow | None:
        row = self.model.row_for(entry_id)
        return cast(SearchRow, self.model.rows[row]) if row is not None else None

    # -- selection --

    def selected_ids(self) -> list[int]:
        rows = sorted(self.view.selectionModel().selectedRows(), key=lambda index: index.row())
        return [index.data(ENTRY_ID_ROLE) for index in rows]

    def visible_ids(self) -> list[int]:
        """Every result's entry id, top to bottom as shown (the selection after a delete)."""
        return [self.model.index(row, 0).data(ENTRY_ID_ROLE) for row in range(self.model.rowCount())]

    def select_ids(self, entry_ids: list[int]) -> None:
        selection = self.view.selectionModel()
        selection.clearSelection()
        flags = selection.SelectionFlag.Select | selection.SelectionFlag.Rows
        for n, entry_id in enumerate(entry_ids):
            row = self.model.row_for(entry_id)
            if row is None:
                continue
            index = self.model.index(row, 0)
            if n == 0:
                selection.setCurrentIndex(index, flags)
            else:
                selection.select(index, flags)

    def focus_results(self) -> None:
        self.view.setFocus()
        if self.model.rowCount() and not self.view.selectionModel().hasSelection():
            self.view.setCurrentIndex(self.model.index(0, 0))

    def _clicked(self, index: QModelIndex) -> None:
        """Ctrl+click on an artist adds it as a filter (the page's artist checkboxes)."""
        if index.column() == SEARCH_COLUMN_INDEX["artist"] and \
                QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier and index.data():
            self.artistRequested.emit(index.data())

    def _menu_requested(self, pos: QPoint) -> None:
        index = self.view.indexAt(pos)
        if index.isValid() and not self.view.selectionModel().isRowSelected(index.row()):
            self.view.setCurrentIndex(index)
        ids = self.selected_ids() if index.isValid() else []
        if ids:
            self.menuRequested.emit(ids, self.view.viewport().mapToGlobal(pos))

