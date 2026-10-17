"""The Rename tool's preview and result (SPEC §10 A): the dry run, always shown before Apply.

The preview lists every music file "Old name → New name" with a status (rename, fallback mask, unchanged, missing
tags, name taken, unreadable, kept), filterable by status; junk files and the .lrc sidecars that follow their
music file are listed separately, and the counts use the script's summary wording. After Apply, the result shows
what happened, in the same sections.
"""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from music_desk.core import rename
from music_desk.core.rename import PlanItem, RenamePlan, RenameReport, Status
from music_desk.ui.qt_util import cell_text

ALL = "All files"
COLUMNS = ("Folder", "Name", "New name", "Status", "Details")


def row_status(item: PlanItem) -> str:
    """The status label of a preview row (also the filter's key)."""
    if item.status is Status.RENAME and item.fallback_missing is not None:
        return "fallback mask"
    if item.status is Status.KEPT:
        return "kept"
    return item.status.value


def row_detail(item: PlanItem) -> str:
    if item.status is Status.RENAME and item.fallback_missing is not None:
        return f"missing: {item.fallback_missing}"
    if item.status is Status.MISSING_TAGS:
        return f"missing: {item.detail}"
    if item.status is Status.KEPT:
        return item.detail
    if item.status is Status.RENAME and item.sidecar is not None and item.sidecar.status is Status.RENAME:
        return f"{item.sidecar.source.name} follows"
    return item.detail


def sections_text(sections: list[tuple[str, list[str]]]) -> str:
    lines = []
    for title, entries in sections:
        lines.append(title)
        lines.extend(f"    {entry}" for entry in entries)
        lines.append("")
    return "\n".join(lines).rstrip()


