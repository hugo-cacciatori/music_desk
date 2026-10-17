"""The library search's controller (SPEC §6, §16): the query field, the results page and the search service.

The query is typed in the toolbar's field and runs as you type (debounced); the results stand in for the file
list until Esc, or until a result is opened. When the index changes while the page is shown, the same query runs
again and the rows are updated in place, so the selection stays.
"""

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QLineEdit, QMenu

from music_desk.core.index.search import LIMIT
from music_desk.services.library import LibraryService
from music_desk.services.search_service import SearchResults, SearchService
from music_desk.ui.controllers import STATUS_MS
from music_desk.ui.qt_util import shortcut
from music_desk.ui.search_view import SearchView
from music_desk.ui.search_view import sort_keys as search_sort_keys

TYPING_MS = 120  # runs as you type, once typing pauses
REFRESH_MS = 1000  # the index changed: the query runs again


class SearchController(QObject):
    def __init__(self, window, library: LibraryService | None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.window = window
        self.view = SearchView()
        self.view.resultActivated.connect(self.go_to_result)
        self.view.menuRequested.connect(lambda ids, pos: self.result_menu(ids).exec(pos))
        self.view.playRequested.connect(window.play_selection)
        self.view.artistRequested.connect(self.add_artist_filter)
        self.view.closeRequested.connect(self.close)
        self.service: SearchService | None = None
        if library is not None:
            self.service = SearchService(library.db_path, self, prepare=search_sort_keys)
            self.service.results.connect(self._results)
            self.view.model.lrclib_status = library.lyrics.status_for
        self.limit = LIMIT  # lowered in tests
        self.shown: int | None = None  # the token of the results on screen (tests wait for it)
        self._token: int | None = None
        self._request: str | None = None  # the query running, and shown
        self._shown_request: str | None = None
        self._timer = QTimer(self, singleShot=True, interval=TYPING_MS)
        self._timer.timeout.connect(self.run)
        self._refresh = QTimer(self, singleShot=True, interval=REFRESH_MS)
        self._refresh.timeout.connect(self.run)

        self.field = QLineEdit()  # the window puts it in its toolbar
        self.field.setPlaceholderText("Search library (Ctrl+K)")
        self.field.setClearButtonEnabled(True)
        self.field.setMinimumWidth(180)
        self.field.setMaximumWidth(320)
        self.field.textEdited.connect(self._edited)
        self.field.returnPressed.connect(lambda: self.view.focus_results())
        shortcut(QKeySequence(Qt.Key.Key_Escape), self.field, self.close, Qt.ShortcutContext.WidgetShortcut)

    @property
    def showing(self) -> bool:
        return self.window.center.currentWidget() is self.view

    def open(self) -> None:
        """Ctrl+K: the search page, with the query field focused. The results stand in for the file list."""
        window = self.window
        if not window._library_open():
            return
        if not self.showing:
            if not window.side_panel.can_leave():  # unsaved tag edits on the selection the search replaces
                return
            window.tools.cancel_planning()
            window.center.setCurrentWidget(self.view)
            window.side_panel.tabs.setCurrentWidget(window.side_panel.lyrics)  # the page's detail pane: the lyrics
            window._show_selection(self.view.selected_ids())
            self.run()
        self.field.setFocus()
        self.field.selectAll()

    def find(self, query: str) -> None:
        """A ready-made search over the whole library (the Action menu)."""
        if not self.window._library_open():
            return
        self.open()
        if not self.showing:
            return  # unsaved tag edits, and Cancel
        self.field.setText(query)
        self.run()

    def close(self) -> None:
        """Esc: back to the folder that was open, as it was."""
        window = self.window
        if not self.showing or not window.side_panel.can_leave():
            return
        self.leave()
        window.center.setCurrentWidget(window.file_view)
        window._show_selection(window.file_view.selected_ids())
        window.file_view.view.setFocus()

    def leave(self) -> None:
        """The page goes away: nothing runs for it any more."""
        self._timer.stop()
        self._refresh.stop()
        self._token = None
        self._shown_request = None  # the next search starts afresh
        if self.service is not None:
            self.service.cancel()

    def shutdown(self) -> None:
        if self.service is not None:
            self.service.close()

    def _edited(self, _text: str) -> None:
        if not self.showing:
            self.open()
        self._timer.start()

    def run(self) -> None:
        self._timer.stop()
        window = self.window
        if not window._library_open() or not self.showing or self.service is None:
            return
        self._request = self.field.text()
        self._token = self.service.search(
            self.field.text(), error_keys=window.open_library.lyrics.error_keys(), limit=self.limit)

    def refresh_later(self) -> None:
        """The index changed: the shown query runs again shortly."""
        if self.showing:
            self._refresh.start()

    def _results(self, results: SearchResults) -> None:
        if results.token != self._token or not self.showing:
            return
        self.shown = results.token
        in_place = self._request == self._shown_request  # run again after the index changed
        self._shown_request = self._request
        if results.query.empty and not results.error:
            self.view.show_prompt()
        else:
            self.view.show_results(results.rows, results.total, results.query.errors, results.error,
                                   keys=results.keys, in_place=in_place)

    def add_artist_filter(self, artist: str) -> None:
        """Ctrl+click on an artist, or "Filter by": the page's artist checkboxes, as artist: filters (ORed)."""
        term = 'artist:"{}"'.format(artist.replace('"', ""))
        text = self.field.text()
        if term.casefold() not in text.casefold():
            self.field.setText(f"{text.rstrip()} {term}".strip())
            self.run()

    def go_to_result(self, entry_id: int) -> None:
        """Enter or a double-click on a result: its folder, with the file selected; a folder result opens."""
        window = self.window
        row = self.view.row(entry_id)
        if row is None or not window._library_open():
            return
        folder_id, folder = (entry_id, str(Path(row.folder, row.name))) if row.is_dir else (row.folder_id, row.folder)
        if not window._folder_exists(folder_id):
            window.statusBar().showMessage(f"{folder} is no longer in the library.", STATUS_MS)
            return
        if not window.side_panel.can_leave():
            return
        self.leave()
        window.center.setCurrentWidget(window.file_view)
        if folder_id != window.folder_id:
            window.open_folder(folder_id)
        if not row.is_dir:
            window.file_view.select_ids([entry_id])
        window.file_view.view.setFocus()

    def result_menu(self, ids: list[int]) -> QMenu:
        window = self.window
        menu = QMenu(window)
        if not window._library_open() or not ids:
            return menu
        go = menu.addAction("Go to folder", lambda: self.go_to_result(ids[0]))
        go.setEnabled(len(ids) == 1)
        open_action = menu.addAction("Open", lambda: window.open_files(ids))
        open_action.setEnabled(len(ids) == 1)
        menu.addAction("Play selection", lambda: window.play_selection(ids))
        menu.addSeparator()
        menu.addAction("Open in File Explorer", lambda: window.reveal(ids[0]))
        menu.addAction("Copy path" if len(ids) == 1 else "Copy paths", lambda: window.copy_paths(ids))
        menu.addSeparator()
        menu.addAction("Remove ReplayGain tags…", lambda: window.remove_replaygain(ids))
        menu.addSeparator()
        menu.addAction("Delete\tDel", lambda: window.delete.delete_entries(ids, self.view.view))
        row = self.view.row(ids[0])
        artist = row.artist if len(ids) == 1 and row is not None else None
        if artist:
            menu.addSeparator()
            menu.addAction(f"Filter by {artist}", lambda: self.add_artist_filter(artist))
        return menu
