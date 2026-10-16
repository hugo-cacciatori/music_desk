import base64
import dataclasses
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDropEvent,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QStyle,
    QToolBar,
    QToolButton,
    QWidget,
)

from music_desk.core import cue, fsops, inline_rename, playlists, rename
from music_desk.core.index import queries
from music_desk.core.index.roots import AddResult, AddStatus, Root
from music_desk.core.index.scanner import ScanProgress, ScanReport, find_folder
from music_desk.core.index.search import LIMIT
from music_desk.core.index.store import path_of
from music_desk.core.path_locks import LockedError, PathLocks
from music_desk.core.playlist_builder import Builder
from music_desk.licenses import licenses_dir
from music_desk.services import jobs as jobs_module
from music_desk.services.jobs import JobList
from music_desk.services.library import LibraryService
from music_desk.services.lyrics_embedder import AutoEmbedder
from music_desk.services.lyrics_service import OFFLINE, PAUSED, RUNNING, UNREACHABLE, LyricsProgress
from music_desk.services.rename_job import Applied, Planned, RenameRunner
from music_desk.services.replaygain_job import Finished, ReplayGainRunner
from music_desk.services.search_service import SearchResults, SearchService
from music_desk.services.split_job import Planned as SplitPlanned
from music_desk.services.split_job import Replanned, SplitFinished, SplitRunner
from music_desk.services.tag_io import TagIO
from music_desk.services.tag_writer import TagWriter
from music_desk.settings import Settings
from music_desk.ui.about_dialog import AboutDialog
from music_desk.ui.address_bar import AddressBar
from music_desk.ui.file_view import FileView
from music_desk.ui.jobs_panel import JobsPanel
from music_desk.ui.playlist_panel import PlaylistPanel
from music_desk.ui.search_view import SearchView
from music_desk.ui.search_view import sort_keys as search_sort_keys
from music_desk.ui.side_panel.panel import SelectedItem, SidePanel
from music_desk.ui.sidebar import Sidebar
from music_desk.ui.toast import Toast
from music_desk.ui.tools.rename_preview import RenamePreview
from music_desk.ui.tools.rg_report import ReplayGainView, short_result
from music_desk.ui.tools.split_preview import SplitPreview
from music_desk.ui.tools.split_preview import short_result as split_short_result
from music_desk.ui.tools.tools_page import RENAME, REPLAYGAIN, SPLIT, TIDY, ToolRequest, ToolScope, ToolsPage

log = logging.getLogger(__name__)

APP_TITLE = "MusicDesk"
FIND_QUERIES = (  # the Lyrics menu's Find entries: (menu text, search query)
    ("Find songs LRCLIB didn't find", "lrclib:notfound"),
    ("Find songs without lyrics in their tags", "lyricstag:no lyricstag:lrc"),
    ("Find songs LRCLIB can't look up (no artist or title)", "lrclib:notags"),
)
ACTION_QUERIES = (  # the Action menu's entries over the whole library: (menu text, search query)
    ("Show tracks without ReplayGain tags", "rg:none"),
)
STATUS_MS = 8000
PAUSED_NOTE = " (paused while ReplayGain runs)"
DELETE_OWNER = "a delete"  # its PathLocks owner, as other jobs see it
DELETE_NAMES = 10  # names listed in the question for several items


@dataclasses.dataclass(frozen=True)
class DeleteItem:
    entry_id: int
    path: Path
    is_dir: bool


@dataclasses.dataclass
class DeleteRequest:
    """One Delete, from the check to the report (SPEC §19). Its lock is held until the end."""

    items: list[DeleteItem]
    lock: int
    view: QWidget | None  # where Del was pressed, for the selection afterwards
    next_id: int | None  # the row to select afterwards


