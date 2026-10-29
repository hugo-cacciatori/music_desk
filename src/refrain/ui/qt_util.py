"""Small Qt helpers that keep the type checker informed (SPEC §18 "Static types")."""

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QStyle, QTableWidget, QWidget


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


_standard_icons: dict[QStyle.StandardPixmap, QIcon] = {}


def standard_icon(pixmap: QStyle.StandardPixmap) -> QIcon:
    """The style's standard icon, read once per run: on Windows each read asks the shell (a few ms), and a main
    window took 0.1 s of its 0.2 s to build re-reading the same few (measured 2026-10-23, SPEC §18)."""
    icon = _standard_icons.get(pixmap)
    if icon is None:
        icon = _standard_icons[pixmap] = app().style().standardIcon(pixmap)
    return icon


def cell_text(table: QTableWidget, row: int, column: int) -> str:
    """A table cell's text ("" for a cell without an item)."""
    item = table.item(row, column)
    return item.text() if item is not None else ""
