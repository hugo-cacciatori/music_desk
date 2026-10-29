import base64
import logging
import os
import time
from pathlib import Path
from typing import cast

from PySide6.QtCore import QByteArray, QEvent, QObject, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDropEvent,
    QKeySequence,
    QMouseEvent,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from refrain.context_menu.request import EDIT_CUE, NEW_CUE, SPLIT, TRIM
from refrain.core import playlists
from refrain.core.index import queries
from refrain.core.index.scanner import ScanReport, find_folder
from refrain.core.index.store import path_of
from refrain.core.path_locks import PathLocks
from refrain.licenses import licenses_dir
from refrain.services.album_search_service import AlbumSearchService
from refrain.services.library import LibraryService
from refrain.services.tag_io import TagIO
from refrain.services.tag_writer import TagWriter
from refrain.settings import Settings
from refrain.ui import theme
from refrain.ui.about_dialog import AboutDialog
from refrain.ui.advanced_search import AdvancedSearchWindow
from refrain.ui.controllers import STATUS_MS
from refrain.ui.controllers.delete import DeleteController
from refrain.ui.controllers.menus import MenuController
from refrain.ui.controllers.playlist import PlaylistController
from refrain.ui.controllers.rename import RenameController
from refrain.ui.controllers.roots import RootsController
from refrain.ui.controllers.search import SearchController
from refrain.ui.controllers.tools import ToolsController
from refrain.ui.file_view import FileView
from refrain.ui.main_toolbars import Toolbars
from refrain.ui.no_wheel import install_no_wheel
from refrain.ui.qt_util import app
from refrain.ui.shell import Shell
from refrain.ui.side_panel.panel import SelectedItem, SidePanel
from refrain.ui.sidebar import Sidebar
from refrain.ui.status_line import StatusLine
from refrain.ui.toast import Toast
from refrain.ui.tools.tool_cards import ToolCards

log = logging.getLogger(__name__)

APP_TITLE = "Refrain"
WAIT_FOR_INDEX_S = 120  # a folder from outside the app opens once indexed, within this (SPEC §29)



