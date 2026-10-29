"""Help › About MusicDesk (SPEC §12): the versions, and the third-party components with their licences."""

import platform

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import Signal, qVersion
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from music_desk import __version__
from music_desk.licenses import COMPONENTS
from music_desk.ui import theme
from music_desk.ui.qt_util import cell_text

HEADERS = ("Component", "Version", "Licence")


class AboutDialog(QDialog):
    licensesRequested = Signal()  # "Open licences folder": the window opens it off the UI thread

    def __init__(self, rsgain_version: str | None, parent: QWidget | None = None, *,
                 flac_version: str | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About MusicDesk")
        title = QLabel(f"<b>MusicDesk {__version__}</b>")
        font = title.font()
        font.setPixelSize(theme.ABOUT_TITLE_PX)
        title.setFont(font)
        self.versions = QLabel(
            f"Python {platform.python_version()} · Qt {qVersion()} (PySide6 {pyside_version})\n"
            f"ReplayGain: {rsgain_version or 'rsgain unavailable'} · "
            f"Cue split: {flac_version or 'flac unavailable'}")
        self.table = QTableWidget(len(COMPONENTS), len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        for row, component in enumerate(COMPONENTS):
            for column, text in enumerate((component.name, component.installed_version(), component.licence)):
                self.table.setItem(row, column, QTableWidgetItem(text))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        note = QLabel("MusicDesk is free software, under the GNU General Public License, version 3 or later. "
                      "It comes with no warranty. The licence texts of MusicDesk and of the components above are in "
                      "the app's licences folder.")
        note.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.licenses_button = QPushButton("Open licences folder")
        buttons.addButton(self.licenses_button, QDialogButtonBox.ButtonRole.ActionRole)
        self.licenses_button.setToolTip("Show the licence texts of MusicDesk and the components it bundles, in File "
                                        "Explorer")
        self.licenses_button.clicked.connect(self.licensesRequested)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(self.versions)
        layout.addWidget(self.table, 1)
        layout.addWidget(note)
        layout.addWidget(buttons)
        self.resize(560, 420)

    def rows(self) -> list[tuple[str, ...]]:
        """The components table as shown (tests)."""
        return [tuple(cell_text(self.table, r, c) for c in range(len(HEADERS)))
                for r in range(self.table.rowCount())]
