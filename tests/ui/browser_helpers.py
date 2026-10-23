"""Helpers for the browser tests."""

from PySide6.QtCore import QModelIndex

from music_desk.ui.file_view import COLUMN_INDEX


def entry(service, *names) -> int:
    folder = service.roots()[0].top_entry_id
    for name in names:
        folder = service.conn.execute("SELECT id FROM entries WHERE parent_id = ? AND name = ?",
                                      (folder, name)).fetchone()[0]
    return folder


def child_names(model, parent: QModelIndex) -> list[str]:
    return [model.index(row, 0, parent).data() for row in range(model.rowCount(parent))]


def column(window, key) -> list[str]:
    proxy = window.file_view.proxy
    return [proxy.index(row, COLUMN_INDEX[key]).data() for row in range(proxy.rowCount())]
