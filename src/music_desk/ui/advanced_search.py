"""The Advanced search window (SPEC §26): album lists to go through and fix by hand.

A separate window, not modal: the rest of the app keeps working beside it. One search is chosen from the options,
Start runs it on a worker, the results are the albums' folders, a double-click shows a folder in the main window,
and Refresh search runs the last search again so the list follows the fixes. The window only lists folders.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from music_desk.core.index.album_search import NOT_SPLIT, SEARCHES, WITHOUT_REPLAYGAIN
from music_desk.services.album_search_service import AlbumSearchService, SearchDone

FOLDER_ROLE = Qt.ItemDataRole.UserRole


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


class AdvancedSearchWindow(QWidget):
    def __init__(self, window, service: AlbumSearchService) -> None:
        # A top-level window of its own (Qt.Window), kept above the main window but never blocking it.
        super().__init__(window, Qt.WindowType.Window)
        self.main = window
        self.service = service
        self.last_search: str | None = None  # what Refresh search runs again
        self._token: int | None = None
        self.setWindowTitle("Advanced search")
        self.resize(640, 480)

        self.options = QButtonGroup(self)
        options = QVBoxLayout()
        tips = {WITHOUT_REPLAYGAIN: "List the album folders with a track that has no ReplayGain values",
                NOT_SPLIT: "List the folders holding a cue sheet and the one audio file it describes, with no "
                           "tracks split from it"}
        for n, (key, label) in enumerate(SEARCHES):
            button = QRadioButton(label)
            button.setProperty("search", key)
            button.setChecked(n == 0)
            button.setToolTip(tips[key])
            self.options.addButton(button)
            options.addWidget(button)
        self.start_button = QPushButton("Start")
        self.start_button.setToolTip("Search the library index for the chosen kind of album; nothing is changed")
        self.start_button.clicked.connect(self.start)
        self.refresh_button = QPushButton("Refresh search")
        self.refresh_button.setToolTip("Run the last search again, so the list follows what was fixed meanwhile.")
        self.refresh_button.clicked.connect(self.refresh)
        self.refresh_button.setEnabled(False)
        self.status = QLabel()
        self.status.setWordWrap(True)
        buttons = QHBoxLayout()
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.refresh_button)
        buttons.addWidget(self.status, 1)

        self.results = QListWidget()
        self.results.setToolTip("Double-click an album to show its folder in MusicDesk.")
        self.results.itemActivated.connect(self._activated)

        layout = QVBoxLayout(self)
        layout.addLayout(options)
        layout.addLayout(buttons)
        layout.addWidget(self.results, 1)
        service.done.connect(self._done)

    @property
    def chosen(self) -> str:
        button = self.options.checkedButton()
        return str(button.property("search")) if button is not None else SEARCHES[0][0]

    def start(self) -> None:
        self._run(self.chosen)

    def refresh(self) -> None:
        if self.last_search is not None:
            self._run(self.last_search)

    def _run(self, search: str) -> None:
        self._token = self.service.run(search)
        self.status.setText("Searching…")
        self.start_button.setEnabled(False)
        self.refresh_button.setEnabled(False)

    def _done(self, done: SearchDone) -> None:
        if done.token != self._token:
            return
        self.last_search = done.search
        self.start_button.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.results.clear()
        if done.error:
            self.status.setText(f"The search failed: {done.error}")
            return
        for result in done.results:
            item = QListWidgetItem(str(result.path))
            item.setData(FOLDER_ROLE, result.folder_id)
            self.results.addItem(item)
        label = dict(SEARCHES)[done.search].removeprefix("Find ")
        self.status.setText(f"{plural(len(done.results), 'album')} found ({label})." if done.results
                            else f"No album found ({label}).")

    def _activated(self, item: QListWidgetItem) -> None:
        self.main.show_folder(int(item.data(FOLDER_ROLE)), item.text())

    def paths(self) -> list[str]:
        """The listed folders, for tests."""
        return [self.results.item(i).text() for i in range(self.results.count())]