class DropOverlay(QLabel):
    """Covers the window while folders are dragged over it."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__("Add to library", parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet("background: rgba(40, 110, 200, 60); border: 3px dashed rgb(40, 110, 200);"
                           " font-size: 22px; font-weight: 600;")
        self.hide()

    def cover(self) -> None:
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()


class MainWindow(QMainWindow):
    shellFailed = Signal(str, str, bool)  # message, a path to show in Explorer instead, whether it is a folder
    renameDone = Signal(object, bool)  # RenameResult, whether it was an Undo
    renameFailed = Signal(str)
    playlistExported = Signal(str, int)  # the saved file, its track count (emitted from the shell thread)
    deleteChecked = Signal(object, list)  # the DeleteRequest, [(path, why it can't be recycled)] (shell thread)
    deleteDone = Signal(object, list, list)  # the DeleteRequest, the sent items, [(name, error)] (shell thread)

    def __init__(self, library: LibraryService | None = None, settings: Settings | None = None) -> None:
        super().__init__()
        self.library = library
        self.settings = settings or Settings()
        self.folder_id: int | None = None
        self.folder_path: str | None = None  # to find the nearest surviving folder if this one disappears
        self.history_back: list[int] = []
        self.history_forward: list[int] = []
        self.rsgain_version: str | None = None  # from the startup self-check (About)
        self.flac_version: str | None = None  # likewise; the cue split needs it (SPEC §22)
        self._indexing_paused = False  # while a ReplayGain job runs
        # Handing files to Windows can stall (a slow drive, a busy shell), so it happens off the UI thread.
        # These three are replaced in tests, which never launch anything.
        self.launch = fsops.open_with_default_app
        self.show_in_explorer = fsops.open_in_explorer
        # Delete (SPEC §19): tests replace both, so they never use the real Recycle Bin.
        self.recycle = fsops.send_to_recycle_bin
        self.recycle_check = fsops.can_recycle
        self.playlist_dir: Path | None = None  # None: %LOCALAPPDATA%\MusicDesk\playlists
        self._shell = ThreadPoolExecutor(1, thread_name_prefix="shell")
        self.setWindowTitle(APP_TITLE)
        self.resize(1200, 760)
        self.index_label = QLabel()
        self.index_label.hide()
        self.statusBar().addPermanentWidget(self.index_label)
        self.lyrics_label = QLabel()
        self.lyrics_label.hide()
        self.lyrics_button = QToolButton()
        self.lyrics_button.setAutoRaise(True)
        self.lyrics_button.clicked.connect(self._lyrics_button_clicked)
        self.lyrics_button.hide()
        self.statusBar().addPermanentWidget(self.lyrics_label)
        self.statusBar().addPermanentWidget(self.lyrics_button)
        self._lyrics_state = ""
        self.jobs_button = QToolButton()
        self.jobs_button.setAutoRaise(True)
        self.jobs_button.setToolTip("Show or hide the jobs panel (Ctrl+J)")
        self.jobs_button.clicked.connect(lambda: self.jobs_dock.setVisible(not self.jobs_dock.isVisible()))
        self.jobs_button.hide()
        self.statusBar().addPermanentWidget(self.jobs_button)
        self.rsgain_label = QLabel("Checking rsgain…")
        self.statusBar().addPermanentWidget(self.rsgain_label)

        conn = lambda: self.library.conn if self._library_open() else None  # noqa: E731
        self.sidebar = Sidebar(conn, self.settings.expanded_folders)
        self.sidebar.model.only_music = self.settings.hide_folders_without_music
        self.file_view = FileView(conn)
        self.tag_io = TagIO(self)
        self.tag_io.name_masks = self._name_masks
        self.tag_writer = TagWriter(
            library.locks if library is not None else PathLocks(),
            on_written=lambda path: self.library.indexer.update_file(path) if self._library_open() else None,
            parent=self)
        self.tag_writer.name_masks = self._name_masks
        self.side_panel = SidePanel(self.tag_io, self.tag_writer, conn,
                                    lyrics_service=library.lyrics if library is not None else None)
        self.side_panel.lyrics.urlRequested.connect(self.open_url)
        self.side_panel.summary.playRequested.connect(self.play_folder)
        self.side_panel.summary.toolsRequested.connect(lambda entry_id: self.open_tools([entry_id]))
        self.side_panel.preview.splitRequested.connect(lambda path: self.split_album(Path(path)))
        self.side_panel.preview.openRequested.connect(lambda path: self._in_shell(
            lambda: self.launch(path), f"Windows couldn't open {os.path.basename(path)}: no app is set for this "
            "file type, or the file isn't reachable.", path, False))
        self.tools_page = ToolsPage(self.settings)
        self.tools_page.backRequested.connect(self.close_tools)
        self.tools_page.runRequested.connect(self.run_tool)
        self.tag_io.renameFields.connect(self._tool_example)
        self.tag_writer.replaygainRemoved.connect(self._replaygain_removed)
        self._rg_remove_token: int | None = None
        self._example_token: int | None = None
        self.rename_preview = RenamePreview()
        self.rename_preview.backRequested.connect(self._rename_back)
        self.rename_preview.doneRequested.connect(self._rename_done)
        self.rename_preview.applyRequested.connect(self.apply_rename)
        self.rename_preview.cancelRequested.connect(lambda: self.rename_runner.cancel())
        self.rename_runner = RenameRunner(library.locks if library is not None else PathLocks(), self)
        self.rename_runner.planned.connect(self._rename_planned)
        self.rename_runner.applied.connect(self._rename_applied)
        self._rename_token: int | None = None
        self._rename_request: ToolRequest | None = None
        self.rg_view = ReplayGainView()
        self.rg_view.backRequested.connect(lambda: self.center.setCurrentWidget(self.tools_page))
        self.rg_view.doneRequested.connect(self.close_tools)
        self.rg_view.cancelRequested.connect(lambda: self.rg_runner.cancel())
        self.rg_view.openLogRequested.connect(self.open_log)
        self.rg_view.revealRequested.connect(lambda path: self._in_shell(
            lambda: self.show_in_explorer(path, True), f"File Explorer couldn't show {path}.", "", True))
        self.rg_runner = ReplayGainRunner(library.locks if library is not None else PathLocks(), self)
        self.rg_runner.lines.connect(self.rg_view.add_lines)
        self.rg_runner.progress.connect(self.rg_view.set_progress)
        self.rg_runner.progress.connect(self._rg_progress)
        self.rg_runner.finished.connect(self._rg_finished)
        self._rg_token: int | None = None
        self._rg_request: ToolRequest | None = None
        # The cue split (SPEC §22): its preview, report and job.
        self.split_preview = SplitPreview()
        self.split_preview.backRequested.connect(self._split_back)
        self.split_preview.doneRequested.connect(self.close_tools)
        self.split_preview.applyRequested.connect(self.apply_split)
        self.split_preview.cancelRequested.connect(lambda: self.split_runner.cancel())
        self.split_preview.encodingChanged.connect(self._split_encoding)
        self.split_runner = SplitRunner(library.locks if library is not None else PathLocks(), self)
        self.split_runner.planned.connect(self._split_planned)
        self.split_runner.replanned.connect(self._split_replanned)
        self.split_runner.progress.connect(self._split_progress)
        self.split_runner.finished.connect(self._split_finished)
        self._split_token: int | None = None
        self._split_replans: set[int] = set()
        self._split_request: ToolRequest | None = None
        self._split_job: int | None = None
        # The jobs panel (SPEC §10): this session's runs, in a bottom drawer; they go on while you browse.
        self.jobs = JobList(self)
        self.jobs_panel = JobsPanel(self.jobs)
        self.jobs_panel.openRequested.connect(self.show_job)
        self.jobs_panel.stopRequested.connect(self.stop_job)
        self.jobs_dock = QDockWidget("Jobs", self)
        self.jobs_dock.setObjectName("jobs")
        self.jobs_dock.setWidget(self.jobs_panel)
        self.jobs_dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        self.jobs_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.jobs_dock)
        self.jobs_dock.hide()
        self.jobs.changed.connect(self._update_jobs_button)
        self.jobs.removed.connect(self._update_jobs_button)
        self._rename_job: int | None = None  # the job id of the running rename (or Tidy's rename step)
        self._rg_job: int | None = None
        # Library search (SPEC §6): the query is typed in the toolbar, the results replace the file list.
        self.search_view = SearchView()
        self.search_view.resultActivated.connect(self.go_to_result)
        self.search_view.menuRequested.connect(lambda ids, pos: self.result_menu(ids).exec(pos))
        self.search_view.playRequested.connect(self.play_selection)
        self.search_view.artistRequested.connect(self.add_artist_filter)
        self.search_view.closeRequested.connect(self.close_search)
        self.search_service = (SearchService(library.db_path, self, prepare=search_sort_keys)
                               if library is not None else None)
        if self.search_service is not None:
            self.search_service.results.connect(self._search_results)
        self.search_limit = LIMIT  # lowered in tests
        self._search_token: int | None = None
        self.search_shown: int | None = None
        self._search_request: str | None = None  # the query running, and shown
        self._shown_request: tuple | None = None
        self._search_timer = QTimer(self, singleShot=True, interval=120)  # runs as you type, debounced
        self._search_timer.timeout.connect(self._run_search)
        self._search_refresh = QTimer(self, singleShot=True, interval=1000)  # the index changed: run it again
        self._search_refresh.timeout.connect(self._run_search)
        self.center = QStackedWidget()  # the file list, the search results, or the Tools page and its previews
        self.center.addWidget(self.file_view)
        self.center.addWidget(self.search_view)
        self.center.addWidget(self.tools_page)
        self.center.addWidget(self.rename_preview)
        self.center.addWidget(self.rg_view)
        self.center.addWidget(self.split_preview)
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
        self.splitter.setSizes([260, 640, 0, 340])
        self._selected_ids: list[int] = []
        self._restoring_selection = False
        self.setCentralWidget(self.splitter)
        self._build_navigation(conn)
        self._restore_state()
        self._restore_playlist()

        self.add_folder_action = QAction("&Add folder…", self, shortcut=QKeySequence("Ctrl+O"))
        self.add_folder_action.triggered.connect(self.add_folder)
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(self.add_folder_action)
        self.rescan_library_action = file_menu.addAction("&Rescan library", self.rescan_library)
        self.rescan_library_action.setEnabled(self.library is not None)
        file_menu.addSeparator()
        file_menu.addAction("E&xit", self.close)
        view_menu = self.menuBar().addMenu("&View")
        self.hide_empty_action = QAction("&Hide folders without music", self, checkable=True)
        self.hide_empty_action.setChecked(self.settings.hide_folders_without_music)
        self.hide_empty_action.toggled.connect(self.set_hide_folders_without_music)
        view_menu.addAction(self.hide_empty_action)
        self.jobs_action = self.jobs_dock.toggleViewAction()
        self.jobs_action.setText("&Jobs panel")
        self.jobs_action.setShortcut(QKeySequence("Ctrl+J"))
        view_menu.addAction(self.jobs_action)
        view_menu.addAction(self.playlist_action)
        lyrics_menu = self.menuBar().addMenu("&Lyrics")
        self.offline_action = QAction("&Offline mode (no LRCLIB lookups)", self, checkable=True)
        self.offline_action.setChecked(self.settings.lyrics_offline)
        self.offline_action.toggled.connect(self.set_lyrics_offline)
        self.auto_save_action = QAction("&Automatically save found lyrics into tags", self, checkable=True)
        self.auto_save_action.setToolTip("Only empty Lyrics tags are filled; existing lyrics are never replaced.")
        self.auto_save_action.setChecked(self.settings.lyrics_auto_save)
        self.auto_save_action.toggled.connect(self.set_lyrics_auto_save)
        lyrics_menu.addAction(self.offline_action)
        lyrics_menu.addAction(self.auto_save_action)
        lyrics_menu.addSeparator()
        # Library-wide lists for a lyrics action, as ready-made searches (SPEC §5: instead of smart views).
        for text, query in FIND_QUERIES:
            lyrics_menu.addAction(text, lambda q=query: self.find(q))
        lyrics_menu.setEnabled(library is not None)
        action_menu = self.menuBar().addMenu("&Action")  # actions over the whole library (SPEC §5)
        for text, query in ACTION_QUERIES:
            action_menu.addAction(text, lambda q=query: self.find(q))
        action_menu.setEnabled(library is not None)
        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction("&About MusicDesk", self.open_about)
        self.embedder = None
        if library is not None:
            self.embedder = AutoEmbedder(library.db_path, library.lyrics, self.tag_writer, self,
                                         enabled=self.settings.lyrics_auto_save)
            self.embedder.embedded.connect(
                lambda n: self.statusBar().showMessage(
                    f"Saved LRCLIB lyrics into the tags of {n} file{'s' if n != 1 else ''}.", STATUS_MS))

        self.overlay = DropOverlay(self)
        self.toast = Toast(self)
        self.setAcceptDrops(library is not None)
        self.sidebar.addFolderRequested.connect(self.add_folder)
        self.sidebar.removeRequested.connect(self.remove_root)
        self.sidebar.folderSelected.connect(self.open_folder)
        self.file_view.folderActivated.connect(self.open_folder)
        self.file_view.fileActivated.connect(lambda entry_id: self.open_files([entry_id]))
        self.file_view.menuRequested.connect(lambda ids, pos: self.list_menu(ids).exec(pos))
        self.sidebar.folder_menu_provider = lambda menu, entry_id: self.add_folder_actions(
            menu, entry_id, rename=lambda: self.sidebar.tree.start_rename(entry_id),
            delete=lambda: self._tree_delete(entry_id))
        self.file_view.renameRequested.connect(self.rename_entry)
        self.sidebar.renameRequested.connect(self.rename_entry)
        self.renameDone.connect(self._renamed)
        self.renameFailed.connect(lambda message: self.toast.show_message(message))
        self.deleteChecked.connect(self._delete_checked)
        self.deleteDone.connect(self._delete_done)
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
        self.shellFailed.connect(self._shell_failed)
        QApplication.instance().installEventFilter(self)  # the mouse back/forward buttons, anywhere in the window

        if library is None:
            self.add_folder_action.setEnabled(False)
            self.sidebar.setEnabled(False)
        else:
            library.rootsChanged.connect(self.refresh_roots)
            library.addFinished.connect(self.show_add_results)
            library.failed.connect(lambda message: self.statusBar().showMessage(message, STATUS_MS))
            library.notice.connect(lambda message: self.statusBar().showMessage(message, STATUS_MS))
            library.indexer.progress.connect(self.show_index_progress)
            library.indexer.idle.connect(self.index_label.hide)
            library.indexer.failed.connect(lambda message: self.statusBar().showMessage(message, STATUS_MS))
            library.indexer.folderUpdated.connect(self.folder_updated)
            library.indexer.scanFinished.connect(self.scan_finished)
            library.lyrics.foldersChanged.connect(self.lyrics_updated)
            library.lyrics.unreachable.connect(self.lrclib_unreachable)
            library.lyrics.progress.connect(self.show_lyrics_progress)
            library.lyrics.entryReady.connect(self._lyrics_entry)
            self.file_view.model.lrclib_status = library.lyrics.status_for
            self.search_view.model.lrclib_status = library.lyrics.status_for
            self.refresh_roots()
            self._reopen_last_folder()

    # -- rsgain --

    def show_rsgain_version(self, version: str | None) -> None:
        self.rsgain_version = version  # for the About dialog
        self.tools_page.set_rsgain_unavailable(
            None if version else "rsgain couldn't run, so ReplayGain is unavailable. See the log for details.")
        if version:
            self.rsgain_label.setText(version)
            self.rsgain_label.setToolTip("")
        else:
            self.rsgain_label.setText("rsgain unavailable: ReplayGain tools disabled")
            self.rsgain_label.setToolTip("The bundled rsgain.exe could not run. See the log for details.")

    def show_flac_version(self, version: str | None) -> None:
        """The startup self-check of the bundled flac (SPEC §13). The Split card is disabled with the reason when
        it failed; nothing else uses flac, so it has no status-bar label."""
        self.flac_version = version
        self.tools_page.set_flac_unavailable(
            None if version else "flac couldn't run, so the split is unavailable. See the log for details.")

    def open_about(self) -> None:
        """Help › About: versions and licences."""
        dialog = AboutDialog(self.rsgain_version, self, flac_version=self.flac_version)
        folder = str(licenses_dir())
        dialog.licensesRequested.connect(lambda: self._in_shell(
            lambda: self.show_in_explorer(folder, True), f"File Explorer couldn't open {folder}.", "", True))
        self.show_about(dialog)

    def show_about(self, dialog: AboutDialog) -> None:
        """Shows the dialog modally (replaced in tests, which never open a real dialog)."""
        dialog.exec()

    # -- browsing --

    def open_folder(self, entry_id: int, *, record: bool = True) -> None:
        """Show a folder at once from the index, then have it revalidated (stale-while-revalidate).
        `record`: a new step in the history (not for back, forward, or a replacement of a vanished folder)."""
        if not self._library_open() or entry_id == self.folder_id or not self._folder_exists(entry_id):
            return  # a row being removed can still be selected for a moment
        if not self.side_panel.can_leave():  # unsaved tag edits: Save, Discard or Cancel
            if self.folder_id is not None:
                self.sidebar.tree.select_entry(queries.folder_path_ids(self.library.conn, self.folder_id))
            return
        self.close_tools()  # browsing elsewhere goes back to the file list (the cards keep their options)
        if record and self.folder_id is not None:
            self.history_back.append(self.folder_id)
            self.history_forward.clear()
        self.folder_id = entry_id
        self.folder_path = str(path_of(self.library.conn, entry_id))
        self.file_view.set_folder(entry_id)
        crumbs = queries.folder_crumbs(self.library.conn, entry_id)
        self.address_bar.set_location(crumbs, self.folder_path)
        self.sidebar.tree.select_entry([entry for entry, _name in crumbs])
        self._show_selection([])
        self.library.folder_opened(entry_id)
        self.library.lyrics.prioritize_folder(entry_id)
        self._update_navigation()

    # -- history and navigation --

    def _build_navigation(self, conn) -> None:
        style = self.style()
        self.back_action = QAction(style.standardIcon(QStyle.StandardPixmap.SP_ArrowBack), "Back", self)
        # Explicit keys: Qt's standard Back also binds Backspace, which goes up here (SPEC §5), and a key bound
        # twice is ambiguous and fires nothing.
        self.back_action.setShortcut(QKeySequence("Alt+Left"))
        self.back_action.triggered.connect(self.go_back)
        self.forward_action = QAction(style.standardIcon(QStyle.StandardPixmap.SP_ArrowForward), "Forward", self)
        self.forward_action.setShortcut(QKeySequence("Alt+Right"))
        self.forward_action.triggered.connect(self.go_forward)
        self.up_action = QAction(style.standardIcon(QStyle.StandardPixmap.SP_ArrowUp), "Up", self)
        self.up_action.setShortcut(QKeySequence("Alt+Up"))
        self.up_action.triggered.connect(self.go_up)
        self.refresh_action = QAction(style.standardIcon(QStyle.StandardPixmap.SP_BrowserReload), "Rescan folder",
                                      self)
        self.refresh_action.setShortcut(QKeySequence(QKeySequence.StandardKey.Refresh))
        self.refresh_action.triggered.connect(self.rescan_folder)
        self.address_action = QAction("Edit address", self, shortcut=QKeySequence("Ctrl+L"))
        self.address_action.triggered.connect(lambda: self.address_bar.edit())
        self.addAction(self.address_action)

        self.address_bar = AddressBar(
            lambda entry_id: [(f.id, f.name) for f in queries.child_folders(
                conn(), entry_id, only_music=self.settings.hide_folders_without_music)] if conn() else [])
        self.address_bar.folderRequested.connect(self.open_folder)
        self.address_bar.pathEntered.connect(self.go_to_path)

        toolbar = QToolBar("Navigation")
        toolbar.setMovable(False)
        toolbar.setObjectName("navigation")
        for action in (self.back_action, self.forward_action, self.up_action, self.refresh_action):
            toolbar.addAction(action)
        toolbar.addWidget(self.address_bar)
        self.tools_action = QAction("Tools", self)
        self.tools_action.setToolTip("Rename from tags, ReplayGain and Tidy, for the selection or this folder")
        self.tools_action.triggered.connect(lambda: self.open_tools(self.file_view.selected_ids()))
        toolbar.addAction(self.tools_action)
        self.playlist_action = QAction("Playlist builder", self, checkable=True,
                                       shortcut=QKeySequence("Ctrl+Shift+P"))
        self.playlist_action.setToolTip("Show or hide the playlist builder (Ctrl+Shift+P)")
        self.playlist_action.toggled.connect(self.set_playlist_visible)
        toolbar.addAction(self.playlist_action)
        self.search_field = QLineEdit()
        self.search_field.setPlaceholderText("Search library (Ctrl+K)")
        self.search_field.setClearButtonEnabled(True)
        self.search_field.setMinimumWidth(180)
        self.search_field.setMaximumWidth(320)
        self.search_field.textEdited.connect(self._search_edited)
        self.search_field.returnPressed.connect(lambda: self.search_view.focus_results())
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self.search_field, activated=self.close_search,
                  context=Qt.ShortcutContext.WidgetShortcut)
        toolbar.addWidget(self.search_field)
        self.search_action = QAction("Search library", self, shortcut=QKeySequence("Ctrl+K"))
        self.search_action.triggered.connect(self.open_search)
        self.addAction(self.search_action)
        self.addToolBar(toolbar)

        # Backspace goes up, but only from the lists (it edits text everywhere else).
        for view in (self.file_view.view, self.sidebar.tree):
            QShortcut(QKeySequence(Qt.Key.Key_Backspace), view, activated=self.go_up,
                      context=Qt.ShortcutContext.WidgetShortcut)
        # Del sends the focused view's selection to the Recycle Bin. Shift+Del does the same: the app never
        # deletes permanently (SPEC §19). The playlist's own Del only removes tracks from the playlist.
        for view, selected in ((self.file_view.view, self.file_view.selected_ids),
                               (self.sidebar.tree, self.sidebar.tree.selected_ids),
                               (self.search_view.view, self.search_view.selected_ids)):
            for key in ("Del", "Shift+Del"):
                QShortcut(QKeySequence(key), view, context=Qt.ShortcutContext.WidgetShortcut,
                          activated=lambda view=view, selected=selected: self.delete_entries(selected(), view))
        self._update_navigation()

    def _update_navigation(self) -> None:
        open_ = self._library_open() and self.folder_id is not None
        self.back_action.setEnabled(bool(self.history_back))
        self.forward_action.setEnabled(bool(self.history_forward))
        self.up_action.setEnabled(open_ and queries.parent_folder(self.library.conn, self.folder_id) is not None)
        self.refresh_action.setEnabled(open_)

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
        child = self.folder_id
        parent = queries.parent_folder(self.library.conn, child)
        if parent is not None:
            self.open_folder(parent)
            self.file_view.select_ids([child])  # as Explorer does: the folder you came from is selected

    def refresh_after_tool(self, folder: Path) -> None:
        """A tool changed files under `folder`: the index catches up on the whole subtree first, even during a
        long background scan, and the list follows as each folder is served."""
        if self._library_open():
            self.library.indexer.request_path(str(folder), recursive=True)

    def rescan_folder(self) -> None:
        """F5: reconcile the open folder with the disk now."""
        if self._library_open() and self.folder_id is not None:
            self.library.indexer.request_folder(self.folder_id)  # first, even during a long background scan
            self.statusBar().showMessage("Rescanning this folder…", 2000)

    def go_to_path(self, text: str) -> None:
        """A typed or pasted path: open it if it is an indexed folder, or open a file's folder and select it."""
        if not self._library_open():
            return
        found = find_folder(self.library.conn, text)
        if found is None:
            self.statusBar().showMessage(f"{text} isn't in the library. Add its folder first (File > Add folder…).",
                                         STATUS_MS)
            return
        folder, exact = found
        if exact:
            self.open_folder(folder)
            return
        rest = Path(os.path.normpath(os.path.abspath(text))).relative_to(path_of(self.library.conn, folder)).parts
        file_id = queries.find_child(self.library.conn, folder, rest[0]) if len(rest) == 1 else None
        if file_id is not None:
            self.open_folder(folder)
            self.file_view.select_ids([file_id])
        else:
            self.statusBar().showMessage(f"{text} wasn't found in the library's index.", STATUS_MS)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress and isinstance(watched, QWidget) and \
                watched.window() is self:
            if event.button() == Qt.MouseButton.BackButton:
                self.go_back()
                return True
            if event.button() == Qt.MouseButton.ForwardButton:
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
            self.library.lyrics.prioritize_track(audio_ids[0])  # the selected track's lyrics first (SPEC §9)
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
        self._refresh_search_later()

    # -- library search (SPEC §6) --

    @property
    def showing_search(self) -> bool:
        return self.center.currentWidget() is self.search_view

    def open_search(self) -> None:
        """Ctrl+K: the search page, with the query field focused. The results stand in for the file list."""
        if not self._library_open():
            return
        if not self.showing_search:
            if not self.side_panel.can_leave():  # unsaved tag edits on the selection the search replaces
                return
            if self.rename_preview.state == "planning":
                self.rename_runner.cancel()
            self.center.setCurrentWidget(self.search_view)
            self.side_panel.tabs.setCurrentWidget(self.side_panel.lyrics)  # the page's detail pane: the lyrics
            self._show_selection(self.search_view.selected_ids())
            self._run_search()
        self.search_field.setFocus()
        self.search_field.selectAll()

    def find(self, query: str) -> None:
        """A ready-made search over the whole library (the Lyrics menu's Find entries, the Action menu)."""
        if not self._library_open():
            return
        self.open_search()
        if not self.showing_search:
            return  # unsaved tag edits, and Cancel
        self.search_field.setText(query)
        self._run_search()

    def close_search(self) -> None:
        """Esc: back to the folder that was open, as it was."""
        if not self.showing_search or not self.side_panel.can_leave():
            return
        self._leave_search()
        self.center.setCurrentWidget(self.file_view)
        self._show_selection(self.file_view.selected_ids())
        self.file_view.view.setFocus()

    def _leave_search(self) -> None:
        self._search_timer.stop()
        self._search_refresh.stop()
        self._search_token = None
        self._shown_request = None  # the next search starts afresh
        if self.search_service is not None:
            self.search_service.cancel()

    def _search_edited(self, _text: str) -> None:
        if not self.showing_search:
            self.open_search()
        self._search_timer.start()

    def _run_search(self) -> None:
        self._search_timer.stop()
        if not self._library_open() or not self.showing_search or self.search_service is None:
            return
        self._search_request = self.search_field.text()
        self._search_token = self.search_service.search(
            self.search_field.text(), error_keys=self.library.lyrics.error_keys(), limit=self.search_limit)

    def _refresh_search_later(self) -> None:
        if self.showing_search:
            self._search_refresh.start()

    def _search_results(self, results: SearchResults) -> None:
        if results.token != self._search_token or not self.showing_search:
            return
        self.search_shown = results.token  # the results on screen (tests wait for it)
        in_place = self._search_request == self._shown_request  # run again after the index changed
        self._shown_request = self._search_request
        if results.query.empty and not results.error:
            self.search_view.show_prompt()
        else:
            self.search_view.show_results(results.rows, results.total, results.query.errors, results.error,
                                          keys=results.keys, in_place=in_place)

    def add_artist_filter(self, artist: str) -> None:
        """Ctrl+click on an artist, or "Filter by": the page's artist checkboxes, as artist: filters (ORed)."""
        term = 'artist:"{}"'.format(artist.replace('"', ""))
        text = self.search_field.text()
        if term.casefold() not in text.casefold():
            self.search_field.setText(f"{text.rstrip()} {term}".strip())
            self._run_search()

    def go_to_result(self, entry_id: int) -> None:
        """Enter or a double-click on a result: its folder, with the file selected; a folder result opens."""
        row = self.search_view.row(entry_id)
        if row is None or not self._library_open():
            return
        folder_id, folder = (entry_id, str(Path(row.folder, row.name))) if row.is_dir else (row.folder_id, row.folder)
        if not self._folder_exists(folder_id):
            self.statusBar().showMessage(f"{folder} is no longer in the library.", STATUS_MS)
            return
        if not self.side_panel.can_leave():
            return
        self._leave_search()
        self.center.setCurrentWidget(self.file_view)
        if folder_id != self.folder_id:
            self.open_folder(folder_id)
        if not row.is_dir:
            self.file_view.select_ids([entry_id])
        self.file_view.view.setFocus()

    def result_menu(self, ids: list[int]) -> QMenu:
        menu = QMenu(self)
        if not self._library_open() or not ids:
            return menu
        go = menu.addAction("Go to folder", lambda: self.go_to_result(ids[0]))
        go.setEnabled(len(ids) == 1)
        open_action = menu.addAction("Open", lambda: self.open_files(ids))
        open_action.setEnabled(len(ids) == 1)
        menu.addAction("Play selection", lambda: self.play_selection(ids))
        menu.addSeparator()
        menu.addAction("Open in File Explorer", lambda: self.reveal(ids[0]))
        menu.addAction("Copy path" if len(ids) == 1 else "Copy paths", lambda: self.copy_paths(ids))
        menu.addSeparator()
        menu.addAction("Remove ReplayGain tags…", lambda: self.remove_replaygain(ids))
        menu.addSeparator()
        menu.addAction("Delete\tDel", lambda: self.delete_entries(ids, self.search_view.view))
        row = self.search_view.row(ids[0])
        if len(ids) == 1 and row is not None and row.artist:
            menu.addSeparator()
            menu.addAction(f"Filter by {row.artist}", lambda: self.add_artist_filter(row.artist))
        return menu

    # -- Tools (SPEC §10) --

    def tool_scope(self, ids: list[int]) -> ToolScope | None:
        """What the tools act on: one folder (from the list, the tree or the folder summary); else the audio
        files among the selected items in the open folder (a selection); with nothing selected, the open folder."""
        if not self._library_open():
            return None
        conn = self.library.conn
        kinds = {i: row[0] for i in ids
                 if (row := conn.execute("SELECT is_dir FROM entries WHERE id = ?", (i,)).fetchone())}
        if len(ids) == 1 and kinds.get(ids[0]):
            folder_id, files = ids[0], ()
        elif ids:
            rows = [self.file_view.model.rows[r] for i in ids if (r := self.file_view.model.row_for(i)) is not None]
            files = tuple(Path(self.folder_path, row.name) for row in rows if row.is_audio)
            if not files or self.folder_id is None:
                return None
            folder_id = self.folder_id
        elif self.folder_id is not None:
            folder_id, files = self.folder_id, ()
        else:
            return None
        if not self._folder_exists(folder_id):
            return None
        folder = path_of(conn, folder_id)
        first = files[0] if files else queries.folder_summary(conn, folder_id).first_track
        return ToolScope(folder, folder_id, files, Path(first) if first else None)

    def open_tools(self, ids: list[int]) -> None:
        scope = self.tool_scope(ids)
        if scope is None:
            self.statusBar().showMessage("Select a folder, or music files, to use the tools.", STATUS_MS)
            return
        self.tools_page.set_scope(scope)
        self._example_token = self.tag_io.read_rename_fields(scope.first_track) if scope.first_track else None
        self.center.setCurrentWidget(self.tools_page)

    def close_tools(self) -> None:
        """Back to the file list. A running job goes on (the jobs panel shows it and reopens its page)."""
        if self.center.currentWidget() is not self.file_view:
            if self.showing_search:
                self._leave_search()
            if self.rename_preview.state == "planning":
                self.rename_runner.cancel()
            if self.split_preview.state == "planning":
                self.split_runner.cancel()
            self.center.setCurrentWidget(self.file_view)
            self.file_view.view.setFocus()

    @property
    def showing_tools(self) -> bool:
        return self.center.currentWidget() not in (self.file_view, self.search_view)

    def _tool_example(self, token: int, fields: dict | None, extension: str) -> None:
        if token == self._example_token:
            self.tools_page.set_example(fields, extension)

    def run_tool(self, request: ToolRequest) -> None:
        """A card's button: the rename preview, the ReplayGain job (no preview), or Tidy's rename preview. A tool
        runs one job at a time: while its job runs, its page shows that job instead."""
        busy = None
        if request.tool == SPLIT and self._split_job is not None:
            busy = self._split_job
        elif request.tool in (RENAME, TIDY) and self._rename_job is not None:
            busy = self._rename_job
        elif request.tool in (REPLAYGAIN, TIDY) and self._rg_job is not None:
            busy = self._rg_job
        if busy is not None:
            job = self.jobs.get(busy)
            self.toast.show_message(f"{job.kind} is still running in {job.folder}. It can be followed in the jobs "
                                    "panel; start another run when it has finished.")
            self.show_job(busy)
            return
        if request.tool == RENAME:
            self.preview_rename(request)
        elif request.tool == REPLAYGAIN:
            self.run_replaygain(request)  # no preview: it only writes tags and the logs (Hugo, 2026-10-15)
        elif request.tool == TIDY:
            self.preview_rename(request)  # Tidy: the rename step is previewed; ReplayGain follows Apply
        elif request.tool == SPLIT:
            self.preview_split(request)

    def _unsaved_edits_under(self, folder: Path) -> bool:
        """Unsaved Metadata edits on a file a job is about to change: ask Save, Discard or Cancel first."""
        metadata = self.side_panel.metadata
        edited = [p for p in ([metadata.path] if metadata.path else []) + list(metadata.paths)
                  if Path(p).is_relative_to(folder)]
        return bool(edited) and not self.side_panel.can_leave()

    # -- the ReplayGain tool (SPEC §10 B) --

    def run_replaygain(self, request: ToolRequest | None = None, title: str | None = None) -> bool:
        """Start the ReplayGain job (the card's Run, or Tidy's second step). False when it didn't start."""
        request = request or self._rg_request
        if request is None or not self._library_open():
            return False
        folder = request.scope.folder
        if self._unsaved_edits_under(folder):
            return False
        try:
            self._rg_token = self.rg_runner.run(folder, request.replaygain_options, request.discography)
        except LockedError as e:
            self.toast.show_message(f"{e.path} is in use by {e.owner}. Try again when it has finished.")
            return False
        self._rg_request = request
        self._pause_indexing(True)  # rsgain gets the drive to itself; folders you open are still served
        if request.tool == TIDY and self._rename_job is not None:
            self._rg_job, self._rename_job = self._rename_job, None  # the same Tidy job, step 2
            self.jobs.update(self._rg_job, status="ReplayGain")
        else:
            self._rg_job = self._start_job(jobs_module.REPLAYGAIN, folder)
        follow = self.center.currentWidget() in (self.rg_view, self.rename_preview, self.tools_page)
        self.rg_view.show_running(folder, title, request.discography)
        if follow:  # Tidy's step 2 doesn't pull you back if you went browsing meanwhile
            self.center.setCurrentWidget(self.rg_view)
        return True

    def _rg_finished(self, finished: Finished) -> None:
        if finished.token != self._rg_token:
            return
        self._pause_indexing(False)
        if self._library_open():
            self.refresh_after_tool(finished.folder)  # new tags and replaygain.log files
        self.rg_view.show_result(finished)
        if self._rg_job is not None:
            self.jobs.report(self._rg_job, "replaygain", finished)
            state = jobs_module.FAILED if finished.error else jobs_module.STOPPED if finished.cancelled else \
                jobs_module.DONE
            self.jobs.finish(self._rg_job, state, short_result(finished))
            self._rg_job = None
        if not finished.error and not finished.nothing:
            albums = len(finished.album_logs)
            what = "stopped" if finished.cancelled else "done"
            self.statusBar().showMessage(f"ReplayGain {what}: {albums} album{'s' if albums != 1 else ''}.", STATUS_MS)

    def _pause_indexing(self, paused: bool) -> None:
        """Background indexing waits while a ReplayGain job runs. On a hard disk, the two reading at once made
        rsgain several times slower (Hugo, 2026-10-15: 66 s for an album that takes seconds alone)."""
        if not self._library_open() or paused == self._indexing_paused:
            return
        self._indexing_paused = paused
        if paused:
            self.library.indexer.pause()
        else:
            self.library.indexer.resume()
        if self.index_label.isVisible():
            text = self.index_label.text().removesuffix(PAUSED_NOTE)
            self.index_label.setText(text + PAUSED_NOTE if paused else text)

    def open_log(self, path: str) -> None:
        self._in_shell(lambda: self.launch(path), f"Windows couldn't open {os.path.basename(path)}.",
                       os.path.dirname(path), True)

    # -- the Rename tool (SPEC §10 A) --

    def preview_rename(self, request: ToolRequest) -> None:
        scope = request.scope
        self._rename_request = request
        self.rename_preview.show_planning(scope.folder, len(scope.files), tidy=request.tool == TIDY)
        self.center.setCurrentWidget(self.rename_preview)
        self._rename_token = self.rename_runner.plan(scope.folder, request.rename_options,
                                                     list(scope.files) or None)

    def _rename_planned(self, result: Planned) -> None:
        if result.token != self._rename_token or result.cancelled:
            return
        if result.plan is None:
            self.rename_preview.show_error(f"The preview failed: {result.error}")
        else:
            self.rename_preview.show_plan(result.plan)

    def ask_recycle(self, names: list[str]) -> str:
        """Junk files listed for the Recycle Bin: "recycle", "keep" (rename only) or "cancel"."""
        shown = "\n".join(names[:30]) + (f"\n… and {len(names) - 30} more" if len(names) > 30 else "")
        box = QMessageBox(QMessageBox.Icon.Question, "Send junk files to the Recycle Bin",
                          f"These {len(names)} file{'s' if len(names) != 1 else ''} will be sent to the Recycle "
                          f"Bin (you can restore them from there):\n\n{shown}", parent=self)
        recycle = box.addButton("Rename and recycle", QMessageBox.ButtonRole.AcceptRole)
        keep = box.addButton("Rename only", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return "recycle" if box.clickedButton() is recycle else "keep" if box.clickedButton() is keep else "cancel"

    def apply_rename(self) -> None:
        plan = self.rename_preview.plan
        if plan is None or not self._library_open():
            return
        if self._unsaved_edits_under(plan.folder):  # unsaved tag edits on a file about to be renamed
            return
        junk = [i.source.relative_to(plan.folder).as_posix() for i in plan.items if i.status is rename.Status.JUNK]
        if junk:
            answer = self.ask_recycle(junk)
            if answer == "cancel":
                return
            if answer == "keep":
                plan = dataclasses.replace(plan, items=[i for i in plan.items if i.status is not rename.Status.JUNK])
        try:
            self._rename_token = self.rename_runner.apply(plan)
        except LockedError as e:
            self.toast.show_message(f"{e.path} is in use by {e.owner}. Try again when it has finished.")
            return
        tidy = self._rename_request is not None and self._rename_request.tool == TIDY
        self._rename_job = self._start_job(jobs_module.TIDY if tidy else jobs_module.RENAME, plan.folder)
        self.jobs.update(self._rename_job, status="Renaming")
        self.rename_preview.show_applying()

    def _rename_applied(self, result: Applied) -> None:
        if result.token != self._rename_token:
            return
        if result.report is None:
            self.rename_preview.show_error(f"Renaming failed: {result.error}")
            if self._rename_job is not None:
                self.jobs.finish(self._rename_job, jobs_module.FAILED, f"Renaming failed: {result.error}")
                self._rename_job = None
            return
        report = result.report
        job = self._rename_job
        if job is not None:
            self.jobs.report(job, "rename", report)
        if self._library_open():
            by_folder: dict[Path, list] = {}
            for step in report.steps:  # the index applies renames one folder at a time
                by_folder.setdefault(Path(step[0]).parent, []).append(step)
            for steps in by_folder.values():
                self.library.indexer.apply_renames(steps)  # in place: ids, tags and selection kept
            if self._rename_request is not None:  # then the whole scope: recycled junk, sidecars, anything else
                self.refresh_after_tool(self._rename_request.scope.folder)
        self.rename_preview.show_report(report)
        count = len(report.renamed)
        self.statusBar().showMessage(f"Renamed {count} file{'s' if count != 1 else ''}.", STATUS_MS)
        request = self._rename_request
        summary = rename.summary_sections(report, dry_run=False)[0][0]
        if request is not None and request.tool == TIDY:  # Tidy, step 2: ReplayGain on the final names
            if report.cancelled:
                self.rename_preview.message.setText("Stopped, so ReplayGain was not run.")
            elif self.run_replaygain(request, "Tidy, step 2 of 2: ReplayGain"):
                return  # the job goes on (its rename report is kept)
            else:
                self.rename_preview.message.setText("Renamed. ReplayGain was not started (see the message).")
        if job is not None:
            self.jobs.finish(job, jobs_module.STOPPED if report.cancelled else jobs_module.DONE, summary)
            self._rename_job = None

    # -- the cue split (SPEC §22) --

    def split_album(self, path: Path) -> None:
        """"Split into tracks…" on a .cue (that sheet) or an album file (the sheets beside it that cut it), with
        the Split card's saved options."""
        if not self._library_open():
            return
        card = self.tools_page.split_card
        if not card.names.valid or card.unavailable:
            self.toast.show_message(card.unavailable or "Fix the file names first (Tools › File names).")
            return
        found = find_folder(self.library.conn, str(path.parent))
        if found is None or not found[1]:
            return
        scope = ToolScope(path.parent, found[0])
        if path.suffix.lower() == ".cue":
            request = self.tools_page.split_request(scope, cues=(path,))
        else:
            sheets = tuple(path.parent / n for n in self._names_in(found[0]) if n.lower().endswith(".cue"))
            request = self.tools_page.split_request(scope, cues=sheets, source=path)
        self.tools_page.set_scope(scope)
        self.run_tool(request)

    def _split_target(self, ids: list[int]) -> Path | None:
        """The file "Split into tracks…" applies to: one selected .cue, or a single-file album's audio file."""
        if len(ids) != 1 or self.folder_id is None or self.folder_path is None:
            return None
        row = self.file_view.model.row_for(ids[0])
        if row is None or self.file_view.model.rows[row].is_dir:
            return None
        name = self.file_view.model.rows[row].name
        if name.lower().endswith(".cue") or self.is_album_file(self.folder_id, name):
            return Path(self.folder_path, name)
        return None

    def _names_in(self, folder_id: int) -> list[str]:
        return [name for (name,) in self.library.conn.execute(
            "SELECT name FROM entries WHERE parent_id = ? AND is_dir = 0", (folder_id,))]

    def is_album_file(self, folder_id: int, name: str) -> bool:
        """An audio file that looks like a single-file album's, from the index only (no file is read on the UI
        thread): a .cue beside it with the same stem, or the folder's only audio file next to a .cue. The preview
        then reads the sheets and says if it isn't one."""
        names = self._names_in(folder_id)
        cues = {Path(n).stem.casefold() for n in names if n.lower().endswith(".cue")}
        if not cues:
            return False
        audio = [n for n in names if Path(n).suffix.lower() in cue.SUPPORTED]
        return Path(name).stem.casefold() in cues or audio == [name]

    def preview_split(self, request: ToolRequest) -> None:
        self._split_request = request
        options = request.split_options
        self.split_preview.show_planning(request.scope.folder, options)
        self.center.setCurrentWidget(self.split_preview)
        name_masks, replacement = self._name_masks()
        self._split_token = self.split_runner.plan(
            request.scope.folder, options.recursive, name_masks, replacement,
            cues=list(request.cues) if request.cues is not None else None, source=request.source)

    def _split_planned(self, result: SplitPlanned) -> None:
        if result.token != self._split_token or result.cancelled:
            return
        if result.error:
            self.split_preview.show_error(f"The preview failed: {result.error}")
        else:
            self.split_preview.show_plans(result.plans)

    def _split_encoding(self, path: Path, codec) -> None:
        name_masks, replacement = self._name_masks()
        self._split_replans.add(self.split_runner.replan(path, codec, name_masks, replacement))

    def _split_replanned(self, result: Replanned) -> None:
        if result.token in self._split_replans:
            self._split_replans.discard(result.token)
            if self.split_preview.state == "preview":
                self.split_preview.replace_plan(result.plan)

    def apply_split(self) -> None:
        request = self._split_request
        if request is None or not self.split_preview.ready() or not self._library_open():
            return
        folder = request.scope.folder
        if self._unsaved_edits_under(folder):
            return
        try:
            self._split_token = self.split_runner.run(folder, self.split_preview.plans, request.split_options,
                                                      request.replaygain_options)
        except LockedError as e:
            self.toast.show_message(f"{e.path} is in use by {e.owner}. Try again when it has finished.")
            return
        self._split_job = self._start_job(jobs_module.SPLIT, folder)
        self._pause_indexing(True)  # flac reads the whole album: the drive is the split's meanwhile
        self.split_preview.show_running()

    def _split_progress(self, done: int, total: int, label: str) -> None:
        self.split_preview.set_progress(done, total, label)
        if self._split_job is not None and label != "done":
            self.jobs.update(self._split_job, status=f"Running ({done + 1} of {total}): {label}",
                             progress=(done, total))

    def _split_finished(self, finished: SplitFinished) -> None:
        if finished.token != self._split_token:
            return
        self._pause_indexing(False)
        if self._library_open():
            self.refresh_after_tool(finished.folder)  # new tracks, recycled originals
        self.split_preview.show_result(finished)
        if self._split_job is not None:
            self.jobs.report(self._split_job, "split", finished)
            state = (jobs_module.FAILED if finished.error else jobs_module.STOPPED if finished.cancelled
                     else jobs_module.DONE)
            self.jobs.finish(self._split_job, state, split_short_result(finished))
            self._split_job = None
        count = sum(a.split for a in finished.albums)
        self.statusBar().showMessage(f"Split {count} album{'s' if count != 1 else ''}.", STATUS_MS)

    def _split_back(self) -> None:
        if self.split_preview.state == "planning":
            self.split_runner.cancel()
        self.center.setCurrentWidget(self.tools_page)

    # -- the jobs panel --

    def _start_job(self, kind: str, folder: Path) -> int:
        job = self.jobs.add(kind, folder)
        self.jobs_dock.show()
        self.jobs_panel.select(job.id)
        return job.id

    def _update_jobs_button(self, _job_id: int = 0) -> None:
        running = len(self.jobs.running)
        self.jobs_button.setText(f"Jobs: {running} running" if running else "Jobs")
        self.jobs_button.setVisible(bool(self.jobs.jobs))

    def show_job(self, job_id: int) -> None:
        """A job's page: live while it runs, its report once it has finished."""
        job = self.jobs.get(job_id)
        if job is None:
            return
        if job_id == self._split_job:
            self.center.setCurrentWidget(self.split_preview)
        elif "split" in job.reports:
            self.split_preview.show_result(job.reports["split"])
            self.center.setCurrentWidget(self.split_preview)
        elif job_id == self._rename_job:
            self.center.setCurrentWidget(self.rename_preview)
        elif job_id == self._rg_job:
            self.center.setCurrentWidget(self.rg_view)
        elif "replaygain" in job.reports:
            self.rg_view.show_result(job.reports["replaygain"])
            self.rg_view.heading.setText(f"<b>{job.kind}</b> · {job.folder}")
            self.center.setCurrentWidget(self.rg_view)
        elif "rename" in job.reports:
            self.rename_preview.heading.setText(f"<b>{job.kind}</b> · {job.folder}")
            self.rename_preview.show_report(job.reports["rename"])
            self.center.setCurrentWidget(self.rename_preview)
        else:
            self.toast.show_message(f"{job.kind} in {job.folder}: {job.status}")

    def stop_job(self, job_id: int) -> None:
        if job_id == self._split_job:
            self.split_runner.cancel()
        elif job_id == self._rename_job:
            self.rename_runner.cancel()
        elif job_id == self._rg_job:
            self.rg_runner.cancel()

    def _rg_progress(self, done: int, total: int, label: str) -> None:
        if self._rg_job is not None and label != "done":
            self.jobs.update(self._rg_job, status=label, progress=(done, total))

    def confirm_quit_with_jobs(self, names: list[str]) -> bool:
        answer = QMessageBox.question(
            self, "A job is running",
            "Still running:\n" + "\n".join(names) + "\n\nStop it and quit? A stopped rename leaves the files "
            "after that point as they were; a stopped ReplayGain album is reported as cancelled; a stopped split "
            "deletes the tracks it was writing and leaves its album as it was.")
        return answer == QMessageBox.StandardButton.Yes

    def _rename_back(self) -> None:
        if self.rename_preview.state == "planning":
            self.rename_runner.cancel()
        self.center.setCurrentWidget(self.tools_page)

    def _rename_done(self) -> None:
        self.close_tools()

    def set_lyrics_offline(self, offline: bool) -> None:
        self.settings.lyrics_offline = offline
        if self._library_open():
            self.library.lyrics.set_offline(offline)

    def set_lyrics_auto_save(self, enabled: bool) -> None:
        self.settings.lyrics_auto_save = enabled
        if self.embedder is not None:
            self.embedder.set_enabled(enabled)

    def lyrics_updated(self, folder_ids: list[int]) -> None:
        """LRCLIB results: the open folder's LRCLIB column follows, and the search results (their text too)."""
        if not self._library_open():
            return
        self._refresh_search_later()
        if self.folder_id not in folder_ids:
            return
        self.file_view.refresh()
        self.side_panel.refresh_folder()

    def show_lyrics_progress(self, progress: LyricsProgress) -> None:
        """The status bar's lyrics label and its button: Pause, Resume or Retry now (SPEC §9 Controls)."""
        if not self._library_open():
            return
        if progress.state != self._lyrics_state:  # offline on or off: the LRCLIB column's pending rows change
            self._lyrics_state = progress.state
            self.file_view.model.refresh_column("lrclib")
            self.search_view.model.refresh_column("lrclib")
        left = f"{progress.pending:,} to look up" if progress.pending is not None else "starting"
        button = None
        if progress.state == OFFLINE:
            text = "Lyrics: offline mode"
        elif progress.state == PAUSED:
            text, button = f"Lyrics paused: {left}", "Resume"
        elif progress.state == UNREACHABLE:
            resume = time.strftime("%H:%M", time.localtime(self.library.lyrics.resume_at))
            text, button = f"LRCLIB unreachable, retrying at {resume}", "Retry now"
        elif progress.state == RUNNING:
            text, button = f"Looking up lyrics: {left}", "Pause"
        elif progress.pending:
            text = f"Lyrics: {progress.pending:,} to retry later"
        else:
            text = ""
        self.lyrics_label.setText(text)
        self.lyrics_label.setVisible(bool(text))
        self.lyrics_button.setText(button or "")
        self.lyrics_button.setVisible(button is not None)

    def _lyrics_button_clicked(self) -> None:
        if not self._library_open():
            return
        lyrics = self.library.lyrics
        action = {"Pause": lyrics.pause, "Resume": lyrics.resume, "Retry now": lyrics.retry_now}.get(
            self.lyrics_button.text())
        if action:
            action()

    def _lyrics_entry(self, _key: str, entry: dict) -> None:
        if entry.get("status") == "error" and self._library_open():
            self.file_view.model.refresh_column("lrclib")  # the failed key's rows show "error"
            self.search_view.model.refresh_column("lrclib")

    def lrclib_unreachable(self, resume_at: float) -> None:
        minutes = max(1, round((resume_at - time.time()) / 60))
        self.toast.show_message(f"LRCLIB unreachable, retrying in {minutes} min.", "Retry now",
                                self.library.lyrics.retry_now)

    def _refresh_location(self) -> None:
        """The open folder (or a folder above it) may have been renamed: its path and breadcrumb follow."""
        if self.folder_id is None or not self._folder_exists(self.folder_id):
            return
        path = str(path_of(self.library.conn, self.folder_id))
        if path != self.folder_path:
            self.folder_path = path
            self.address_bar.set_location(queries.folder_crumbs(self.library.conn, self.folder_id), path)

    def scan_finished(self, _report: ScanReport) -> None:
        if not self._library_open():
            return
        self._refresh_playlist_later()
        self._keep_open_folder()
        self.sidebar.model.refresh_loaded()
        if self.folder_id is not None:
            self.file_view.refresh()
        self.side_panel.refresh_folder()
        self._refresh_search_later()

    def _keep_open_folder(self) -> None:
        """If the open folder left the index (deleted or moved outside the app), open its nearest surviving
        ancestor, as Explorer does. Done before the tree drops the row, which would select a neighbour."""
        if self.folder_id is None or self._folder_exists(self.folder_id):
            return
        found = find_folder(self.library.conn, self.folder_path) if self.folder_path else None
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

    def _folder_exists(self, entry_id: int) -> bool:
        return self.library.conn.execute("SELECT 1 FROM entries WHERE id = ?", (entry_id,)).fetchone() is not None

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
        found = find_folder(self.library.conn, self.settings.last_folder)
        if found and found[1]:
            self.open_folder(found[0])

    # -- the playlist builder (SPEC §15) --

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

        self._in_shell(run, f"The playlist couldn't be saved as {target}.", str(target.parent), True)

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
        self.settings.window_geometry = base64.b64encode(bytes(self.saveGeometry())).decode("ascii")
        self.settings.splitter_state = base64.b64encode(bytes(self.splitter.saveState())).decode("ascii")
        self.settings.file_list_header = self.file_view.save_layout()
        self.settings.expanded_folders = self.sidebar.tree.expanded_paths()
        self.settings.last_folder = ""
        if self._library_open() and self.folder_id is not None and self._folder_exists(self.folder_id):
            self.settings.last_folder = str(path_of(self.library.conn, self.folder_id))
        self.settings.save()

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.side_panel.can_leave():
            event.ignore()
            return
        running = [f"{job.kind}: {job.folder}" for job in self.jobs.running]
        if running and not self.confirm_quit_with_jobs(running):
            event.ignore()
            return
        if self.embedder is not None:
            self.embedder.close()  # before the writer: it may still hand it a batch
        self.rename_runner.close()  # a running rename stops between two files
        self.rg_runner.close()  # a running rsgain is killed; its album is reported as cancelled
        self.split_runner.close()  # a running flac is killed; that album's .partial files are deleted
        self.tag_writer.close()  # the queued saves finish first
        self.tag_io.close()
        self._playlist_save.stop()
        self.save_playlist()
        if self.search_service is not None:
            self.search_service.close()
        self.save_state()
        QApplication.instance().removeEventFilter(self)
        self._shell.shutdown(wait=False)
        super().closeEvent(event)

    # -- context menus (SPEC §5): only what exists so far; Rename, Tidy and ReplayGain come with their milestones

    def list_menu(self, ids: list[int]) -> QMenu:
        """The file list's menu: for the selection, or for the open folder when the click was on the background."""
        menu = QMenu(self)
        if not self._library_open():
            return menu
        if not ids:
            if self.folder_id is not None:
                self.add_folder_actions(menu, self.folder_id, include_open=False)
            return menu
        if len(ids) == 1 and self.file_view.is_dir(ids[0]):
            self.add_folder_actions(menu, ids[0], rename=lambda: self.file_view.start_rename(ids[0]),
                                    delete=lambda: self.delete_entries(ids, self.file_view.view))
            return menu
        open_action = menu.addAction("Open", lambda: self.open_files(ids))
        open_action.setEnabled(len(ids) == 1)
        play = menu.addAction("Play selection", lambda: self.play_selection(ids))
        play.setEnabled(bool(queries.playlist_for_ids(self.library.conn, ids)))
        menu.addSeparator()
        menu.addAction("Open in File Explorer", lambda: self.reveal(ids[0]))
        menu.addAction("Copy path" if len(ids) == 1 else "Copy paths", lambda: self.copy_paths(ids))
        menu.addSeparator()
        if len(ids) == 1:
            menu.addAction("Rename\tF2", lambda: self.file_view.start_rename(ids[0]))
        album = self._split_target(ids)
        if album is not None:
            menu.addAction("Split into tracks…", lambda: self.split_album(album))
        tools = menu.addAction("Tools…", lambda: self.open_tools(ids))
        any_audio = any(self.file_view.model.rows[r].is_audio for i in ids
                        if (r := self.file_view.model.row_for(i)) is not None)
        tools.setEnabled(any_audio)
        remove = menu.addAction("Remove ReplayGain tags…", lambda: self.remove_replaygain(ids))
        remove.setEnabled(any_audio)
        menu.addSeparator()
        menu.addAction("Delete\tDel", lambda: self.delete_entries(ids, self.file_view.view))
        return menu

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
        if rename is not None and queries.parent_folder(self.library.conn, entry_id) is not None:
            menu.addAction("Rename\tF2", rename)  # not a library root
        menu.addAction("Rescan", lambda: self.library.indexer.request_folder(entry_id))
        menu.addSeparator()
        menu.addAction("Tools…", lambda: self.open_tools([entry_id]))
        menu.addAction("Remove ReplayGain tags…", lambda: self.remove_replaygain([entry_id]))
        if delete is not None and queries.parent_folder(self.library.conn, entry_id) is not None:
            menu.addSeparator()
            menu.addAction("Delete\tDel", delete)  # never a library root (Remove from library is below)

    def _tree_delete(self, entry_id: int) -> None:
        """Delete from the tree's menu: its selection if the clicked folder is in it, else that folder."""
        selected = self.sidebar.tree.selected_ids()
        self.delete_entries(selected if entry_id in selected else [entry_id], self.sidebar.tree)

    # -- Delete to the Recycle Bin (SPEC §19) --

    def delete_entries(self, ids: list[int], view: QWidget | None = None) -> None:
        """Del, Shift+Del or Delete in a menu: the items go to the Recycle Bin after a confirmation, and nowhere
        else. Refused, with nothing touched: a library root, an item a job or a save holds, and a place where
        Windows can't recycle (checked off the UI thread, before asking)."""
        if not self._library_open() or not ids:
            return
        conn = self.library.conn
        items: list[DeleteItem] = []
        for entry_id in dict.fromkeys(ids):
            row = conn.execute("SELECT is_dir FROM entries WHERE id = ?", (entry_id,)).fetchone()
            if row is None:
                continue
            path = path_of(conn, entry_id)
            if queries.parent_folder(conn, entry_id) is None:
                self.toast.show_message(f"{path} is a library folder. Use Remove from library to take it out of "
                                        "the library.")
                return
            items.append(DeleteItem(entry_id, path, bool(row[0])))
        # An item inside a selected folder goes with it (the tree can select a folder and its subfolder).
        items = [i for i in items if not any(o.is_dir and o is not i and i.path.is_relative_to(o.path) for o in items)]
        if not items:
            return
        try:
            lock = self.library.locks.acquire([item.path for item in items], DELETE_OWNER)
        except LockedError as e:
            self.toast.show_message(f"{Path(e.path).name} is in use by {e.owner}. Try again when it has finished.")
            return
        request = DeleteRequest(items, lock, view, self._row_after(ids, view))
        check = self.recycle_check

        def run() -> None:
            reasons = []
            for item in items:
                try:
                    reason = check(item.path)
                except OSError as e:
                    reason = f"Windows couldn't check {item.path.name} ({e})"
                if reason is not None:
                    reasons.append((str(item.path), reason))
            self.deleteChecked.emit(request, reasons)

        self._shell.submit(run)

    def _row_after(self, ids: list[int], view: QWidget | None) -> int | None:
        """The row to select once these are gone: the first one after them, else the last one before (Explorer)."""
        if view is self.file_view.view:
            shown = self.file_view.visible_ids()
        elif view is self.search_view.view:
            shown = self.search_view.visible_ids()
        else:
            return None  # the tree: the open folder's own rule applies (SPEC §5)
        chosen = set(ids)
        places = [n for n, entry_id in enumerate(shown) if entry_id in chosen]
        if not places:
            return None
        after = [i for i in shown[places[-1] + 1:] if i not in chosen]
        before = [i for i in shown[:places[0]] if i not in chosen]
        return after[0] if after else before[-1] if before else None

    def _delete_checked(self, request: DeleteRequest, reasons: list[tuple[str, str]]) -> None:
        if not self._library_open():
            return
        if reasons:
            self.library.locks.release(request.lock)
            path, reason = reasons[0]
            is_dir = any(str(item.path) == path and item.is_dir for item in request.items)
            self.toast.show_message(
                f"MusicDesk only deletes to the Recycle Bin, and {reason}, so nothing was deleted. You can delete it "
                "in File Explorer.", "Open in File Explorer",
                lambda: self._shell.submit(lambda: self.show_in_explorer(path, is_dir)))
            return
        unsaved = self._unsaved_edits_in(request.items)
        if not self.ask_delete(request.items, unsaved):
            self.library.locks.release(request.lock)
            return
        if unsaved:
            self.side_panel.metadata.discard_edits()  # the question said they would be lost
        recycle = self.recycle

        def run() -> None:
            sent, failed = [], []
            for item in request.items:  # one at a time, so a failure names its file and the others still go
                try:
                    recycle(item.path)
                except OSError as e:  # CannotRecycle too: never deleted for good instead
                    failed.append((item.path.name, e.strerror or str(e)))
                else:
                    sent.append(item)
            self.deleteDone.emit(request, sent, failed)

        self._shell.submit(run)

    def _unsaved_edits_in(self, items: list[DeleteItem]) -> bool:
        metadata = self.side_panel.metadata
        if not metadata.dirty:
            return False
        edited = [metadata.path] if metadata.path is not None else metadata.paths
        return any(path == item.path or (item.is_dir and path.is_relative_to(item.path))
                   for path in edited for item in items)

    def delete_question(self, items: list[DeleteItem], unsaved: bool) -> str:
        """The confirmation's text (SPEC §19). A folder's file count comes from the index."""
        if len(items) == 1 and not items[0].is_dir:
            text = f"Send '{items[0].path.name}' to the Recycle Bin?"
        elif len(items) == 1:
            count = queries.file_count(self.library.conn, items[0].entry_id)
            text = (f"Send the folder '{items[0].path.name}' and everything in it ({count} file"
                    f"{'' if count == 1 else 's'}) to the Recycle Bin?")
        else:
            names = [item.path.name for item in items[:DELETE_NAMES]]
            if len(items) > DELETE_NAMES:
                names.append(f"and {len(items) - DELETE_NAMES} more")
            text = f"Send these {len(items)} items to the Recycle Bin?\n\n" + "\n".join(names)
        if unsaved:
            text += "\n\nUnsaved tag edits on it will be lost."
        return text

    def ask_delete(self, items: list[DeleteItem], unsaved: bool) -> bool:
        """Confirm a delete (replaced in tests). Cancel is the default, and Esc."""
        box = QMessageBox(QMessageBox.Icon.Question, "Delete", self.delete_question(items, unsaved), parent=self)
        send = box.addButton("Send to Recycle Bin", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        return box.clickedButton() is send

    def _delete_done(self, request: DeleteRequest, sent: list[DeleteItem], failed: list[tuple[str, str]]) -> None:
        if not self._library_open():
            return
        self.library.locks.release(request.lock)
        for parent in dict.fromkeys(item.path.parent for item in sent):
            self.library.indexer.request_path(str(parent))  # at once; the watcher would see it too, but later
        if sent:
            what = f"'{sent[0].path.name}'" if len(sent) == 1 else f"{len(sent)} items"
            self.statusBar().showMessage(f"Sent {what} to the Recycle Bin.", STATUS_MS)
        if failed:
            shown = "; ".join(f"{name}: {why}" for name, why in failed[:5])
            more = f" (and {len(failed) - 5} more)" if len(failed) > 5 else ""
            self.toast.show_message(f"Not sent to the Recycle Bin: {shown}{more}", timeout_ms=15_000)
        if request.next_id is not None and sent:
            if request.view is self.file_view.view and not self.showing_search:
                self.file_view.select_ids([request.next_id])
            elif request.view is self.search_view.view and self.showing_search:
                self.search_view.select_ids([request.next_id])

    # -- removing ReplayGain (SPEC §10 B) --

    def remove_replaygain(self, ids: list[int]) -> None:
        """The ReplayGain tags of the tracks among `ids`, folders included with their subfolders: the
        REPLAYGAIN_* values and the comment lines the ReplayGain step writes. Asks first; runs on the TagWriter."""
        if not self._library_open():
            return
        conn = self.library.conn
        track_ids = queries.track_ids_for(conn, ids)
        rows = queries.track_rows(conn, track_ids)
        paths = [str(rows[i].path) for i in track_ids if i in rows]
        if not paths:
            self.statusBar().showMessage("There are no audio files here.", STATUS_MS)
            return
        folders = [i for i in ids if (r := conn.execute("SELECT is_dir FROM entries WHERE id = ?",
                                                         (i,)).fetchone()) and r[0]]
        if len(ids) == 1 and folders:
            where = f"{path_of(conn, folders[0])} and its subfolders"
        else:
            where = "the selection"
        options = self.tools_page.replaygain_card.options()
        if not self.ask_remove_replaygain(len(paths), where, options.comment):
            return
        self._rg_remove_token = self.tag_writer.remove_replaygain(paths, options)
        count = len(paths)
        self.statusBar().showMessage(f"Removing the ReplayGain tags of {count} file{'s' if count != 1 else ''}…",
                                     STATUS_MS)

    def ask_remove_replaygain(self, count: int, where: str, comment: str) -> bool:
        """Confirm the removal (replaced in tests)."""
        noun = f"{count} track{'s' if count != 1 else ''}"
        box = QMessageBox(QMessageBox.Icon.Question, "Remove ReplayGain tags",
                          f"Remove the ReplayGain tags from {noun} in {where}?\n\n"
                          f"This deletes their track and album gain and peak values, and the comment line "
                          f"\"{comment}\" that ReplayGain writes. Other tags, other comment lines and the audio "
                          f"itself are not changed, and replaygain.log files stay.", parent=self)
        remove = box.addButton("Remove", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return box.clickedButton() is remove

    def _replaygain_removed(self, result) -> None:
        if result.token != self._rg_remove_token:
            return
        done = len(result.removed)
        if done:
            text = f"Removed the ReplayGain tags of {done} file{'s' if done != 1 else ''}."
        else:
            text = "There were no ReplayGain tags to remove."
        if result.unchanged and done:
            text += f" {result.unchanged} had none."
        self.statusBar().showMessage(text, STATUS_MS)
        if result.failed:
            first = result.failed[0][1]
            more = f" (and {len(result.failed) - 1} more)" if len(result.failed) > 1 else ""
            self.toast.show_message(f"Not changed: {first}{more}")

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
        found = find_folder(self.library.conn, os.path.dirname(path))
        entry_id = queries.find_child(self.library.conn, found[0], os.path.basename(path)) \
            if found and found[1] else None
        if entry_id is not None:
            self.rename_entry(entry_id, new_name)

    def rename_entry(self, entry_id: int, new_name: str) -> None:
        """Rename a file or folder on disk (off the UI thread), then update the index in place."""
        if not self._library_open() or not self._folder_exists(entry_id):
            return
        conn = self.library.conn
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
        locks = self.library.locks

        def run() -> None:
            try:
                result = inline_rename.rename_entry(path, new_name, locks)
            except inline_rename.RenameError as e:
                self.renameFailed.emit(str(e))
                return
            self.renameDone.emit(result, False)

        self._shell.submit(run)

    def _renamed(self, result: inline_rename.RenameResult, was_undo: bool) -> None:
        if not result.steps:
            return
        if self._library_open():
            self.library.indexer.apply_renames(result.steps)
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
        locks = self.library.locks if self._library_open() else None

        def undo() -> None:
            def run() -> None:
                try:
                    back = inline_rename.undo(result, locks)
                except inline_rename.RenameError as e:
                    self.renameFailed.emit(str(e))
                    return
                self.renameDone.emit(back, True)
            self._shell.submit(run)

        self.toast.show_message(text, "Undo", undo, timeout_ms=10_000)

    # -- actions handed to Windows --

    def _paths(self, ids: list[int]) -> list[str]:
        return [str(path_of(self.library.conn, entry_id)) for entry_id in ids if self._folder_exists(entry_id)]

    def open_files(self, ids: list[int]) -> None:
        """Open with the default app, as a double-click in Explorer does."""
        if not self._library_open():
            return
        for path in self._paths(ids):
            self._in_shell(lambda p=path: self.launch(p),
                           f"Windows couldn't open {os.path.basename(path)}: no app is set for this file type, "
                           "or the file isn't reachable.", path, False)

    def open_url(self, url: str) -> None:
        """A web search from the Lyrics tab, in the default browser."""
        self._in_shell(lambda: self.launch(url), "Windows couldn't open the web browser.", "", False)

    def reveal(self, entry_id: int) -> None:
        if not self._library_open() or not self._folder_exists(entry_id):
            return
        path = str(path_of(self.library.conn, entry_id))
        is_dir = bool(self.library.conn.execute("SELECT is_dir FROM entries WHERE id = ?", (entry_id,)).fetchone()[0])
        self._in_shell(lambda: self.show_in_explorer(path, is_dir), f"File Explorer couldn't show {path}.", "", True)

    def copy_paths(self, ids: list[int]) -> None:
        paths = self._paths(ids) if self._library_open() else []
        if paths:
            QApplication.clipboard().setText("\n".join(paths))
            self.statusBar().showMessage("Path copied." if len(paths) == 1 else f"{len(paths)} paths copied.",
                                         STATUS_MS)

    def play_folder(self, entry_id: int, recursive: bool) -> None:
        if not self._library_open() or not self._folder_exists(entry_id):
            return
        entries = queries.playlist_for_folder(self.library.conn, entry_id, recursive)
        self._play(entries, str(path_of(self.library.conn, entry_id)), True)

    def play_selection(self, ids: list[int]) -> None:
        if not self._library_open():
            return
        entries = queries.playlist_for_ids(self.library.conn, ids)
        self._play(entries, str(entries[0].path) if entries else "", False)

    def _play(self, entries: list[playlists.PlaylistEntry], fallback: str, fallback_is_dir: bool) -> None:
        """Write a temporary playlist (the app's own file) and hand it to the default player (SPEC §14)."""
        if not entries:
            self.statusBar().showMessage("There are no audio files to play here.", STATUS_MS)
            return

        def run() -> None:
            playlist = playlists.write_temp_playlist(entries, self.playlist_dir)
            self.launch(playlist)

        self._in_shell(run, "No app is set to play .m3u8 playlists. To choose one, open Windows Settings > Apps > "
                       "Default apps and search for .m3u8.", fallback, fallback_is_dir)
        self.statusBar().showMessage(f"Playing {len(entries)} track{'s' if len(entries) > 1 else ''}…", STATUS_MS)

    def _in_shell(self, fn, message: str, fallback: str, fallback_is_dir: bool) -> None:
        def run() -> None:
            try:
                fn()
            except OSError as e:
                log.warning("%s (%s)", message, e)
                self.shellFailed.emit(message, fallback, fallback_is_dir)
        self._shell.submit(run)

    def _shell_failed(self, message: str, fallback: str, fallback_is_dir: bool) -> None:
        if fallback:
            self.toast.show_message(message, "Open in File Explorer", lambda: self._shell.submit(
                lambda: self.show_in_explorer(fallback, fallback_is_dir)))
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
        self.sidebar.set_roots(self.library.roots())

    def rescan_library(self) -> None:
        """Every library folder, incrementally: only new or changed files are read again (SPEC §4)."""
        if not self._library_open():
            return
        count = self.library.rescan_all()
        self.statusBar().showMessage(
            f"Rescanning {count} library folder{'s' if count != 1 else ''}…" if count
            else "The library is empty. Add a folder first (File > Add folder…).", STATUS_MS)

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
        if last is not None:
            self.sidebar.select_root(last.root_id)
        if files:
            folders = list(dict.fromkeys(os.path.dirname(path) for path in files))
            what = "its folder" if len(folders) == 1 else "their folders"
            self.toast.show_message(
                "Only folders can be added to the library, not files.", f"Add {what}",
                lambda: self.library.add_folders(folders))

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
        self.library.add_folders(paths)  # files are sorted out off the UI thread, and come back as a toast
