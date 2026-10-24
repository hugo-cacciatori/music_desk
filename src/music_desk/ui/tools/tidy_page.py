"""The Tidy page (SPEC §28): the whole plan before anything runs, then the run's live log and its report.

One button runs everything: Tidy says what it will do to each album (split, rename, ReplayGain, nothing), and
Run Tidy does it. There is no separate ReplayGain step to start.
"""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from music_desk.core import cleanup, rename
from music_desk.core.tidy import AlbumTidy, FolderPlan, Kind, TidyPlan
from music_desk.ui.tools.rename_card import Card


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def folder_actions(plan: FolderPlan) -> list[str]:
    """What Tidy does in one folder, in words."""
    if plan.kind is Kind.SKIPPED:
        return [f"Skipped: {plan.skip}"]
    actions = []
    if plan.kind is Kind.NOT_SPLIT and plan.split is not None:
        actions.append(f"Split {plan.split.cue.name} into {plural(len(plan.split.written), 'track')}")
    if plan.rename_count:
        actions.append(f"Rename {plural(plan.rename_count, 'file')}")
    return actions


def clutter_text(items: list[cleanup.Item]) -> str:
    """"Offer 3 pictures, 1 video, 1 folder"."""
    counts = [(sum(item.why == cleanup.PICTURE_BESIDE_MUSIC for item in items), "picture"),
              (sum(item.why == cleanup.VIDEO_BESIDE_MUSIC for item in items), "video"),
              (sum(item.is_folder for item in items), "folder")]
    return "Offer " + ", ".join(plural(count, noun) for count, noun in counts if count)


def album_text(album: AlbumTidy) -> str:
    if album.tidy:
        return "Already tidy"
    actions = folder_actions(album.own)
    if any(folder_actions(disc) for disc in album.folders[1:]):
        actions.append("work in its disc folders")
    if album.replaygain is not None:
        actions.append(f"ReplayGain ({album.replaygain})")
    text = ", then ".join(actions)
    if album.clutter:
        offer = clutter_text(album.clutter)
        text = f"{text}; {offer[0].lower()}{offer[1:]}" if text else offer
    return text or "Nothing to do"


