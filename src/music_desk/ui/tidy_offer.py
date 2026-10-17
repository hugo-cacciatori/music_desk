"""Tidy's offer window, "Redundant files" (SPEC §28): the images and cue sheets a run's splits made redundant.

A separate window, not modal. Each file has a checkbox, unchecked; "Delete selected" sends the checked files to the
Recycle Bin at once, without a question. Closing the window keeps every file. Nothing is deleted for good: a file
whose drive has no Recycle Bin can't be checked.
"""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from music_desk.core import fsops

PATH_ROLE = Qt.ItemDataRole.UserRole


class OfferWindow(QWidget):
    revealRequested = Signal(str)  # a file to show in File Explorer
    applied = Signal(object, object)  # recycled paths, problems (list[str])

    def __init__(self, parent: QWidget | None, files: list[Path], *,
                 recycle: Callable[[Path], None] = fsops.send_to_recycle_bin,
                 check_recycle: Callable[[Path], str | None] = fsops.can_recycle) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.recycle = recycle
        self.setWindowTitle("Redundant files")
        self.resize(760, 420)
        intro = QLabel("The albums below were split into tracks, so these images and cue sheets aren't needed to "
                       "play them any more. Check the ones to delete, then click Delete selected: they go to the "
                       "Recycle Bin. Closing this window keeps them all.")
        intro.setWordWrap(True)
        self.list = QTreeWidget()
        self.list.setHeaderLabels(["File", "Folder"])
        self.list.setRootIsDecorated(False)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setToolTip("Check the files to send to the Recycle Bin")
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        for path in files:
            reason = check_recycle(path)
            item = QTreeWidgetItem([path.name, str(path.parent)])
            item.setData(0, PATH_ROLE, str(path))
            item.setCheckState(0, Qt.CheckState.Unchecked)
            if reason:
                item.setText(0, f"{path.name} (no Recycle Bin: {reason})")
                item.setToolTip(0, f"Kept: it can't go to the Recycle Bin ({reason})")
                item.setDisabled(True)
            self.list.addTopLevelItem(item)
        self.list.setColumnWidth(0, 300)
        self.list.itemChanged.connect(lambda _item, _column: self._update())

        self.delete_button = QPushButton("Delete selected")
        self.delete_button.setToolTip("Send the checked files to the Recycle Bin now")
        self.delete_button.clicked.connect(self.apply)
        self.close_button = QPushButton("Close")
        self.close_button.setToolTip("Close this window and keep every file")
        self.close_button.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.delete_button)
        buttons.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.list, 1)
        layout.addLayout(buttons)
        self._update()

    # -- the checkboxes --

    def items(self) -> list[QTreeWidgetItem]:
        return [item for i in range(self.list.topLevelItemCount()) if (item := self.list.topLevelItem(i))]

    def marked(self) -> list[Path]:
        """The checked files (a disabled row is never checked)."""
        return [Path(item.data(0, PATH_ROLE)) for item in self.items()
                if not item.isDisabled() and item.checkState(0) == Qt.CheckState.Checked]

    def _update(self) -> None:
        self.delete_button.setEnabled(bool(self.marked()))

    def _menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        menu.addAction("Open in File Explorer", lambda: self.revealRequested.emit(item.data(0, PATH_ROLE)))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    # -- Delete selected --

    def apply(self) -> None:
        marked = self.marked()
        if not marked:
            return
        recycled, problems = [], []
        for path in marked:
            try:
                self.recycle(path)
                recycled.append(path)
            except fsops.CannotRecycle as e:  # never deleted for good instead (SPEC §19)
                problems.append(f"{path.name}: kept, it can't go to the Recycle Bin ({e})")
            except OSError as e:
                problems.append(f"{path.name}: couldn't be sent to the Recycle Bin ({e})")
        self.applied.emit(recycled, problems)
        self.close()
