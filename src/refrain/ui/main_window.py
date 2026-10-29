import base64
import logging
import os
import time
from pathlib import Path
from typing import cast

from PySide6.QtCore import QByteArray, QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDropEvent,
    QKeySequence,
    QMouseEvent,
    QPalette,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from refrain.context_menu.entries import OPEN, Selection, menu_entries
from refrain.context_menu.request import EDIT_CUE, NEW_CUE, SETTINGS, SPLIT, TRIM
from refrain.core import inline_rename, playlists
from refrain.core.index import queries
from refrain.core.index.roots import AddResult, AddStatus, Root
from refrain.core.index.scanner import ScanProgress, ScanReport, find_folder
from refrain.core.index.store import path_of
from refrain.core.path_locks import PathLocks
from refrain.core.playlist_builder import Builder
from refrain.licenses import licenses_dir
from refrain.services.album_search_service import AlbumSearchService
from refrain.services.library import LibraryService
from refrain.services.lyrics_service import OFFLINE, PAUSED, RUNNING, UNREACHABLE, LyricsProgress
from refrain.services.tag_io import TagIO
from refrain.services.tag_writer import TagWriter
from refrain.settings import Settings
from refrain.ui import theme
from refrain.ui.about_dialog import AboutDialog
from refrain.ui.address_bar import AddressBar
from refrain.ui.advanced_search import AdvancedSearchWindow
from refrain.ui.controllers import STATUS_MS
from refrain.ui.controllers.delete import DeleteController
from refrain.ui.controllers.search import SearchController
from refrain.ui.controllers.tools import ToolsController
from refrain.ui.file_view import FileView
from refrain.ui.hover import with_status_tips
from refrain.ui.no_wheel import install_no_wheel
from refrain.ui.playlist_panel import PlaylistPanel
from refrain.ui.qt_util import app, shortcut
from refrain.ui.shell import Shell
from refrain.ui.side_panel.panel import SelectedItem, SidePanel
from refrain.ui.sidebar import Sidebar
from refrain.ui.toast import Toast
from refrain.ui.toolbar_icons import tool_icon
from refrain.ui.tools.tool_cards import CLEANUP, RENAME, REPLAYGAIN, TIDY, ToolCards

log = logging.getLogger(__name__)

APP_TITLE = "Refrain"
PAUSED_NOTE = " (paused while a task reads albums)"
WAIT_FOR_INDEX_S = 120  # a folder from outside the app opens once indexed, within this (SPEC §29)


