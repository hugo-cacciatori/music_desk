"""The Redundant files window (SPEC §32, §28): one window for Clean up and Tidy's offer.

A separate window, not modal, titled after the target folder. One row per item (a file, or a whole folder), every
box checked: the user unchecks what to keep. Kept items are listed below, greyed, with their reason. Delete
selected recycles the checked items on a worker (services/recycle_job.py), then the window becomes its report
and stays open until closed. Closing it before keeps everything.
"""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from music_desk.core import fsops
from music_desk.core.cleanup import Item
from music_desk.core.path_locks import LockedError, PathLocks
from music_desk.services.recycle_job import RecycleReport, RecycleRunner, report_text

INDEX_ROLE = Qt.ItemDataRole.UserRole


class RecycleWindow(QWidget):
    revealRequested = Signal(str, bool)  # a path to show in File Explorer, and whether it is a folder
    finished = Signal(object)  # RecycleReport, after Delete selected
    closed = Signal()  # the window was closed (a right-click entry alone ends with it, SPEC §31)

    def __init__(self, parent: QWidget | None, folder: Path, items: list[Item], *, locks: PathLocks, owner: str,
                 intro: str, recycle: Callable[[Path], None] = fsops.send_to_recycle_bin,
                 check_recycle: Callable[[Path], str | None] = fsops.can_recycle) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.owner = owner
        self.recycle = recycle
        self.state = "list"  # deleting, report
        self.report: RecycleReport | None = None
        self.setWindowTitle(f"Redundant files: {folder.name or folder}")
        self.resize(860, 460)
        self.intro = QLabel(intro)
        self.intro.setWordWrap(True)

        self.items: list[Item] = []
        self.list = QTreeWidget()
        self.list.setHeaderLabels(["File", "Folder", "Why"])
        self.list.setRootIsDecorated(False)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        kept = []
        for item in items:
            reason = item.kept or (f"kept: {why}" if (why := check_recycle(item.path)) else None)
            if reason:
                kept.append((item, reason))
                continue
            row = self._row(item, item.why)
            row.setCheckState(0, Qt.CheckState.Checked)
            row.setToolTip(0, "Checked: goes to the Recycle Bin on Delete selected. Uncheck it to keep it")
        grey = self.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
        for item, reason in kept:  # below the others, greyed, with no checkbox: nothing is left out silently
            row = self._row(item, reason)
            for column in range(3):
                row.setForeground(column, grey)
            row.setFlags(row.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
        self.list.setColumnWidth(0, 320)
        self.list.setColumnWidth(1, 300)
        self.list.itemChanged.connect(lambda _item, _column: self._update())

        self.report_view = QPlainTextEdit()
        self.report_view.setReadOnly(True)
        self.progress = QLabel()

        self.check_all = QPushButton("Check all")
        self.check_all.clicked.connect(lambda: self._check_all(Qt.CheckState.Checked))
        self.uncheck_all = QPushButton("Uncheck all")
        self.uncheck_all.clicked.connect(lambda: self._check_all(Qt.CheckState.Unchecked))
        checks = QHBoxLayout()
        checks.addWidget(self.check_all)
        checks.addWidget(self.uncheck_all)
        checks.addStretch(1)

        self.delete_button = QPushButton("Delete selected")
        self.delete_button.setToolTip("Send the checked items to the Recycle Bin now")
        self.delete_button.clicked.connect(self.apply)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setToolTip("Stop after the item being sent now; the others stay")
        self.stop_button.clicked.connect(lambda: self.runner.cancel())
        self.close_button = QPushButton("Close")
        self.close_button.setToolTip("Close this window; whatever is still listed is kept")
        self.close_button.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addWidget(self.progress, 1)
        buttons.addWidget(self.delete_button)
        buttons.addWidget(self.stop_button)
        buttons.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.intro)
        layout.addLayout(checks)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.report_view, 1)
        layout.addLayout(buttons)

        self.runner = RecycleRunner(locks, self)
        self.runner.progress.connect(self._progress)
        self.runner.finished.connect(self._finished)
        self._show("list")

    def _row(self, item: Item, why: str) -> QTreeWidgetItem:
        row = QTreeWidgetItem([item.name, str(item.path.parent), why])
        row.setData(0, INDEX_ROLE, len(self.items))
        self.items.append(item)
        self.list.addTopLevelItem(row)
        return row

    # -- the checkboxes --

    def rows(self) -> list[QTreeWidgetItem]:
        return [row for i in range(self.list.topLevelItemCount()) if (row := self.list.topLevelItem(i))]

    def _checkable(self, row: QTreeWidgetItem) -> bool:
        return bool(row.flags() & Qt.ItemFlag.ItemIsUserCheckable)

    def marked(self) -> list[Item]:
        """The checked items (a kept row has no checkbox)."""
        return [self.items[row.data(0, INDEX_ROLE)] for row in self.rows()
                if self._checkable(row) and row.checkState(0) == Qt.CheckState.Checked]

    def kept_rows(self) -> list[tuple[str, str]]:
        """(file, why) of the greyed rows, for tests."""
        return [(row.text(0), row.text(2)) for row in self.rows() if not self._checkable(row)]

    def _check_all(self, state: Qt.CheckState) -> None:
        for row in self.rows():
            if self._checkable(row):
                row.setCheckState(0, state)

    def _update(self) -> None:
        self.delete_button.setEnabled(self.state == "list" and bool(self.marked()))

    def _menu(self, pos) -> None:
        row = self.list.itemAt(pos)
        if row is None:
            return
        item = self.items[row.data(0, INDEX_ROLE)]
        menu = QMenu(self)
        menu.addAction("Open in File Explorer", lambda: self.revealRequested.emit(str(item.path), item.is_folder))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    # -- Delete selected, then the report --

    def apply(self) -> None:
        marked = self.marked()
        if not marked or self.state != "list":
            return
        try:
            self.runner.run(marked, self.owner, self.recycle)
        except LockedError as e:
            self.progress.setText(f"{e.path} is in use by {e.owner}. Try again when it has finished.")
            return
        self._show("deleting")

    def _progress(self, done: int, total: int, name: str) -> None:
        self.progress.setText(f"Sending to the Recycle Bin ({done + 1} of {total}): {name}" if name else "")

    def _finished(self, report: RecycleReport) -> None:
        self.report = report
        self.report_view.setPlainText(report_text(report))
        self.progress.setText("")
        self._show("report")
        self.finished.emit(report)

    def _show(self, state: str) -> None:
        self.state = state
        listing = state in ("list", "deleting")
        self.list.setVisible(listing)
        self.list.setEnabled(state == "list")
        self.check_all.setVisible(state == "list")
        self.uncheck_all.setVisible(state == "list")
        self.report_view.setVisible(state == "report")
        self.delete_button.setVisible(state != "report")
        self.stop_button.setVisible(state == "deleting")
        self.close_button.setEnabled(state != "deleting")
        if state == "report":
            self.intro.setText("Done. What went to the Recycle Bin, and what stayed:")
        self._update()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.state == "deleting":  # Stop first: the report follows, between two items
            self.runner.cancel()
            event.ignore()
            return
        self.runner.close()
        super().closeEvent(event)
        self.closed.emit()
