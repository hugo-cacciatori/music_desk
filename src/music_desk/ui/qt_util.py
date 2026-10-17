"""Small Qt helpers that keep the type checker informed (SPEC §18 "Static types")."""

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QTableWidget, QTreeWidget, QWidget


def shortcut(key: QKeySequence | QKeySequence.StandardKey, parent: QWidget, slot: Callable[[], object],
             context: Qt.ShortcutContext = Qt.ShortcutContext.WindowShortcut) -> QShortcut:
    """A QShortcut on `parent` that calls `slot`. PySide accepts `activated=` and `context=` in the constructor,
    but its stubs don't, so this connects them the long way."""
    result = QShortcut(key, parent)
    result.setContext(context)
    result.activated.connect(slot)
    return result


def app() -> QApplication:
    """The running QApplication (the window and its widgets only exist while one does)."""
    instance = QApplication.instance()
    assert isinstance(instance, QApplication)
    return instance


def cell_text(table: QTableWidget, row: int, column: int) -> str:
    """A table cell's text ("" for a cell without an item)."""
    item = table.item(row, column)
    return item.text() if item is not None else ""


def top_level_text(tree: QTreeWidget, row: int, column: int) -> str:
    """A top-level tree row's text in one column ("" past the last row)."""
    item = tree.topLevelItem(row)
    return item.text(column) if item is not None else ""