class RenamePreview(QWidget):
    backRequested = Signal()  # back to the Tools page (cancels a preview still reading)
    applyRequested = Signal()
    cancelRequested = Signal()  # stop a run in progress
    doneRequested = Signal()  # back to the file list

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.plan: RenamePlan | None = None
        self.report: RenameReport | None = None
        self.state = "idle"  # planning, preview, applying, result, error
        self.tidy = False  # step 1 of Tidy: Apply also starts ReplayGain, even with nothing to rename

        self.back_button = QPushButton("← Back to options")
        self.back_button.clicked.connect(self.backRequested)
        self.heading = QLabel()
        self.heading.setWordWrap(True)
        header = QHBoxLayout()
        header.addWidget(self.back_button)
        header.addWidget(self.heading, 1)

        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setMaximumWidth(120)
        self.busy.setTextVisible(False)
        self.message = QLabel()
        self.message.setWordWrap(True)
        status_row = QHBoxLayout()
        status_row.addWidget(self.busy)
        status_row.addWidget(self.message, 1)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.filter = QComboBox()
        self.filter.currentIndexChanged.connect(self._apply_filter)
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Show:"))
        filter_row.addWidget(self.filter)
        filter_row.addStretch(1)
        self.filter_widget = QWidget()
        self.filter_widget.setLayout(filter_row)
        filter_row.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().hide()
        self.table.setSortingEnabled(True)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header_view.setStretchLastSection(True)

        self.extras = QPlainTextEdit()  # junk files, sidecars and warnings; then the result
        self.extras.setReadOnly(True)
        self.extras.setMaximumHeight(160)

        self.apply_button = QPushButton("Apply renames")
        self.apply_button.clicked.connect(self.applyRequested)
        self.back_bottom = QPushButton("← Back to options")  # next to Apply: return without changing anything
        self.back_bottom.clicked.connect(self.backRequested)
        self.cancel_button = QPushButton("Stop")
        self.cancel_button.clicked.connect(self.cancelRequested)
        self.done_button = QPushButton("Done")
        self.done_button.clicked.connect(self.doneRequested)
        buttons = QHBoxLayout()
        buttons.addWidget(self.apply_button)
        buttons.addWidget(self.back_bottom)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        buttons.addWidget(self.done_button)

        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addLayout(status_row)
        layout.addWidget(self.summary)
        layout.addWidget(self.filter_widget)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.extras)
        layout.addLayout(buttons)
        self._show("idle", "")

    # -- states --

    def show_planning(self, folder: Path, selection: int = 0, tidy: bool = False) -> None:
        self.plan, self.report, self.tidy = None, None, tidy
        what = f"{selection} selected file{'s' if selection != 1 else ''} in {folder}" if selection else str(folder)
        title = "Tidy, step 1 of 2: check the renames" if tidy else "Rename: check the changes"
        self.heading.setText(f"<b>{title}</b> · {what}")
        self.apply_button.setText("Apply renames, then run ReplayGain" if tidy else "Apply renames")
        self.table.setRowCount(0)
        self._show("planning", "Reading the tags… Nothing is renamed until you apply.")

    def show_plan(self, plan: RenamePlan) -> None:
        self.plan = plan
        report = rename.preview_report(plan)
        sections = rename.summary_sections(report, dry_run=True)
        self.summary.setText("  ·  ".join(title for title, _entries in sections if title != "Warnings"))
        self._fill_table(plan)
        self.extras.setPlainText(self._extras_text(plan))
        self.extras.setVisible(bool(self.extras.toPlainText()))
        actionable = any(i.status in (Status.RENAME, Status.JUNK) for i in plan.items)
        back = "Back to options changes nothing."
        if self.tidy and actionable:
            message = ("Nothing has changed yet. Apply renames exactly these files, then runs ReplayGain on the final "
                       f"names. {back}")
        elif self.tidy:
            message = f"Nothing to rename. Run ReplayGain goes straight to step 2. {back}"
            self.apply_button.setText("Run ReplayGain")
        elif actionable:
            message = f"Nothing has changed yet. Apply renames exactly these files. {back}"
        else:
            message = "Nothing to rename: every file already has its name, or can't be renamed."
        self._show("preview", message)
        self.apply_button.setEnabled(actionable or self.tidy)

    def show_applying(self) -> None:
        self._show("applying", "Renaming… Files in this folder can't be edited meanwhile.")

    def show_report(self, report: RenameReport) -> None:
        self.report = report
        self.summary.setText("")
        self.extras.setPlainText(sections_text(rename.summary_sections(report, dry_run=False)))
        self.extras.setVisible(True)
        message = "Stopped: the files after that point were left as they were." if report.cancelled else "Done."
        self._show("result", message)

    def show_error(self, text: str) -> None:
        self._show("error", text)

    def _show(self, state: str, message: str) -> None:
        self.state = state
        self.message.setText(message)
        self.busy.setVisible(state in ("planning", "applying"))
        preview = state == "preview"
        self.summary.setVisible(preview and bool(self.summary.text()))
        self.filter_widget.setVisible(preview)
        self.table.setVisible(preview)
        if state == "result":
            self.extras.setMaximumHeight(16777215)
        else:
            self.extras.setMaximumHeight(160)
        self.extras.setVisible(state == "result" or (preview and bool(self.extras.toPlainText())))
        self.apply_button.setVisible(preview)
        self.back_bottom.setVisible(preview)
        self.back_button.setVisible(state != "applying")  # no way back while files are being renamed
        self.cancel_button.setVisible(state in ("planning", "applying"))
        self.done_button.setVisible(state in ("result", "error"))
        self.done_button.setText("Done" if state == "result" else "Close")

    # -- the table --

    def _fill_table(self, plan: RenamePlan) -> None:
        rows = [item for item in plan.items if item.status is not Status.JUNK]
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            folder = item.source.parent.relative_to(plan.folder).as_posix() if item.source.parent != plan.folder \
                else ""
            values = (folder, item.source.name, item.target.name if item.target else "", row_status(item),
                      row_detail(item))
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.table.setSortingEnabled(True)
        self.table.sortItems(1, Qt.SortOrder.AscendingOrder)  # by folder, then name (the sort is stable)
        self.table.sortItems(0, Qt.SortOrder.AscendingOrder)
        self.table.resizeColumnsToContents()
        counts: dict[str, int] = {}
        for item in rows:
            counts[row_status(item)] = counts.get(row_status(item), 0) + 1
        self.filter.blockSignals(True)
        self.filter.clear()
        self.filter.addItem(f"{ALL} ({len(rows)})", ALL)
        for status, count in sorted(counts.items()):
            self.filter.addItem(f"{status} ({count})", status)
        self.filter.blockSignals(False)
        self._apply_filter()

    def _apply_filter(self, *_args) -> None:
        wanted = self.filter.currentData()
        for row in range(self.table.rowCount()):
            status = cell_text(self.table, row, 3)
            self.table.setRowHidden(row, wanted not in (None, ALL) and status != wanted)

    def visible_rows(self) -> list[tuple[str, ...]]:
        return [tuple(cell_text(self.table, r, c) for c in range(len(COLUMNS)))
                for r in range(self.table.rowCount()) if not self.table.isRowHidden(r)]

    @staticmethod
    def _extras_text(plan: RenamePlan) -> str:
        junk = [i.source.relative_to(plan.folder).as_posix() for i in plan.items if i.status is Status.JUNK]
        sidecars = [f"{i.sidecar.source.name} → {i.sidecar.new_path.name}" for i in plan.items
                    if i.status is Status.RENAME and i.sidecar is not None and i.sidecar.status is Status.RENAME]
        sections = []
        if junk:
            sections.append((f"{rename.count_files(len(junk))} would be sent to the Recycle Bin as junk "
                             "(you confirm first)", junk))
        if sidecars:
            sections.append((f"{len(sidecars)} .lrc file{'s' if len(sidecars) != 1 else ''} would follow "
                             f"{'their' if len(sidecars) != 1 else 'its'} music file", sidecars))
        if plan.warnings:
            sections.append(("Warnings", plan.warnings))
        return sections_text(sections)