LYRICS_RETRY_TIP = "Try reaching LRCLIB again now instead of waiting."  # the status bar's Retry now

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
    renameDone = Signal(object, bool)  # RenameResult, whether it was an Undo
    renameFailed = Signal(str)
    playlistExported = Signal(str, int)  # the saved file, its track count (emitted from the shell thread)
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
        self._indexing_paused = False  # while a task reads whole albums (the library says so)
        self._waiting_folder: tuple[str, float] | None = None  # (path, deadline): opens once indexed (SPEC §29)
        self.shell = Shell("shell", self)  # handing files to Windows, off the UI thread
        self.setWindowTitle(APP_TITLE)
        self.resize(1200, 760)
        self.index_label = QLabel()
        self.index_label.hide()
        self.statusBar().addPermanentWidget(self.index_label)
        self.lyrics_label = QLabel()
        self.lyrics_label.hide()
        self.lyrics_button = QToolButton()  # Retry now, while LRCLIB is unreachable (no Pause since 0.8.3)
        self.lyrics_button.setText("Retry now")
        self.lyrics_button.setToolTip(LYRICS_RETRY_TIP)
        self.lyrics_button.setAutoRaise(True)
        self.lyrics_button.clicked.connect(self._lyrics_button_clicked)
        self.lyrics_button.hide()
        self.statusBar().addPermanentWidget(self.lyrics_label)
        self.statusBar().addPermanentWidget(self.lyrics_button)
        self._lyrics_state = ""

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
        # Library search (SPEC §6): the query is typed in the toolbar, the results replace the file list.
        self.search = SearchController(self, library, self)
        self.search_view, self.search_field = self.search.view, self.search.field
        self.center = QStackedWidget()  # the file list, the search results, or a tool's page
        self.center.addWidget(self.file_view)
        self.center.addWidget(self.search_view)
        # The playlist builder (SPEC §15): a narrow column between the centre and the side panel, hidden until asked.
        self.playlist_panel = PlaylistPanel(conn)
        self.playlist_file: Path | None = (self.settings.path.with_name("playlist.json")
                                           if self.settings.path is not None else None)  # None: not kept (tests)
        self._playlist_save = QTimer(self, singleShot=True, interval=500)
        self._playlist_save.timeout.connect(self.save_playlist)
        self.playlist_panel.model.changed.connect(self._playlist_save.start)
        self._playlist_refresh = QTimer(self, singleShot=True, interval=1000)  # the index changed: rows follow
        self._playlist_refresh.timeout.connect(self.playlist_panel.model.refresh)
        self.playlist_panel.model.added.connect(self.show_playlist_added)
        self.playlist_panel.playRequested.connect(self.play_playlist)
        self.playlist_panel.exportRequested.connect(self.export_playlist)
        self.playlist_panel.relative_paths.setChecked(self.settings.playlist_relative)
        self.playlist_panel.relative_paths.toggled.connect(lambda on: setattr(self.settings, "playlist_relative", on))
        self.playlistExported.connect(self._playlist_exported)
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
        self._build_navigation(conn)
        self._restore_state()
        self._restore_playlist()
        self._apply_tree()  # hidden by default; refresh_roots applies it again once the folders are known

        self.add_folder_action = QAction("&Add folder…", self, shortcut=QKeySequence("Ctrl+O"))
        self.add_folder_action.triggered.connect(self.add_folder)
        self.addAction(self.add_folder_action)  # Ctrl+O; the sidebar's Add folder… is the visible way (0.8.4)
        # No menu bar (SPEC §36 task 3): the … button at the search row's right end holds Options…, About and Exit.
        # No Add folder… or Rescan library since 0.8.4 (SPEC §30); every option in one window, the Options… window.
        self.options_action = QAction("&Options…", self)
        self.options_action.triggered.connect(lambda: self.tools.open_options())
        self.about_action = QAction("&About Refrain", self)
        self.about_action.triggered.connect(self.open_about)
        self.exit_action = QAction("E&xit", self)
        self.exit_action.triggered.connect(self.close)
        more = QMenu(self)
        more.addAction(self.options_action)
        more.addAction(self.about_action)
        more.addSeparator()
        more.addAction(self.exit_action)
        self.more_button.setMenu(with_status_tips(more))

        self.overlay = DropOverlay(self)
        self.toast = Toast(self)
        self.setAcceptDrops(library is not None)
        self.sidebar.addFolderRequested.connect(self.add_folder)
        self.sidebar.removeRequested.connect(self.remove_root)
        self.sidebar.folderSelected.connect(self.open_folder)
        self.file_view.folderActivated.connect(self.open_folder)
        self.file_view.fileActivated.connect(lambda entry_id: self.open_files([entry_id]))
        self.file_view.menuRequested.connect(lambda ids, pos: self.list_menu(ids).exec(pos))
        self.sidebar.folder_menu_provider = self._tree_menu
        self.file_view.renameRequested.connect(self.rename_entry)
        self.sidebar.renameRequested.connect(self.rename_entry)
        self.renameDone.connect(self._renamed)
        self.renameFailed.connect(lambda message: self.toast.show_message(message))
        # Each page's selection counts only while it is on screen.
        self.file_view.selectionChanged.connect(
            lambda ids: None if self.showing_search else self._selection_changed(ids))
        self.search_view.selectionChanged.connect(
            lambda ids: self._selection_changed(ids) if self.showing_search else None)
        self.side_panel.metadata.fileSaved.connect(self._file_saved)
        self.side_panel.metadata.renameSuggested.connect(self.rename_path)
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
            library.addFinished.connect(self.show_add_results)
            library.failed.connect(lambda message: self.statusBar().showMessage(message, STATUS_MS))
            library.notice.connect(lambda message: self.statusBar().showMessage(message, STATUS_MS))
            library.indexingPaused.connect(self._show_indexing_paused)
            library.indexer.progress.connect(self.show_index_progress)
            library.indexer.idle.connect(self.index_label.hide)
            library.indexer.failed.connect(lambda message: self.statusBar().showMessage(message, STATUS_MS))
            library.indexer.folderUpdated.connect(self.folder_updated)
            library.indexer.scanFinished.connect(self.scan_finished)
            # A folder asked for from File Explorer (SPEC §29) opens as soon as the index has it.
            for signal in (library.indexer.progress, library.indexer.folderUpdated, library.indexer.scanFinished,
                           library.rootsChanged):
                signal.connect(self._open_waiting_folder)
            library.lyrics.foldersChanged.connect(self.lyrics_updated)
            library.lyrics.unreachable.connect(self.lrclib_unreachable)
            library.lyrics.progress.connect(self.show_lyrics_progress)
            library.lyrics.entryReady.connect(self._lyrics_entry)
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

    def _build_navigation(self, conn) -> None:
        self.back_action = QAction("Back", self)
        # Explicit keys: Qt's standard Back also binds Backspace, which goes up here (SPEC §5), and a key bound
        # twice is ambiguous and fires nothing.
        self.back_action.setShortcut(QKeySequence("Alt+Left"))
        self.back_action.triggered.connect(self.go_back)
        self.back_action.setToolTip("Go back to the folder you were in before (Alt+Left)")
        self.forward_action = QAction("Forward", self)
        self.forward_action.setShortcut(QKeySequence("Alt+Right"))
        self.forward_action.triggered.connect(self.go_forward)
        self.forward_action.setToolTip("Go forward again to the folder you went back from (Alt+Right)")
        self.up_action = QAction("Up", self)
        self.up_action.setShortcut(QKeySequence("Alt+Up"))
        self.up_action.triggered.connect(self.go_up)
        self.up_action.setToolTip("Open the folder that contains this one (Alt+Up)")
        self.refresh_action = QAction("Rescan folder", self)
        self.refresh_action.setShortcut(QKeySequence(QKeySequence.StandardKey.Refresh))
        self.refresh_action.triggered.connect(self.rescan_folder)
        self.refresh_action.setToolTip("Read this folder from the disk again now, to show changes made outside "
                                       "Refrain (F5)")
        # The folder tree is a side function (SPEC §33): hidden by default, one click shows it.
        self.folders_action = QAction("Folders", self, checkable=True, shortcut=QKeySequence("Ctrl+Shift+F"))
        self.folders_action.setToolTip("Show or hide the folder tree (Ctrl+Shift+F)")
        self.folders_action.toggled.connect(self.set_tree_visible)
        self.explorer_action = QAction("Open in File Explorer", self)
        self.explorer_action.setToolTip("Open this folder in File Explorer")
        self.explorer_action.triggered.connect(
            lambda: self.reveal(self.folder_id) if self.folder_id is not None else None)
        self.address_action = QAction("Edit address", self, shortcut=QKeySequence("Ctrl+L"))
        self.address_action.triggered.connect(lambda: self.address_bar.edit())
        self.addAction(self.address_action)

        self.address_bar = AddressBar(
            lambda entry_id: [(f.id, f.name) for f in queries.child_folders(
                conn(), entry_id, only_music=self.settings.hide_folders_without_music)] if conn() else [])
        self.address_bar.folderRequested.connect(self.open_folder)
        self.address_bar.pathEntered.connect(self.go_to_path)

        # The search first, on a row of its own, big and centred (SPEC §33); the address bar below, alone.
        search_bar = QToolBar("Search")
        search_bar.setMovable(False)
        search_bar.setObjectName("search")
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(0)  # the toggle joined to the field
        layout.addStretch(1)
        layout.addWidget(self.search.modes)
        layout.addWidget(self.search_field, 6)  # most of the row, up to its widest
        layout.addStretch(1)
        self.more_button = QToolButton()  # its menu: __init__, once the actions exist
        self.more_button.setObjectName("moreButton")
        self.more_button.setAutoRaise(True)
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_button.setToolTip("Options…, About and Exit")
        layout.addWidget(self.more_button)
        search_bar.addWidget(row)
        self.search_row = row
        self.search_bar = search_bar
        self.search_action = QAction("Search library", self, shortcut=QKeySequence("Ctrl+K"))
        self.search_action.triggered.connect(self.search.open)
        self.addAction(self.search_action)
        self.addToolBar(search_bar)
        self.addToolBarBreak()

        toolbar = QToolBar("Navigation")
        toolbar.setMovable(False)
        toolbar.setObjectName("navigation")
        toolbar.addAction(self.folders_action)
        toolbar.addSeparator()
        for action in (self.back_action, self.forward_action, self.up_action, self.refresh_action):
            toolbar.addAction(action)
        toolbar.addWidget(self.address_bar)
        toolbar.addAction(self.explorer_action)
        for action in (self.folders_action, self.explorer_action):  # labelled: clear at a glance
            button = toolbar.widgetForAction(action)
            if isinstance(button, QToolButton):
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)
        self.navigation_bar = toolbar
        self.addToolBarBreak()
        self._build_playlist_and_advanced_search()
        self._build_tools_bar()

        # Backspace goes up, but only from the lists (it edits text everywhere else).
        for view in (self.file_view.view, self.sidebar.tree):
            shortcut(QKeySequence(Qt.Key.Key_Backspace), view, self.go_up, Qt.ShortcutContext.WidgetShortcut)
        # Del sends the focused view's selection to the Recycle Bin. Shift+Del does the same: the app never
        # deletes permanently (SPEC §19). The playlist's own Del only removes tracks from the playlist.
        for view, selected in ((self.file_view.view, self.file_view.selected_ids),
                               (self.sidebar.tree, self.sidebar.tree.selected_ids),
                               (self.search_view.view, self.search_view.selected_ids)):
            for key in ("Del", "Shift+Del"):
                shortcut(QKeySequence(key), view,
                         lambda view=view, selected=selected: self.delete.delete_entries(selected(), view),
                         Qt.ShortcutContext.WidgetShortcut)
        self._update_navigation()

    def _build_tools_bar(self) -> None:
        """The second row (SPEC §30), always visible: Tidy, Rename, ReplayGain and Clean up, then the playlist
        builder and Advanced search. Tidy, Rename and Clean up are plain buttons starting their right-click entry on
        the selection or the open folder; ReplayGain lists its entries (SPEC §35). The options are in File >
        Options… only. Icons with a short label; Tidy first and in evidence (SPEC §33)."""
        bar = QToolBar("Tools")
        bar.setMovable(False)
        bar.setObjectName("tools")
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.tools_bar = bar
        self.tool_actions: dict[str, QAction] = {}
        for tool, label in ((TIDY, "Tidy"), (RENAME, "Rename"), (REPLAYGAIN, "ReplayGain"), (CLEANUP, "Clean up")):
            action = QAction(label, self)
            if tool == REPLAYGAIN:  # its entries, as the right-click menu lists them (Hugo, 2026-10-25)
                entries = QMenu(self)
                entries.aboutToShow.connect(lambda entries=entries: self.tools.fill_replaygain_menu(entries))
                action.setMenu(entries)
            else:
                action.triggered.connect(lambda _=False, tool=tool: self.tools.start(tool))
            action.hovered.connect(lambda tool=tool: self._update_tool_tip(tool))
            bar.addAction(action)
            if tool == REPLAYGAIN and isinstance(button := bar.widgetForAction(action), QToolButton):
                button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)  # a click opens the list
            self.tool_actions[tool] = action
            self._update_tool_tip(tool)
        self.tidy_action = self.tool_actions[TIDY]
        self._set_tidy_in_evidence(bar)
        bar.addSeparator()
        self.playlist_action.setText("Playlist")
        bar.addAction(self.playlist_action)
        bar.addAction(self.advanced_search_action)
        self.addToolBar(bar)
        self._tint_tool_icons()
        if (current := theme.current()) is not None:
            current.changed.connect(self._tint_tool_icons)  # light and dark, or a new accent colour

    def _tint_tool_icons(self) -> None:
        """The toolbars' line drawings in the text colour, Tidy's in the colour that reads on the accent."""
        color = self.palette().color(QPalette.ColorRole.ButtonText)
        for tool, action in self.tool_actions.items():
            action.setIcon(tool_icon(tool, theme.on_accent() if tool == TIDY else color))
        for name, action in (("playlist", self.playlist_action), ("advanced_search", self.advanced_search_action),
                             ("folders", self.folders_action), ("back", self.back_action),
                             ("forward", self.forward_action), ("up", self.up_action),
                             ("rescan", self.refresh_action), ("explorer", self.explorer_action)):
            action.setIcon(tool_icon(name, color))
        self.more_button.setIcon(tool_icon("more", color))
        self.search.search_icon.setIcon(tool_icon("search", color))

    def _set_tidy_in_evidence(self, bar: QToolBar) -> None:
        """Tidy, the tool the app is built around (SPEC §33, Hugo 2026-10-24): larger than the other buttons and
        filled with Windows' accent colour (theme.style_sheet), its icon and label in white or black, whichever
        reads on it."""
        button = bar.widgetForAction(self.tidy_action)
        if not isinstance(button, QToolButton):
            return
        button.setObjectName("tidyButton")
        button.setIconSize(bar.iconSize() * 1.5)
        font = theme.scaled(button.font(), theme.SCALE_TIDY)
        font.setBold(True)
        button.setFont(font)

    def _update_tool_tip(self, tool: str) -> None:
        """The hover text names the target, or says why a click can't start, read as the mouse comes over the
        button (SPEC §30). The button stays enabled: a click says the same in the status bar."""
        self.tool_actions[tool].setToolTip(self.tools.hover_text(tool))

    def _build_playlist_and_advanced_search(self) -> None:
        self.playlist_action = QAction("Playlist builder", self, checkable=True,
                                       shortcut=QKeySequence("Ctrl+Shift+P"))
        self.playlist_action.setToolTip("Show or hide the playlist builder (Ctrl+Shift+P)")
        self.playlist_action.toggled.connect(self.set_playlist_visible)
        self.advanced_search_action = QAction("Advanced search", self)
        self.advanced_search_action.setToolTip("Open a window listing the albums to fix by hand, like those without "
                                               "ReplayGain tags or not split yet")
        self.advanced_search_action.triggered.connect(self.open_advanced_search)
        self.advanced_search_action.setEnabled(self._library_open())
        self.advanced_search: AdvancedSearchWindow | None = None  # made on first use (SPEC §26)

    def _update_navigation(self) -> None:
        open_ = self._library_open() and self.folder_id is not None
        self.back_action.setEnabled(bool(self.history_back))
        self.forward_action.setEnabled(bool(self.history_forward))
        self.up_action.setEnabled(open_ and self.folder_id is not None
                                  and queries.parent_folder(self.open_library.conn, self.folder_id) is not None)
        self.refresh_action.setEnabled(open_)
        self.explorer_action.setEnabled(open_)

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
        self._refresh_playlist_later()
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

    def _show_indexing_paused(self, paused: bool) -> None:
        """The library pauses indexing while a task reads whole albums (`LibraryService.DRIVE_OWNERS`), whichever
        window started it (SPEC §35); the status line says so."""
        self._indexing_paused = paused
        if self.index_label.isVisible():
            text = self.index_label.text().removesuffix(PAUSED_NOTE)
            self.index_label.setText(text + PAUSED_NOTE if paused else text)

    def lyrics_updated(self, folder_ids: list[int]) -> None:
        """LRCLIB results: the open folder's LRCLIB column follows, and the search results (their text too)."""
        if not self._library_open():
            return
        self.search.refresh_later()
        if self.folder_id not in folder_ids:
            return
        self.file_view.refresh()
        self.side_panel.refresh_folder()

    def show_lyrics_progress(self, progress: LyricsProgress) -> None:
        """The status bar's lyrics label, and Retry now while LRCLIB is unreachable (SPEC §9 Controls)."""
        if not self._library_open():
            return
        if progress.state != self._lyrics_state:  # offline on or off: the LRCLIB column's pending rows change
            self._lyrics_state = progress.state
            self.file_view.model.refresh_column("lrclib")
            self.search_view.model.refresh_column("lrclib")
        left = f"{progress.pending:,} to look up" if progress.pending is not None else "starting"
        if progress.state == OFFLINE:
            text = "Lyrics: lookups off"
        elif progress.state == PAUSED:  # the service can still pause (tests); the app no longer asks it to
            text = f"Lyrics paused: {left}"
        elif progress.state == UNREACHABLE:
            resume = time.strftime("%H:%M", time.localtime(self.open_library.lyrics.resume_at))
            text = f"LRCLIB unreachable, retrying at {resume}"
        elif progress.state == RUNNING:
            text = f"Looking up lyrics: {left}"
        elif progress.pending:
            text = f"Lyrics: {progress.pending:,} to retry later"
        else:
            text = ""
        self.lyrics_label.setText(text)
        self.lyrics_label.setVisible(bool(text))
        self.lyrics_button.setVisible(progress.state == UNREACHABLE)

    def _lyrics_button_clicked(self) -> None:
        if self._library_open():
            self.open_library.lyrics.retry_now()

    def _lyrics_entry(self, _key: str, entry: dict) -> None:
        if entry.get("status") == "error" and self._library_open():
            self.file_view.model.refresh_column("lrclib")  # the failed key's rows show "error"
            self.search_view.model.refresh_column("lrclib")

    def lrclib_unreachable(self, resume_at: float) -> None:
        minutes = max(1, round((resume_at - time.time()) / 60))
        self.toast.show_message(f"LRCLIB unreachable, retrying in {minutes} min.", "Retry now",
                                self.open_library.lyrics.retry_now)

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
        self._refresh_playlist_later()
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

    # -- the playlist builder (SPEC §15) --

    # -- the folder tree (SPEC §33) --

    def set_tree_visible(self, visible: bool) -> None:
        """The Folders button: the user's choice, kept. The tree still shows while the library is empty, where its
        Add folder… button is."""
        self.settings.tree_visible = visible
        self._apply_tree()

    def _apply_tree(self) -> None:
        shown = self.settings.tree_visible or self.sidebar.empty
        empty = self.sidebar.empty
        self.folders_action.setEnabled(not empty)
        self.folders_action.setToolTip("The folder tree shows until a folder is added" if empty
                                       else "Show or hide the folder tree (Ctrl+Shift+F)")
        if self.folders_action.isChecked() != shown:
            self.folders_action.blockSignals(True)
            self.folders_action.setChecked(shown)
            self.folders_action.blockSignals(False)
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

    def set_playlist_visible(self, visible: bool) -> None:
        """Show or hide the panel. It takes its width from the centre only, so the tree and the side panel keep
        theirs (the list scrolls sideways if its columns no longer fit), and gives it back when hidden."""
        if visible == self.playlist_panel.isVisible():
            return
        sizes = self.splitter.sizes()
        if visible:
            width = min(max(self.settings.playlist_width, 200), max(0, sizes[1] - 200))
            sizes[1] -= width
            sizes[2] = width
            self.playlist_panel.show()
        else:
            if sizes[2] > 0:
                self.settings.playlist_width = sizes[2]
            sizes[1] += sizes[2]
            sizes[2] = 0
            self.playlist_panel.hide()
        self.splitter.setSizes(sizes)
        self.settings.playlist_visible = visible
        if self.playlist_action.isChecked() != visible:
            self.playlist_action.setChecked(visible)

    def show_playlist_added(self, added: int, skipped: int) -> None:
        if not added and not skipped:
            text = "Nothing to add: there is no music in what was dropped."
        else:
            text = f"Added {added:,} track{'s' if added != 1 else ''} to the playlist."
            if skipped:
                text += f" {skipped:,} already in it {'were' if skipped != 1 else 'was'} skipped."
        self.statusBar().showMessage(text, STATUS_MS)

    def play_playlist(self) -> None:
        """Through a temporary playlist, as Play selection (SPEC §14); tracks gone from the library are left out."""
        entries = self.playlist_panel.entries()
        self._play(entries, str(entries[0].path) if entries else "", False)

    def choose_export_file(self, suggested: str) -> str:
        """Replaced in tests. The dialog asks before replacing a file."""
        target, _filter = QFileDialog.getSaveFileName(self, "Export the playlist", suggested,
                                                      "M3U8 playlist (*.m3u8)")
        return target

    def export_playlist(self) -> None:
        """Save the playlist as a .m3u8 where the user chooses (a Ground rule 1 exception), off the UI thread."""
        entries = self.playlist_panel.entries()
        if not entries:
            self.statusBar().showMessage("The playlist has no tracks to export.", STATUS_MS)
            return
        folder = Path(self.settings.playlist_export_dir or Path.home() / "Music")
        chosen = self.choose_export_file(str(folder / "Playlist.m3u8"))
        if not chosen:
            return
        target = Path(chosen)
        if target.suffix.lower() != ".m3u8":
            target = target.with_name(target.name + ".m3u8")
        self.settings.playlist_export_dir = str(target.parent)
        relative = self.playlist_panel.relative_paths.isChecked()

        def run() -> None:
            playlists.export_playlist(entries, target, relative)
            self.playlistExported.emit(str(target), len(entries))

        self.shell.run(run, f"The playlist couldn't be saved as {target}.", str(target.parent), True)

    def _playlist_exported(self, target: str, count: int) -> None:
        text = f"Exported {count:,} track{'s' if count != 1 else ''} to {target}."
        missing = self.playlist_panel.model.rowCount() - count
        if missing > 0:
            text += f" {missing} not in the library {'were' if missing != 1 else 'was'} left out."
        self.statusBar().showMessage(text, STATUS_MS)

    def _restore_playlist(self) -> None:
        self.playlist_panel.restore_layout(self.settings.playlist_header)
        if self.playlist_file is not None:
            self.playlist_panel.model.set_builder(Builder.load(self.playlist_file))
        if self.settings.playlist_visible:
            self.set_playlist_visible(True)

    def save_playlist(self) -> None:
        if self.playlist_file is not None:
            self.playlist_panel.model.builder.save(self.playlist_file)

    def _refresh_playlist_later(self) -> None:
        if self.playlist_panel.model.rowCount():
            self._playlist_refresh.start()

    def save_state(self) -> None:
        visible = self.playlist_panel.isVisible()
        if visible:  # the splitter's state is saved without the panel; its width and visibility are kept apart
            self.set_playlist_visible(False)
            self.settings.playlist_visible = True
        self.settings.playlist_header = self.playlist_panel.save_layout()
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
        self._playlist_save.stop()
        self.save_playlist()
        self.search.shutdown()
        if self.advanced_search is not None:
            self.advanced_search.close()
            self.advanced_search.service.close()
        self.save_state()
        app().removeEventFilter(self)
        self.shell.shutdown()
        super().closeEvent(event)
        self.closed.emit()

    # -- context menus (SPEC §5): only what exists so far; Rename, Tidy and ReplayGain come with their milestones

    def list_menu(self, ids: list[int]) -> QMenu:
        """The file list's menu: for the selection, or for the open folder when the click was on the background."""
        menu = QMenu(self)
        if not self._library_open():
            return with_status_tips(menu)
        if not ids:
            if self.folder_id is not None:
                self.add_entries(menu, [self.folder_id], background=True)
                self.add_folder_actions(menu, self.folder_id, include_open=False)
            return with_status_tips(menu)
        self.add_entries(menu, ids)
        if len(ids) == 1 and self.file_view.is_dir(ids[0]):
            self.add_folder_actions(menu, ids[0], rename=lambda: self.file_view.start_rename(ids[0]),
                                    delete=lambda: self.delete.delete_entries(ids, self.file_view.view))
            return with_status_tips(menu)
        open_action = menu.addAction("Open", lambda: self.open_files(ids))
        open_action.setEnabled(len(ids) == 1)
        play = menu.addAction("Play selection", lambda: self.play_selection(ids))
        play.setEnabled(bool(queries.playlist_for_ids(self.open_library.conn, ids)))
        menu.addSeparator()
        menu.addAction("Open in File Explorer", lambda: self.reveal(ids[0]))
        menu.addAction("Copy path" if len(ids) == 1 else "Copy paths", lambda: self.copy_paths(ids))
        menu.addSeparator()
        if len(ids) == 1:
            menu.addAction("Rename\tF2", lambda: self.file_view.start_rename(ids[0]))
            menu.addSeparator()
        menu.addAction("Delete\tDel", lambda: self.delete.delete_entries(ids, self.file_view.view))
        return with_status_tips(menu)

    def add_folder_actions(self, menu: QMenu, entry_id: int, include_open: bool = True, rename=None,
                           delete=None) -> None:
        if include_open:
            menu.addAction("Open", lambda: self.open_folder(entry_id))
        menu.addAction("Play folder", lambda: self.play_folder(entry_id, recursive=False))
        menu.addAction("Play folder and subfolders", lambda: self.play_folder(entry_id, recursive=True))
        menu.addSeparator()
        menu.addAction("Open in File Explorer", lambda: self.reveal(entry_id))
        menu.addAction("Copy path", lambda: self.copy_paths([entry_id]))
        menu.addSeparator()
        if rename is not None and queries.parent_folder(self.open_library.conn, entry_id) is not None:
            menu.addAction("Rename\tF2", rename)  # not a library root
        menu.addAction("Rescan", lambda: self.open_library.indexer.request_folder(entry_id))
        if delete is not None and queries.parent_folder(self.open_library.conn, entry_id) is not None:
            menu.addSeparator()
            menu.addAction("Delete\tDel", delete)  # never a library root (Remove from library is below)

    def add_entries(self, menu: QMenu, ids: list[int], background: bool = False) -> None:
        """Explorer's right-click entries for these items (SPEC §31, §35: the same rules, labels and windows), as
        items of this menu itself, one group then a separator: no Refrain submenu in the app (Hugo, 2026-10-25).
        `background`: the open folder, as empty space. Open in Refrain and Settings are Explorer's only."""
        paths, folders = self.entry_paths(ids)
        if not paths:
            return
        selection = Selection.of(paths, background, is_dir=folders.__contains__)  # the index knows, not the disk
        added = False
        for item in menu_entries(selection, self.tools.cards.replaygain_card.options().target):
            if item.entry in (OPEN, SETTINGS):
                continue
            action = menu.addAction(item.label, lambda entry=item.entry: self.entryRequested.emit(entry, paths))
            action.setStatusTip(item.help)
            added = True
        if added:
            menu.addSeparator()

    def entry_paths(self, ids: list[int]) -> tuple[list[str], set[str]]:
        """These entries' paths, in order, and which of them are folders (from the index: the disk isn't asked)."""
        conn = self.open_library.conn
        paths, folders = [], set()
        for entry_id in ids:
            row = conn.execute("SELECT is_dir FROM entries WHERE id = ?", (entry_id,)).fetchone()
            if row is None:
                continue
            path = str(path_of(conn, entry_id))
            paths.append(path)
            if row[0]:
                folders.add(path)
        return paths, folders

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

    def _tree_menu(self, menu: QMenu, entry_id: int) -> None:
        """The folder tree's menu: the entries for its selection if the clicked folder is in it, else for that
        folder (as Delete), then the folder's own actions."""
        selected = self.sidebar.tree.selected_ids()
        self.add_entries(menu, selected if entry_id in selected else [entry_id])
        self.add_folder_actions(menu, entry_id, rename=lambda: self.sidebar.tree.start_rename(entry_id),
                                delete=lambda: self._tree_delete(entry_id))

    def _tree_delete(self, entry_id: int) -> None:
        """Delete from the tree's menu: its selection if the clicked folder is in it, else that folder."""
        selected = self.sidebar.tree.selected_ids()
        self.delete.delete_entries(selected if entry_id in selected else [entry_id], self.sidebar.tree)

    # -- inline rename (SPEC §11) --

    def confirm_extension_change(self, old: str, new: str) -> bool:
        answer = QMessageBox.question(
            self, "Change the extension?",
            f"Change the extension of {old} to {Path(new).suffix or '(none)'}?\n\nThe file may stop opening in its "
            "usual app, or stop counting as audio.")
        return answer == QMessageBox.StandardButton.Yes

    def rename_path(self, path: str, new_name: str) -> None:
        """A rename asked by path (the hint after a save)."""
        if not self._library_open():
            return
        found = find_folder(self.open_library.conn, os.path.dirname(path))
        entry_id = queries.find_child(self.open_library.conn, found[0], os.path.basename(path)) \
            if found and found[1] else None
        if entry_id is not None:
            self.rename_entry(entry_id, new_name)

    def rename_entry(self, entry_id: int, new_name: str) -> None:
        """Rename a file or folder on disk (off the UI thread), then update the index in place."""
        if not self._library_open() or not self._folder_exists(entry_id):
            return
        conn = self.open_library.conn
        if queries.parent_folder(conn, entry_id) is None:
            self.toast.show_message("A library folder can't be renamed here. Rename it in File Explorer, then "
                                    "add it again.")
            return
        path = path_of(conn, entry_id)
        if new_name == path.name:
            return
        is_dir = bool(conn.execute("SELECT is_dir FROM entries WHERE id = ?", (entry_id,)).fetchone()[0])
        if not is_dir and Path(new_name).suffix.lower() != path.suffix.lower() and \
                not self.confirm_extension_change(path.name, new_name):
            return
        edited = self.side_panel.metadata.path == path or path in self.side_panel.metadata.paths
        if (edited or is_dir) and not self.side_panel.can_leave():
            return
        locks = self.open_library.locks

        def run() -> None:
            try:
                result = inline_rename.rename_entry(path, new_name, locks)
            except inline_rename.RenameError as e:
                self.renameFailed.emit(str(e))
                return
            self.renameDone.emit(result, False)

        self.shell.submit(run)

    def _renamed(self, result: inline_rename.RenameResult, was_undo: bool) -> None:
        if not result.steps:
            return
        if self._library_open():
            self.open_library.indexer.apply_renames(result.steps)
        old, new = result.steps[0]
        if was_undo:
            self.statusBar().showMessage(f"Renamed {old.name} back to {new.name}.", STATUS_MS)
            return
        text = f"Renamed {old.name} to {new.name}"
        if result.sidecar:
            text += f", and its lyrics file {result.sidecar[0].name} to {result.sidecar[1].name}"
        text += "."
        if result.warnings:
            text += " " + " ".join(result.warnings)
        locks = self.open_library.locks if self._library_open() else None

        def undo() -> None:
            def run() -> None:
                try:
                    back = inline_rename.undo(result, locks)
                except inline_rename.RenameError as e:
                    self.renameFailed.emit(str(e))
                    return
                self.renameDone.emit(back, True)
            self.shell.submit(run)

        self.toast.show_message(text, "Undo", undo, timeout_ms=10_000)

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
        self._play(entries, str(path_of(self.open_library.conn, entry_id)), True)

    def play_selection(self, ids: list[int]) -> None:
        if not self._library_open():
            return
        entries = queries.playlist_for_ids(self.open_library.conn, ids)
        self._play(entries, str(entries[0].path) if entries else "", False)

    def _play(self, entries: list[playlists.PlaylistEntry], fallback: str, fallback_is_dir: bool) -> None:
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

    # -- indexing --

    def show_index_progress(self, progress: ScanProgress) -> None:
        root = next((r for r in self.library.roots() if r.id == progress.root_id), None) if self.library else None
        name = (os.path.basename(root.path) or root.path) if root else "library"  # a drive root has no basename
        if progress.phase == "structure":
            text = f"Indexing {name}: {progress.folders:,} folders, {progress.files:,} files"
        elif progress.phase == "tags":
            text = f"Reading tags in {name}: {progress.tags_done:,} / {progress.tags_total:,}"
        elif progress.phase == "stats":
            text = f"Indexing {name}: totals"
        else:
            text = f"Indexed {name}: {progress.files:,} files"
        self.index_label.setText(text + (PAUSED_NOTE if self._indexing_paused else ""))
        self.index_label.show()

    # -- roots --

    def refresh_roots(self) -> None:
        if not self._library_open():
            return
        self._keep_open_folder()  # its root may have been removed, or merged into a new one
        self.sidebar.set_roots(self.open_library.roots())
        self._apply_tree()

    def choose_folder(self) -> str:
        return QFileDialog.getExistingDirectory(self, "Add a music folder to the library")

    def add_folder(self) -> None:
        if self.library is None:
            return
        folder = self.choose_folder()
        if folder:
            self.library.add_folders([folder])

    def confirm_remove(self, root: Root) -> bool:
        answer = QMessageBox.question(
            self, "Remove from library",
            f"Remove {root.path} from the library?\n\nNothing on disk changes: only the app's index of this folder "
            "is deleted. Adding it again rescans it.")
        return answer == QMessageBox.StandardButton.Yes

    def remove_root(self, root: Root) -> None:
        if self.library is not None and self.confirm_remove(root):
            self.library.remove_root(root.id)
            self.statusBar().showMessage(f"Removed {root.path} from the library. Nothing on disk changed.",
                                         STATUS_MS)

    def show_add_results(self, results: list[AddResult]) -> None:
        messages, files = [], []
        for result in results:
            match result.status:
                case AddStatus.ADDED:
                    messages.append(f"Added {result.path} to the library.")
                case AddStatus.MERGED:
                    count = len(result.merged)
                    messages.append(f"Added {result.path} to the library. {count} library folder"
                                    f"{'s' if count > 1 else ''} inside it became its subfolders.")
                case AddStatus.ALREADY_ROOT:
                    messages.append(f"{result.path} is already in the library.")
                case AddStatus.INSIDE_ROOT:
                    messages.append(f"{result.path} is already in the library.")
                case AddStatus.IS_A_FILE:
                    files.append(result.path)
                case AddStatus.NOT_A_FOLDER:
                    messages.append(f"{result.path} can't be added: the folder doesn't exist or can't be reached.")
        if messages:
            self.statusBar().showMessage(" ".join(messages), STATUS_MS)
        last = next((r for r in reversed(results) if r.root_id is not None), None)
        if last is not None and last.root_id is not None:
            self.sidebar.select_root(last.root_id)
        if files:
            folders = list(dict.fromkeys(os.path.dirname(path) for path in files))
            what = "its folder" if len(folders) == 1 else "their folders"
            self.toast.show_message(
                "Only folders can be added to the library, not files.", f"Add {what}",
                lambda: self.open_library.add_folders(folders))

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
