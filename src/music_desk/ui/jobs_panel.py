"""The jobs panel (SPEC §10): a bottom drawer listing this session's Rename, ReplayGain and Tidy runs, running or
finished, with their progress. Open shows a job's page (its live log while it runs, its report once done), Stop
stops it, and Clear finished empties the list. The jobs keep running while you browse.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from music_desk.services.jobs import DONE, FAILED, RUNNING, STOPPED, Job, JobList
from music_desk.ui.qt_util import top_level_text

COLUMNS = ("Job", "Folder", "Status", "Started")
STATE_TEXT = {RUNNING: "Running", DONE: "Done", STOPPED: "Stopped", FAILED: "Failed"}


def status_text(job: Job) -> str:
    if job.running and job.progress and job.progress[1] > 1:
        done, total = job.progress
        return f"{STATE_TEXT[job.state]} ({min(done + 1, total)} of {total}): {job.status}"
    return f"{STATE_TEXT[job.state]}: {job.status}" if job.status else STATE_TEXT[job.state]


class JobsPanel(QWidget):
    openRequested = Signal(int)  # job id
    stopRequested = Signal(int)

    def __init__(self, jobs: JobList, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.jobs = jobs
        self._items: dict[int, QTreeWidgetItem] = {}
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(COLUMNS)
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tree.setColumnWidth(0, 90)
        self.tree.setColumnWidth(1, 320)
        self.tree.itemDoubleClicked.connect(
            lambda item, _col: self.openRequested.emit(item.data(0, Qt.ItemDataRole.UserRole)))
        self.tree.currentItemChanged.connect(lambda *_args: self._update_buttons())

        self.open_button = QPushButton("Open")
        self.open_button.clicked.connect(lambda: self._emit(self.openRequested))
        self.open_button.setToolTip("Show the selected job's page or report (or double-click it)")
        self.stop_button = QPushButton("Stop")
        self.stop_button.setToolTip("Stop the selected job; each tool says in its report what it left as it was")
        self.stop_button.clicked.connect(lambda: self._emit(self.stopRequested))
        self.clear_button = QPushButton("Clear finished")
        self.clear_button.setToolTip("Remove the finished jobs from this list; their files stay as they are")
        self.clear_button.clicked.connect(jobs.clear_finished)
        buttons = QVBoxLayout()
        for button in (self.open_button, self.stop_button, self.clear_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.tree, 1)
        layout.addLayout(buttons)

        jobs.changed.connect(self._changed)
        jobs.removed.connect(self._removed)
        for job in jobs.jobs:
            self._changed(job.id)
        self._update_buttons()

    def selected_job(self) -> Job | None:
        item = self.tree.currentItem()
        return self.jobs.get(item.data(0, Qt.ItemDataRole.UserRole)) if item is not None else None

    def select(self, job_id: int) -> None:
        item = self._items.get(job_id)
        if item is not None:
            self.tree.setCurrentItem(item)

    def rows(self) -> list[tuple[str, ...]]:
        """The rows as shown, top to bottom (tests)."""
        return [tuple(top_level_text(self.tree, i, c) for c in range(len(COLUMNS)))
                for i in range(self.tree.topLevelItemCount())]

    def _changed(self, job_id: int) -> None:
        job = self.jobs.get(job_id)
        if job is None:
            return
        item = self._items.get(job_id)
        if item is None:
            item = QTreeWidgetItem()
            item.setData(0, Qt.ItemDataRole.UserRole, job_id)
            self.tree.insertTopLevelItem(0, item)  # the newest first
            self._items[job_id] = item
            self.tree.setCurrentItem(item)
        item.setText(0, job.kind)
        item.setText(1, str(job.folder))
        item.setToolTip(1, f"{job.folder}\nLocked while the job runs: tag edits and renames there are refused."
                        if job.running else str(job.folder))
        item.setText(2, status_text(job))
        item.setToolTip(2, status_text(job))
        item.setText(3, job.started.strftime("%H:%M:%S"))
        self._update_buttons()

    def _removed(self, job_id: int) -> None:
        item = self._items.pop(job_id, None)
        if item is not None:
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item))
        self._update_buttons()

    def _emit(self, signal) -> None:
        job = self.selected_job()
        if job is not None:
            signal.emit(job.id)

    def _update_buttons(self) -> None:
        job = self.selected_job()
        self.open_button.setEnabled(job is not None)
        self.stop_button.setEnabled(job is not None and job.running)
        self.clear_button.setEnabled(any(not j.running for j in self.jobs.jobs))