class DropOverlay(QLabel):
    """Covers the window while folders are dragged over it."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__("Add to library", parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setObjectName("dropOverlay")  # its colours: theme.style_sheet
        font = self.font()
        font.setPixelSize(theme.OVERLAY_TEXT_PX)
        self.setFont(font)
        theme.strong(self)
        self.hide()

    def cover(self) -> None:
        parent = self.parentWidget()
        assert parent is not None  # an overlay always covers its parent
        self.setGeometry(parent.rect())
        self.show()
        self.raise_()


class MainWindow(QMainWindow):
    closed = Signal()  # closed for good: the process goes on while a task's window is open (SPEC §35)
    entryRequested = Signal(str, list)  # a right-click entry and its paths: app.Starter opens its window (SPEC §35)

    def __init__(self, library: LibraryService | None = None, settings: Settings | None = None,
                 cards: ToolCards | None = None) -> None:
        """`cards`: the Options window's cards, when the right-click Settings entry made them first (SPEC §31)."""
        super().__init__()
        self.library = library
        self.settings = settings or Settings()
        self.folder_id: int | None = None
        self.folder_path: str | None = None  # to find the nearest surviving folder if this one disappears
        self.history_back: list[int] = []
        self.history_forward: list[int] = []
        self.rsgain_version: str | None = None  # from the startup self-check (About)
        self.flac_version: str | None = None  # likewise; the cue split needs it (SPEC §22)
        self._waiting_folder: tuple[str, float] | None = None  # (path, deadline): opens once indexed (SPEC §29)
        self.shell = Shell("shell", self)  # handing files to Windows, off the UI thread
        self.setWindowTitle(APP_TITLE)
        self.resize(1200, 760)
        self.status_line = StatusLine(self, self)  # indexing and lyrics progress

        conn = lambda: self.open_library.conn if self._library_open() else None  # noqa: E731
        self.sidebar = Sidebar(conn, self.settings.expanded_folders)
        self.sidebar.model.only_music = self.settings.hide_folders_without_music
        self.file_view = FileView(conn)
        self.tag_io = TagIO(self)
        self.tag_io.name_masks = self._name_masks
        self.tag_writer = TagWriter(
            library.locks if library is not None else PathLocks(),
            on_written=lambda path: self.open_library.indexer.update_file(path) if self._library_open() else None,
            parent=self)
        self.tag_writer.name_masks = self._name_masks
        self.side_panel = SidePanel(self.tag_io, self.tag_writer, conn,
                                    lyrics_service=library.lyrics if library is not None else None)
        self.side_panel.lyrics.urlRequested.connect(self.open_url)
        self.side_panel.lyrics.set_text_size(self.settings.lyrics_text_size)  # as last set (SPEC §8)
        self.side_panel.lyrics.textSizeChanged.connect(lambda points: setattr(self.settings, "lyrics_text_size",
                                                                              points))
        self.side_panel.summary.playRequested.connect(self.play_folder)
        # The cue preview's Split and Edit…, the Metadata tab's Trim… and New cue sheet…: the right-click windows
        # (SPEC §35), through app.Starter.
        self.side_panel.preview.splitRequested.connect(lambda path: self.entryRequested.emit(SPLIT, [path]))
        self.side_panel.preview.editRequested.connect(lambda path: self.entryRequested.emit(EDIT_CUE, [path]))
        self.side_panel.metadata.trimRequested.connect(lambda path: self.entryRequested.emit(TRIM, [str(path)]))
        self.side_panel.metadata.newCueRequested.connect(
            lambda path: self.entryRequested.emit(NEW_CUE, [str(path)]))
        self.side_panel.preview.openRequested.connect(lambda path: self.shell.run(
            lambda: self.shell.launch(path), f"Windows couldn't open {os.path.basename(path)}: no app is set for this "
            "file type, or the file isn't reachable.", path, False))
        # Each feature's flow lives in its controller (SPEC §16); every task has its own window (SPEC §35).
        self.tools = ToolsController(self, self, cards)  # the toolbar's tools, as their entries (SPEC §30, §35)
        self.tools.cards.hideFoldersChanged.connect(self.set_hide_folders_without_music)  # the Browser page
        self.delete = DeleteController(self, self)  # Delete to the Recycle Bin (SPEC §19)
        self.renamer = RenameController(self, self)  # inline rename (SPEC §11)
        self.roots = RootsController(self, self)  # the library's folders (SPEC §3)
        self.menus = MenuController(self, self)  # the file list's and the tree's menus (SPEC §5)
        # Library search (SPEC §6): the query is typed in the toolbar, the results replace the file list.
        self.search = SearchController(self, library, self)
        self.search_view, self.search_field = self.search.view, self.search.field
        self.center = QStackedWidget()  # the file list, the search results, or a tool's page
        self.center.addWidget(self.file_view)
        self.center.addWidget(self.search_view)
        self.playlist = PlaylistController(self, conn, self)  # the playlist builder (SPEC §15)
        self.playlist_panel, self.playlist_action = self.playlist.panel, self.playlist.action
        self.splitter = QSplitter()
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(self.center)
        self.splitter.addWidget(self.playlist_panel)
        self.splitter.addWidget(self.side_panel)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setCollapsible(1, False)
        self.splitter.setCollapsible(2, False)
        self.playlist_panel.hide()
        self.splitter.setSizes([240, 560, 0, 440])  # a first start: a lyrics pane wide enough to read (SPEC §8)
        self._selected_ids: list[int] = []
        self._restoring_selection = False
        # The panels as cards on the window's background (SPEC §36 task 3), 8 px apart.
        self.splitter.setHandleWidth(theme.SPACE_S)
        for panel in (self.sidebar, self.center, self.playlist_panel, self.side_panel):
            theme.card(panel)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(theme.SPACE_S, 0, theme.SPACE_S, theme.SPACE_S)
        body_layout.addWidget(self.splitter)
        self.setObjectName("mainWindow")  # the window's background: theme.style_sheet
        self.setCentralWidget(body)
        self.advanced_search: AdvancedSearchWindow | None = None  # made on first use (SPEC §26)
        self.toolbars = Toolbars(self, conn, self)  # the search, navigation and tools rows (SPEC §30, §33)
        self.address_bar = self.toolbars.address_bar
        self._update_navigation()
        self._restore_state()
        self.playlist.restore()
        self._apply_tree()  # hidden by default; refresh_roots applies it again once the folders are known

        self.add_folder_action = QAction("&Add folder…", self, shortcut=QKeySequence("Ctrl+O"))
        self.add_folder_action.triggered.connect(self.roots.add_folder)
        self.addAction(self.add_folder_action)  # Ctrl+O; the sidebar's Add folder… is the visible way (0.8.4)
        self.overlay = DropOverlay(self)
        self.toast = Toast(self)
        self.setAcceptDrops(library is not None)
        self.sidebar.addFolderRequested.connect(self.roots.add_folder)
        self.sidebar.removeRequested.connect(self.roots.remove_root)
        self.sidebar.folderSelected.connect(self.open_folder)
        self.file_view.folderActivated.connect(self.open_folder)
        self.file_view.fileActivated.connect(lambda entry_id: self.open_files([entry_id]))
        self.file_view.menuRequested.connect(lambda ids, pos: self.menus.list_menu(ids).exec(pos))
        self.sidebar.folder_menu_provider = self.menus.tree_menu
        self.file_view.renameRequested.connect(self.renamer.rename_entry)
        self.sidebar.renameRequested.connect(self.renamer.rename_entry)
        # Each page's selection counts only while it is on screen.
        self.file_view.selectionChanged.connect(
            lambda ids: None if self.showing_search else self._selection_changed(ids))
        self.search_view.selectionChanged.connect(
            lambda ids: self._selection_changed(ids) if self.showing_search else None)
        self.side_panel.metadata.fileSaved.connect(self._file_saved)
        self.side_panel.metadata.renameSuggested.connect(self.renamer.rename_path)
        self.side_panel.metadata.filesSaved.connect(
            lambda count: self.statusBar().showMessage(f"Saved the tags of {count} files.", STATUS_MS))
        self.side_panel.metadata.saveFailed.connect(lambda message: self.toast.show_message(message))
        self.shell.failed.connect(self._shell_failed)
        app().installEventFilter(self)  # the mouse back/forward buttons, anywhere in the window
        install_no_wheel(app())  # Ground rule 7, in every window and dialog

        if library is None:
            self.add_folder_action.setEnabled(False)
            self.sidebar.setEnabled(False)
        else:
            library.rootsChanged.connect(self.refresh_roots)
            library.addFinished.connect(self.roots.show_add_results)
            library.failed.connect(lambda message: self.statusBar().showMessage(message, STATUS_MS))
            library.notice.connect(lambda message: self.statusBar().showMessage(message, STATUS_MS))
            library.indexingPaused.connect(self.status_line.set_indexing_paused)
            library.indexer.progress.connect(self.status_line.show_index_progress)
            library.indexer.idle.connect(self.status_line.index_label.hide)
            library.indexer.failed.connect(lambda message: self.statusBar().showMessage(message, STATUS_MS))
            library.indexer.folderUpdated.connect(self.folder_updated)
            library.indexer.scanFinished.connect(self.scan_finished)
            # A folder asked for from File Explorer (SPEC §29) opens as soon as the index has it.
            for signal in (library.indexer.progress, library.indexer.folderUpdated, library.indexer.scanFinished,
                           library.rootsChanged):
                signal.connect(self._open_waiting_folder)
            library.lyrics.foldersChanged.connect(self.lyrics_updated)
            library.lyrics.unreachable.connect(self.status_line.lrclib_unreachable)
            library.lyrics.progress.connect(self.status_line.show_lyrics_progress)
            library.lyrics.entryReady.connect(self.status_line.lyrics_entry)
            self.file_view.model.lrclib_status = library.lyrics.status_for
            self.refresh_roots()
            self._reopen_last_folder()
        self._shown_once = False

    # -- rsgain --

    def show_rsgain_version(self, version: str | None) -> None:
        """The startup self-check of the bundled rsgain (SPEC §13). A failure is explained on the ReplayGain card
        and by the toolbar's Start; the version only shows in About, so the status bar has no rsgain label."""
        self.rsgain_version = version  # for the About dialog
        self.tools.cards.set_rsgain_unavailable(
            None if version else "rsgain couldn't run, so ReplayGain is unavailable. See the log for details.")

    def show_flac_version(self, version: str | None) -> None:
        """The startup self-check of the bundled flac (SPEC §13): the Metadata tab's Trim… is disabled with the
        reason when it failed (a Split or Trim window says so itself when it reads its file)."""
        self.flac_version = version
        reason = None if version else "flac couldn't run, so trimming is unavailable. See the log for details."
        self.side_panel.metadata.set_trim_unavailable(reason)

    def open_about(self) -> None:
        """Help › About: versions and licences."""
        dialog = AboutDialog(self.rsgain_version, self, flac_version=self.flac_version)
        folder = str(licenses_dir())
        dialog.licensesRequested.connect(lambda: self.shell.run(
            lambda: self.shell.show_in_explorer(folder, True), f"File Explorer couldn't open {folder}.", "", True))
        self.show_about(dialog)

    def show_about(self, dialog: AboutDialog) -> None:
        """Shows the dialog modally (replaced in tests, which never open a real dialog)."""
        dialog.exec()

    # -- browsing --

    def open_advanced_search(self) -> None:
        """The toolbar's Advanced search: its window, not modal (SPEC §26). One at a time; brought to the front."""
        if not self._library_open():
            return
        if self.advanced_search is None:
            self.advanced_search = AdvancedSearchWindow(self, AlbumSearchService(self.open_library.db_path, self))
        self.advanced_search.show()
        self.advanced_search.raise_()
        self.advanced_search.activateWindow()

    # -- a folder from outside the app (SPEC §29) --

    def bring_to_front(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def open_from_outside(self, path: str | None) -> None:
        """A folder from File Explorer's `refrain` keyword, the command line or a later start: the window comes to
        the front and shows it, adding it to the library first when it isn't in it. None: only to the front."""
        self.bring_to_front()
        if not path or not self._library_open():
            return
        path = os.path.normpath(os.path.abspath(path))
        if not os.path.isdir(path):
            self._waiting_folder = None
            self.statusBar().showMessage(f"{path} isn't a folder that can be reached.", STATUS_MS)
            return
        self._waiting_folder = (path, time.monotonic() + WAIT_FOR_INDEX_S)
        found = find_folder(self.open_library.conn, path)
        if found is not None and found[1]:
            self._open_waiting_folder()
        elif found is None:
            self.open_library.add_folders([path])  # the status bar says it was added; it opens once indexed
        else:
            self.open_library.indexer.request_path(path)  # inside a root, not indexed yet

    def _open_waiting_folder(self, *_args) -> None:
        if self._waiting_folder is None or not self._library_open():
            return
        path, deadline = self._waiting_folder
        if time.monotonic() > deadline:
            self._waiting_folder = None
            return
        found = find_folder(self.open_library.conn, path)
        if found is not None and found[1]:
            self._waiting_folder = None
            self.show_folder(found[0], path)

    def show_folder(self, folder_id: int, shown: str = "") -> None:
        """A folder from another window (an Advanced search result): the file list on it, in front."""
        if not self._library_open() or not self._folder_exists(folder_id):
            self.statusBar().showMessage(f"{shown or 'That folder'} is no longer in the library.", STATUS_MS)
            return
        if not self.side_panel.can_leave():
            return
        if self.showing_search:
            self.search.leave()
        self.center.setCurrentWidget(self.file_view)
        if folder_id != self.folder_id:
            self.open_folder(folder_id)
        self.raise_()
        self.activateWindow()
        self.file_view.view.setFocus()

    def open_folder(self, entry_id: int, *, record: bool = True) -> None:
        """Show a folder at once from the index, then have it revalidated (stale-while-revalidate).
        `record`: a new step in the history (not for back, forward, or a replacement of a vanished folder)."""
        if not self._library_open() or entry_id == self.folder_id or not self._folder_exists(entry_id):
            return  # a row being removed can still be selected for a moment
        if not self.side_panel.can_leave():  # unsaved tag edits: Save, Discard or Cancel
            if self.folder_id is not None:
                self.sidebar.tree.select_entry(queries.folder_path_ids(self.open_library.conn, self.folder_id))
            return
        self.close_search()  # browsing elsewhere goes back to the file list
        came_from = self.folder_id
        if record and self.folder_id is not None:
            self.history_back.append(self.folder_id)
            self.history_forward.clear()
        self.folder_id = entry_id
        self.folder_path = str(path_of(self.open_library.conn, entry_id))
        self.file_view.set_folder(entry_id)
        crumbs = queries.folder_crumbs(self.open_library.conn, entry_id)
        self.address_bar.set_location(crumbs, self.folder_path)
        self.sidebar.tree.select_entry([entry for entry, _name in crumbs])
        self._show_selection([])
        if came_from is not None:
            self._select_came_from(came_from, entry_id)
        self.open_library.folder_opened(entry_id)
        self.open_library.lyrics.prioritize_folder(entry_id)
        self._update_navigation()

    def _select_came_from(self, came_from: int, opened: int) -> None:
        """Going up by any way (Up, Back, the address bar, the tree) selects the folder you came from, as Explorer
        does; from deeper down, the folder on the way back to it (SPEC §5)."""
        path = queries.folder_path_ids(self.open_library.conn, came_from)
        if opened in path[:-1]:
            self.file_view.select_ids([path[path.index(opened) + 1]], reveal=True)

    # -- history and navigation --

    def _update_navigation(self) -> None:
        open_ = self._library_open() and self.folder_id is not None
        self.toolbars.back_action.setEnabled(bool(self.history_back))
        self.toolbars.forward_action.setEnabled(bool(self.history_forward))
        self.toolbars.up_action.setEnabled(open_ and self.folder_id is not None
                                  and queries.parent_folder(self.open_library.conn, self.folder_id) is not None)
        self.toolbars.refresh_action.setEnabled(open_)
        self.toolbars.explorer_action.setEnabled(open_)

    def go_back(self) -> None:
        self._step(self.history_back, self.history_forward)

    def go_forward(self) -> None:
        self._step(self.history_forward, self.history_back)

    def _step(self, source: list[int], target: list[int]) -> None:
        """Move through the history, skipping folders that left the index."""
        if not self._library_open():
            return
        while source:
            entry_id = source.pop()
            if self._folder_exists(entry_id) and entry_id != self.folder_id:
                if self.folder_id is not None:
                    target.append(self.folder_id)
                self.open_folder(entry_id, record=False)
                return
        self._update_navigation()

    def go_up(self) -> None:
        if not self._library_open() or self.folder_id is None:
            return
        parent = queries.parent_folder(self.open_library.conn, self.folder_id)
        if parent is not None:
            self.open_folder(parent)  # which selects the folder you came from

    def refresh_after_tool(self, folder: Path) -> None:
        """A tool changed files under `folder`: the index catches up on the whole subtree first, even during a
        long background scan, and the list follows as each folder is served."""
        if self._library_open():
            self.open_library.indexer.request_path(str(folder), recursive=True)

    def rescan_folder(self) -> None:
        """F5: reconcile the open folder with the disk now."""
        if self._library_open() and self.folder_id is not None:
            self.open_library.indexer.request_folder(self.folder_id)  # first, even during a long background scan
            self.statusBar().showMessage("Rescanning this folder…", 2000)

    def go_to_path(self, text: str) -> None:
        """A typed or pasted path: open it if it is an indexed folder, or open a file's folder and select it."""
        if not self._library_open():
            return
        found = find_folder(self.open_library.conn, text)
        if found is None:
            self.statusBar().showMessage(
                f"{text} isn't in the library. Add its folder first (Add folder… in the sidebar).", STATUS_MS)
            return
        folder, exact = found
        if exact:
            self.open_folder(folder)
            return
        rest = Path(os.path.normpath(os.path.abspath(text))).relative_to(path_of(self.open_library.conn, folder)).parts
        file_id = queries.find_child(self.open_library.conn, folder, rest[0]) if len(rest) == 1 else None
        if file_id is not None:
            self.open_folder(folder)
            self.file_view.select_ids([file_id])
        else:
            self.statusBar().showMessage(f"{text} wasn't found in the library's index.", STATUS_MS)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress and isinstance(watched, QWidget) and \
                watched.window() is self:
            button = cast(QMouseEvent, event).button()
            if button == Qt.MouseButton.BackButton:
                self.go_back()
                return True
            if button == Qt.MouseButton.ForwardButton:
                self.go_forward()
                return True
        return False

    # -- selection and the side panel --

    def _selection_changed(self, ids: list[int]) -> None:
        if self._restoring_selection or ids == self._selected_ids:
            return
        if not self.side_panel.can_leave():
            self._restoring_selection = True  # Cancel: back to the file being edited
            (self.search_view if self.showing_search else self.file_view).select_ids(self._selected_ids)
            self._restoring_selection = False
            return
        self._show_selection(ids)

    def _selected_item(self, entry_id: int) -> SelectedItem | None:
        """A row of the page on screen: the file list (the open folder), or the search results (any folder)."""
        if self.showing_search:
            r = self.search_view.row(entry_id)
            return SelectedItem(Path(r.folder, r.name), r.is_dir, r.is_audio, entry_id) if r else None
        row = self.file_view.model.row_for(entry_id)
        if row is None or not self.folder_path:
            return None
        r = self.file_view.model.rows[row]
        return SelectedItem(Path(self.folder_path, r.name), r.is_dir, r.is_audio, entry_id)

    def _show_selection(self, ids: list[int]) -> None:
        self._selected_ids = ids
        items, folder_ids, audio_ids = [], [], []
        for entry_id in ids:
            item = self._selected_item(entry_id)
            if item is not None:
                items.append(item)
                if item.is_dir:
                    folder_ids.append(entry_id)
                elif item.is_audio:
                    audio_ids.append(entry_id)
        if len(items) == 1 and audio_ids:
            self.open_library.lyrics.prioritize_track(audio_ids[0])  # the selected track's lyrics first (SPEC §9)
        if len(items) == 1 and folder_ids:
            self.side_panel.show_folder(folder_ids[0])  # one folder selected: its summary
        elif not items and self.folder_id is not None and not self.showing_search:
            self.side_panel.show_folder(self.folder_id)  # nothing selected: the open folder's summary
        else:
            self.side_panel.show_selection(items)

    def _file_saved(self, path: str) -> None:
        """The index already caught up through TagWriter.on_written; the list row follows on folderUpdated."""
        self.statusBar().showMessage(f"Saved the tags of {os.path.basename(path)}.", STATUS_MS)

    def folder_updated(self, entry_id: int) -> None:
        if not self._library_open():
            return
        self.playlist.refresh_later()
        self._keep_open_folder()
        self.sidebar.model.refresh(entry_id)
        if entry_id == self.folder_id:
            self.file_view.refresh()
            self._show_selection(self._selected_ids)  # a renamed file reloads under its new name
        self._refresh_location()
        self.side_panel.refresh_folder()
        self.search.refresh_later()

    # -- the centre: the file list or the search results (SPEC §6); every task has its own window (SPEC §35) --

    @property
    def showing_search(self) -> bool:
        return self.search.showing

    def close_search(self) -> None:
        """Back to the file list (browsing elsewhere leaves the search results)."""
        if self.showing_search:
            self.search.leave()
            self.center.setCurrentWidget(self.file_view)
            self.file_view.view.setFocus()

    def _unsaved_edits_under(self, folder: Path) -> bool:
        """Unsaved Metadata edits on a file a job is about to change: ask Save, Discard or Cancel first."""
        metadata = self.side_panel.metadata
        edited = [p for p in ([metadata.path] if metadata.path else []) + list(metadata.paths)
                  if Path(p).is_relative_to(folder)]
        return bool(edited) and not self.side_panel.can_leave()

    def allow_changes(self, paths: list[str]) -> bool:
        """A task's window is about to change files at these paths (files, or folders and all below): unsaved
        Metadata edits on one of them ask Save, Discard or Cancel first, as the tools always did. False on Cancel."""
        return not any(self._unsaved_edits_under(Path(path)) for path in paths)

    def lyrics_updated(self, folder_ids: list[int]) -> None:
        """LRCLIB results: the open folder's LRCLIB column follows, and the search results (their text too)."""
        if not self._library_open():
            return
        self.search.refresh_later()
        if self.folder_id not in folder_ids:
            return
        self.file_view.refresh()
        self.side_panel.refresh_folder()

    def _refresh_location(self) -> None:
        """The open folder (or a folder above it) may have been renamed: its path and breadcrumb follow."""
        if self.folder_id is None or not self._folder_exists(self.folder_id):
            return
        try:
            path = str(path_of(self.open_library.conn, self.folder_id))
        except KeyError:  # a folder above it went between the two reads (its root removed): the removal closes it
            return
        if path != self.folder_path:
            self.folder_path = path
            self.address_bar.set_location(queries.folder_crumbs(self.open_library.conn, self.folder_id), path)

    def scan_finished(self, _report: ScanReport) -> None:
        if not self._library_open():
            return
        self.playlist.refresh_later()
        self._keep_open_folder()
        self.sidebar.model.refresh_loaded()
        if self.folder_id is not None:
            self.file_view.refresh()
        self.side_panel.refresh_folder()
        self.search.refresh_later()

    def _keep_open_folder(self) -> None:
        """If the open folder left the index (deleted or moved outside the app), open its nearest surviving
        ancestor, as Explorer does. Done before the tree drops the row, which would select a neighbour."""
        if self.folder_id is None or self._folder_exists(self.folder_id):
            return
        found = find_folder(self.open_library.conn, self.folder_path) if self.folder_path else None
        if found is None:
            self._close_folder()
        else:
            self.open_folder(found[0], record=False)

    def _name_masks(self) -> tuple[tuple[str, ...], str]:
        """The shared file name masks and replacement (SPEC §21), for the rename hint."""
        return tuple(self.settings.name_masks), self.settings.name_replacement

    def _library_open(self) -> bool:
        """False without a library, and once it is closed: queued signals can arrive after the shutdown."""
        return self.library is not None and not self.library.closed

    @property
    def open_library(self) -> LibraryService:
        """The library, for code that runs only while one is open (after `_library_open()`, or from its signals)."""
        assert self.library is not None
        return self.library

    def _folder_exists(self, entry_id: int) -> bool:
        return self.open_library.conn.execute("SELECT 1 FROM entries WHERE id = ?", (entry_id,)).fetchone() is not None

    def _close_folder(self) -> None:
        self.folder_id = None
        self.folder_path = None
        self.file_view.set_folder(None)
        self.address_bar.set_location([], "")
        self._update_navigation()

    def set_hide_folders_without_music(self, hide: bool) -> None:
        self.settings.hide_folders_without_music = hide
        self.sidebar.model.set_only_music(hide)

    # -- window state --

    def _restore_state(self) -> None:
        for value, restore in ((self.settings.window_geometry, self.restoreGeometry),
                               (self.settings.splitter_state, self.splitter.restoreState)):
            if value:
                try:
                    restore(QByteArray(base64.b64decode(value, validate=True)))
                except ValueError:
                    pass
        self.file_view.restore_layout(self.settings.file_list_header)

    def _reopen_last_folder(self) -> None:
        if not self.settings.last_folder:
            return
        found = find_folder(self.open_library.conn, self.settings.last_folder)
        if found and found[1]:
            self.open_folder(found[0])

    def save_state(self) -> None:
        self.playlist.save_state()
        if not self.sidebar.isHidden() and self.splitter.sizes()[0] > 0:
            self.settings.tree_width = self.splitter.sizes()[0]
        self.settings.window_geometry = base64.b64encode(self.saveGeometry().data()).decode("ascii")
        self.settings.splitter_state = base64.b64encode(self.splitter.saveState().data()).decode("ascii")
        self.settings.file_list_header = self.file_view.save_layout()
        self.settings.expanded_folders = self.sidebar.tree.expanded_paths()
        self.settings.last_folder = ""
        if self._library_open() and self.folder_id is not None and self._folder_exists(self.folder_id):
            self.settings.last_folder = str(path_of(self.open_library.conn, self.folder_id))
        self.settings.save()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._shown_once:
            self._shown_once = True
            self.search_field.setFocus()  # the app opens on its search (SPEC §33), ready to type into

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.side_panel.can_leave():
            event.ignore()
            return
        self.tag_writer.close()  # the queued saves finish first
        self.tag_io.close()
        self.playlist.shutdown()
        self.search.shutdown()
        if self.advanced_search is not None:
            self.advanced_search.close()
            self.advanced_search.service.close()
        self.save_state()
        app().removeEventFilter(self)
        self.shell.shutdown()
        super().closeEvent(event)
        self.closed.emit()

    # -- the folder tree (SPEC §33) --

    def set_tree_visible(self, visible: bool) -> None:
        """The Folders button: the user's choice, kept. The tree still shows while the library is empty, where its
        Add folder… button is."""
        self.settings.tree_visible = visible
        self._apply_tree()

    def _apply_tree(self) -> None:
        shown = self.settings.tree_visible or self.sidebar.empty
        empty = self.sidebar.empty
        self.toolbars.folders_action.setEnabled(not empty)
        self.toolbars.folders_action.setToolTip("The folder tree shows until a folder is added" if empty
                                       else "Show or hide the folder tree (Ctrl+Shift+F)")
        if self.toolbars.folders_action.isChecked() != shown:
            self.toolbars.folders_action.blockSignals(True)
            self.toolbars.folders_action.setChecked(shown)
            self.toolbars.folders_action.blockSignals(False)
        if shown == (not self.sidebar.isHidden()):
            return
        # As the playlist panel: the tree takes its width from the centre only, and gives it back when hidden.
        sizes = self.splitter.sizes()
        if shown:
            width = min(max(self.settings.tree_width, 150), max(0, sizes[1] - 200))
            sizes[1] -= width
            sizes[0] = width
            self.sidebar.show()
        else:
            if sizes[0] > 0:
                self.settings.tree_width = sizes[0]
            sizes[1] += sizes[0]
            sizes[0] = 0
            self.sidebar.hide()
        self.splitter.setSizes(sizes)

    # -- a right-click entry's run (SPEC §35) --

    def apply_renames(self, steps) -> None:
        """A Rename window's renames (old, new paths): the index applies them in place, one folder at a time, so ids,
        tags and the selection stay (SPEC §10 A); `entry_finished` then re-reads the folders for anything else."""
        by_folder: dict[Path, list] = {}
        for step in steps:
            by_folder.setdefault(Path(step[0]).parent, []).append(step)
        for folder_steps in by_folder.values():
            self.open_library.indexer.apply_renames(folder_steps)

    def entry_finished(self, folders: list[Path]) -> None:
        """A right-click entry's run changed files under these folders: the index re-reads them, and the Metadata
        tab reloads the file it shows if it is there (without unsaved edits)."""
        for folder in folders:
            self.refresh_after_tool(folder)
        self.side_panel.reload_under(folders)

    # -- actions handed to Windows --

    def _paths(self, ids: list[int]) -> list[str]:
        return [str(path_of(self.open_library.conn, entry_id)) for entry_id in ids if self._folder_exists(entry_id)]

    def open_files(self, ids: list[int]) -> None:
        """Open with the default app, as a double-click in Explorer does."""
        if not self._library_open():
            return
        for path in self._paths(ids):
            self.shell.run(lambda p=path: self.shell.launch(p),
                           f"Windows couldn't open {os.path.basename(path)}: no app is set for this file type, "
                           "or the file isn't reachable.", path, False)

    def open_url(self, url: str) -> None:
        """A web search from the Lyrics tab, in the default browser."""
        self.shell.run(lambda: self.shell.launch(url), "Windows couldn't open the web browser.", "", False)

    def reveal(self, entry_id: int) -> None:
        if not self._library_open() or not self._folder_exists(entry_id):
            return
        conn = self.open_library.conn
        path = str(path_of(conn, entry_id))
        is_dir = bool(conn.execute("SELECT is_dir FROM entries WHERE id = ?", (entry_id,)).fetchone()[0])
        self.shell.run(lambda: self.shell.show_in_explorer(path, is_dir), f"File Explorer couldn't show {path}.",
                       "", True)

    def copy_paths(self, ids: list[int]) -> None:
        paths = self._paths(ids) if self._library_open() else []
        if paths:
            QApplication.clipboard().setText("\n".join(paths))
            self.statusBar().showMessage("Path copied." if len(paths) == 1 else f"{len(paths)} paths copied.",
                                         STATUS_MS)

    def play_folder(self, entry_id: int, recursive: bool) -> None:
        if not self._library_open() or not self._folder_exists(entry_id):
            return
        entries = queries.playlist_for_folder(self.open_library.conn, entry_id, recursive)
        self.play(entries, str(path_of(self.open_library.conn, entry_id)), True)

    def play_selection(self, ids: list[int]) -> None:
        if not self._library_open():
            return
        entries = queries.playlist_for_ids(self.open_library.conn, ids)
        self.play(entries, str(entries[0].path) if entries else "", False)

    def play(self, entries: list[playlists.PlaylistEntry], fallback: str, fallback_is_dir: bool) -> None:
        """Write a temporary playlist (the app's own file) and hand it to the default player (SPEC §14)."""
        if not entries:
            self.statusBar().showMessage("There are no audio files to play here.", STATUS_MS)
            return

        def run() -> None:
            playlist = playlists.write_temp_playlist(entries, self.shell.playlist_dir)
            self.shell.launch(playlist)

        self.shell.run(run, "No app is set to play .m3u8 playlists. To choose one, open Windows Settings > Apps > "
                       "Default apps and search for .m3u8.", fallback, fallback_is_dir)
        self.statusBar().showMessage(f"Playing {len(entries)} track{'s' if len(entries) > 1 else ''}…", STATUS_MS)

    def _shell_failed(self, message: str, fallback: str, fallback_is_dir: bool) -> None:
        if fallback:
            self.toast.show_message(message, "Open in File Explorer", lambda: self.shell.submit(
                lambda: self.shell.show_in_explorer(fallback, fallback_is_dir)))
        else:
            self.toast.show_message(message)

    # -- roots --

    def refresh_roots(self) -> None:
        if not self._library_open():
            return
        self._keep_open_folder()  # its root may have been removed, or merged into a new one
        self.sidebar.set_roots(self.open_library.roots())
        self._apply_tree()

    # -- drag and drop (folders in; dragging out is disabled elsewhere) --

    @staticmethod
    def _local_paths(event) -> list[str]:
        mime = event.mimeData()
        if not mime.hasUrls():
            return []
        return [os.path.normpath(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()]

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self.library is not None and self._local_paths(event):
            event.acceptProposedAction()
            self.overlay.cover()
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self.overlay.hide()

    def dropEvent(self, event: QDropEvent) -> None:
        self.overlay.hide()
        paths = self._local_paths(event)
        if self.library is None or not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        self.open_library.add_folders(paths)  # files are sorted out off the UI thread, and come back as a toast