class TidyPage(QWidget):
    backRequested = Signal()
    runRequested = Signal()
    stopRequested = Signal()
    doneRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = "empty"  # planning, plan, running, result, error
        self.standalone = False  # in a window of its own (the right-click menu, SPEC §31): Close, not Back
        self.back_button = QPushButton("← Back")
        self.back_button.setToolTip("Back to the file list; nothing has been changed")
        self.back_button.clicked.connect(self.backRequested)
        self.heading = QLabel()
        self.heading.setWordWrap(True)
        self.heading.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        header = QHBoxLayout()
        header.addWidget(self.back_button)
        header.addWidget(self.heading, 1)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        font = self.summary.font()
        font.setBold(True)
        self.summary.setFont(font)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Album", "What Tidy will do"])
        self.tree.setToolTip("Every album found, with what Tidy will do to it; expand one to see its renames")
        self.tree.setColumnWidth(0, 360)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)

        self.busy = QProgressBar()
        self.busy.setMaximumWidth(160)
        self.run_button = Card.primary(QPushButton("Run Tidy"))
        self.run_button.setToolTip("Do everything listed: split, rename, then ReplayGain, album by album")
        self.run_button.clicked.connect(self.runRequested)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setToolTip("Stop after the current step; a split leaves nothing half-written")
        self.stop_button.clicked.connect(self.stopRequested)
        self.done_button = QPushButton("Done")
        self.done_button.setToolTip("Close this page and go back to the file list")
        self.done_button.clicked.connect(self.doneRequested)
        buttons = QHBoxLayout()
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.stop_button)
        buttons.addStretch(1)
        buttons.addWidget(self.busy)
        buttons.addWidget(self.done_button)

        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(self.summary)
        layout.addWidget(self.message)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.log, 1)
        layout.addLayout(buttons)
        self._show("empty")

    def set_standalone(self) -> None:
        """In a window of its own: Back becomes Close (hidden while Tidy runs: Stop, then Done), Done closes."""
        self.standalone = True
        self.back_button.setText("Close")
        self.done_button.setToolTip("Close this window")
        self._show(self.state)

    # -- states --

    def show_planning(self, folder: Path) -> None:
        self.heading.setText(f"<b>Tidy</b> · {folder}")
        self.summary.setText("Reading the albums…")
        self.busy.setRange(0, 0)
        self.tree.clear()
        self.log.clear()
        self._show("planning")

    def set_plan_progress(self, done: int, total: int, folder: str) -> None:
        if self.state == "planning" and total:
            self.busy.setRange(0, total)
            self.busy.setValue(done)
            self.message.setText(f"Reading {folder}" if folder else "")

    def show_plan(self, plan: TidyPlan) -> None:
        self.tree.clear()
        root = plan.root
        for album in plan.albums:
            item = QTreeWidgetItem([self._relative(album.folder, root), album_text(album)])
            for folder in album.folders:
                if folder.folder != album.folder:
                    disc = QTreeWidgetItem([self._relative(folder.folder, root),
                                            ", then ".join(folder_actions(folder)) or "Nothing to do"])
                    item.addChild(disc)
                    self._add_details(disc, folder)
                else:
                    self._add_details(item, folder)
            for each in album.clutter:
                item.addChild(QTreeWidgetItem([self._relative(each.path, album.folder) if each.is_folder
                                               else each.name, f"{each.why}: offered at the end"]))
            self.tree.addTopLevelItem(item)
        by_parent: dict[Path, list] = {}
        for each in plan.loose:  # clutter folders outside any album, under their parent folder
            by_parent.setdefault(each.path.parent, []).append(each)
        for parent, items in by_parent.items():
            texts = [f"Offer {each.why} {each.path.name}" if each.why == "empty folder"
                     else f"Offer folder {each.path.name} ({each.why.removeprefix('folder of ')})" for each in items]
            self.tree.addTopLevelItem(QTreeWidgetItem([self._relative(parent, root), "; ".join(texts)]))
        self.summary.setText(plan.summary() + ("." if plan.has_work else ". Nothing to do."))
        self.message.setText("" if not plan.has_work else
                             "Run Tidy does all of this, album by album. Files made redundant by a split (the image "
                             "and its cue sheet) and the files offered are kept; you choose which to recycle at the "
                             "end.")
        self.run_button.setEnabled(plan.has_work)
        self._show("plan")

    def show_error(self, text: str) -> None:
        self.summary.setText("Tidy can't run.")
        self.message.setText(text)
        self._show("error")

    def show_running(self) -> None:
        self.log.clear()
        self.summary.setText("Tidying… Files in this folder can't be edited meanwhile.")
        self.busy.setRange(0, 0)
        self._show("running")

    def set_progress(self, done: int, total: int, album: str) -> None:
        if total:
            self.busy.setRange(0, total)
            self.busy.setValue(done)
        if album and album != "done":
            self.message.setText(f"Album {done + 1} of {total}: {album}")

    def add_lines(self, lines: list[str]) -> None:
        self.log.appendPlainText("\n".join(lines))

    def show_result(self, text: str, heading: str | None = None) -> None:
        if heading:
            self.heading.setText(heading)
        self.summary.setText(text.split("\n", 1)[0])
        self.log.setPlainText(text)
        self._show("result")

    def _show(self, state: str) -> None:
        self.state = state
        self.tree.setVisible(state == "plan")
        self.log.setVisible(state in ("running", "result"))
        self.message.setVisible(state in ("planning", "plan", "running", "error") and bool(self.message.text()))
        self.busy.setVisible(state in ("planning", "running"))
        self.run_button.setVisible(state == "plan")
        self.stop_button.setVisible(state == "running")
        self.done_button.setVisible(state in ("result", "error"))
        if self.standalone:
            self.back_button.setVisible(state in ("empty", "planning", "plan"))
            self.back_button.setToolTip("Close this window; nothing has been changed")
            return
        self.back_button.setToolTip(
            "Back to the file list; Tidy goes on, and the Jobs panel shows it again" if state == "running"
            else "Back to the file list; nothing has been changed")

    # -- the tree --

    @staticmethod
    def _relative(folder: Path, root: Path) -> str:
        try:
            relative = folder.relative_to(root)
        except ValueError:
            return str(folder)
        return str(relative) if str(relative) != "." else folder.name

    @staticmethod
    def _add_details(item: QTreeWidgetItem, folder: FolderPlan) -> None:
        if folder.renames is not None:
            for each in folder.renames.with_status(rename.Status.RENAME):
                item.addChild(QTreeWidgetItem([each.source.name, f"→ {each.new_path.name}"]))
        for note in folder.notes:
            item.addChild(QTreeWidgetItem(["", note]))
        for path in folder.offer:
            item.addChild(QTreeWidgetItem([path.name, "kept; offered for deletion at the end"]))

    def rows(self) -> list[tuple[str, str]]:
        """The top-level rows, for tests."""
        items = [item for i in range(self.tree.topLevelItemCount()) if (item := self.tree.topLevelItem(i))]
        return [(item.text(0), item.text(1)) for item in items]
