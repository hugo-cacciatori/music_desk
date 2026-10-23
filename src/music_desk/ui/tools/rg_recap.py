"""The ReplayGain recap window (SPEC §30): outside the habitual case, every file the run touches, by folder, with
what happens to it. OK starts the run, Cancel does nothing; it has no options of its own."""

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from music_desk.core.replaygain import Recap


class RecapDialog(QDialog):
    def __init__(self, recap: Recap, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ReplayGain")
        self.setModal(True)
        self.resize(760, 520)
        scope = "as a discography" if recap.discography else "as one album"
        files = sum(len(each.rows) for each in recap.folders)
        self.heading = QLabel(f"ReplayGain on <b>{recap.folder}</b>, {scope}. What happens to each file:")
        self.heading.setWordWrap(True)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["File", "What happens"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self._tops: list[QTreeWidgetItem] = []
        for each in recap.folders:
            top = QTreeWidgetItem([str(each.folder), ""])
            self._tops.append(top)
            top.addChildren([QTreeWidgetItem([name, what]) for name, what in each.rows])
            self.tree.addTopLevelItem(top)
        self.tree.expandAll()
        self.tree.resizeColumnToContents(0)
        self.note = QLabel(f"{files} file{'s' if files != 1 else ''} listed. To change what the run does, change "
                           "the ReplayGain options (File > Options…) or the selection, and click ReplayGain again.")
        self.note.setWordWrap(True)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.heading)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.note)
        layout.addWidget(self.buttons)

    def rows(self) -> list[tuple[str, str, str]]:
        """(folder, file, what happens) as listed, for tests."""
        return [(top.text(0), child.text(0), child.text(1)) for top in self._tops
                for child in (top.child(j) for j in range(top.childCount())) if child is not None]
