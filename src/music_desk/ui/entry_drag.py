"""Dragging library items inside the app (SPEC §15): the folder tree, the file list and the search results drag
their selection into the playlist builder.

The drag carries entry ids in an app-only format, never file URLs or text, and offers only a copy. File Explorer
(and any other app) understands neither, so a drop outside MusicDesk does nothing (Ground rule 1).
"""

import json
from collections.abc import Callable

from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QTreeView

ENTRY_IDS_MIME = "application/x-musicdesk-entry-ids"


def entry_mime(ids: list[int]) -> QMimeData:
    mime = QMimeData()
    mime.setData(ENTRY_IDS_MIME, json.dumps([int(i) for i in ids]).encode("ascii"))
    return mime


def entry_ids(mime: QMimeData) -> list[int]:
    """The ids a drag carries; anything malformed gives none."""
    if not mime.hasFormat(ENTRY_IDS_MIME):
        return []
    try:
        data = json.loads(bytes(mime.data(ENTRY_IDS_MIME)).decode("ascii"))
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    return [i for i in data if isinstance(i, int) and not isinstance(i, bool)]


class EntryDragView(QTreeView):
    """A view whose drag carries `drag_ids()` (its selection in the order shown, which a proxy model's mimeData
    would lose), as a copy only."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.drag_ids: Callable[[], list[int]] = lambda: []

    def startDrag(self, supported_actions) -> None:  # noqa: N802 - Qt's name
        ids = self.drag_ids()
        if not ids:
            return
        drag = QDrag(self)
        drag.setMimeData(entry_mime(ids))
        drag.exec(Qt.DropAction.CopyAction)
