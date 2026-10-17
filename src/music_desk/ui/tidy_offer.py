"""Tidy's offer window (SPEC §28): the images and cue sheets a run's splits made redundant, each to Keep or Delete.

A separate window, not modal. Every row is Keep until the user marks it Delete (a double-click, or the buttons
for the selection); Apply sends the Delete rows to the Recycle Bin after a confirmation. Closing the window keeps
every file. Nothing is deleted for good: a file whose drive has no Recycle Bin can't be marked Delete.
"""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from music_desk.core import fsops

KEEP, DELETE = "Keep", "Delete"
PATH_ROLE = Qt.ItemDataRole.UserRole


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


class OfferWindow(QWidget):
    revealRequested = Signal(str)  # a file to show in File Explorer
    applied = Signal(object, object)  # recycled paths, problems (list[str])

    def __init__(self, parent: QWidget | None, files: list[Path], *,
                 recycle: Callable[[Path], None] = fsops.send_to_recycle_bin,
                 check_recycle: Callable[[Path], str | None] = fsops.can_recycle) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.recycle = recycle
        self.setWindowTitle("Files Tidy made redundant")
        self.resize(760, 420)
        intro = QLabel("The albums below were split into tracks, so these images and cue sheets aren't needed to "
                       "play them any more. Every file is kept unless you mark it Delete; Apply sends the files "
                       "marked Delete to the Recycle Bin. Closing this window keeps them all.")
        intro.setWordWrap(True)
        self.list = QTreeWidget()
        self.list.setHeaderLabels(["File", "Folder", "Choice"])
        self.list.setRootIsDecorated(False)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setToolTip("Select files freely (Ctrl, Shift, Ctrl+A); a double-click switches Keep and Delete")
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.itemDoubleClicked.connect(lambda item, _column: self._toggle(item))
        for path in files:
            reason = check_recycle(path)
            item = QTreeWidgetItem([path.name, str(path.parent), KEEP])
            item.setData(0, PATH_ROLE, str(path))
            if reason:
                item.setText(2, f"{KEEP} (no Recycle Bin: {reason})")
                item.setDisabled(True)
            self.list.addTopLevelItem(item)
        self.list.setColumnWidth(0, 260)
        self.list.setColumnWidth(1, 320)

        self.keep_button = QPushButton("Keep selected")
        self.keep_button.setToolTip("Mark the selected files Keep")
        self.keep_button.clicked.connect(lambda: self._mark(KEEP))
        self.delete_button = QPushButton("Delete selected")
        self.delete_button.setToolTip("Mark the selected files Delete; nothing happens until Apply")
        self.delete_button.clicked.connect(lambda: self._mark(DELETE))
        self.apply_button = QPushButton("Apply")
        self.apply_button.setToolTip("Send the files marked Delete to the Recycle Bin, after asking")
        self.apply_button.clicked.connect(self.apply)
        self.close_button = QPushButton("Close")
        self.close_button.setToolTip("Close this window and keep every file")
        self.close_button.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addWidget(self.keep_button)
        buttons.addWidget(self.delete_button)
        buttons.addStretch(1)
        buttons.addWidget(self.apply_button)
        buttons.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.list, 1)
        layout.addLayout(buttons)
        self._update()

    # -- choices --

    def items(self) -> list[QTreeWidgetItem]:
        return [item for i in range(self.list.topLevelItemCount()) if (item := self.list.topLevelItem(i))]

    def choices(self) -> dict[str, str]:
        """Each file's choice, for tests."""
        return {item.data(0, PATH_ROLE): item.text(2).split(" ")[0] for item in self.items()}

    def marked(self) -> list[Path]:
        return [Path(item.data(0, PATH_ROLE)) for item in self.items() if item.text(2) == DELETE]

    def _mark(self, choice: str, items: list[QTreeWidgetItem] | None = None) -> None:
        for item in items if items is not None else self.list.selectedItems():
            if not item.isDisabled():
                item.setText(2, choice)
        self._update()

    def _toggle(self, item: QTreeWidgetItem) -> None:
        self._mark(KEEP if item.text(2) == DELETE else DELETE, [item])

    def _update(self) -> None:
        count = len(self.marked())
        self.apply_button.setEnabled(bool(count))
        self.apply_button.setText(f"Apply ({plural(count, 'file')} to the Recycle Bin)" if count else "Apply")

    def _menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        menu.addAction("Keep", lambda: self._mark(KEEP))
        menu.addAction("Delete", lambda: self._mark(DELETE))
        menu.addSeparator()
        menu.addAction("Open in File Explorer", lambda: self.revealRequested.emit(item.data(0, PATH_ROLE)))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    # -- Apply --

    def confirm_apply(self, count: int) -> bool:
        """Replaced in tests, which never open a real dialog."""
        answer = QMessageBox.question(self, "Send to the Recycle Bin",
                                      f"Send {plural(count, 'file')} to the Recycle Bin?\n\nThe tracks split from "
                                      "them stay. You can restore the files from the Recycle Bin.")
        return answer == QMessageBox.StandardButton.Yes

    def apply(self) -> None:
        marked = self.marked()
        if not marked or not self.confirm_apply(len(marked)):
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
