import base64
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, QObject, Qt, Signal
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
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QStyle,
    QToolBar,
    QWidget,
)

from music_desk.core import fsops, inline_rename, playlists
from music_desk.core.index import queries
from music_desk.core.index.roots import AddResult, AddStatus, Root
from music_desk.core.index.scanner import ScanProgress, ScanReport, find_folder
from music_desk.core.index.store import path_of
from music_desk.core.path_locks import PathLocks
from music_desk.services.library import LibraryService
from music_desk.services.tag_io import TagIO
from music_desk.services.tag_writer import TagWriter
from music_desk.settings import Settings
from music_desk.ui.address_bar import AddressBar
from music_desk.ui.file_view import FileView
from music_desk.ui.side_panel.panel import SelectedItem, SidePanel
from music_desk.ui.sidebar import Sidebar
from music_desk.ui.toast import Toast

log = logging.getLogger(__name__)

APP_TITLE = "MusicDesk"
STATUS_MS = 8000


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

    def __init__(self, library: LibraryService | None = None, settings: Settings | None = None) -> None:
        super().__init__()
        self.library = library
        self.settings = settings or Settings()
        self.folder_id: int | None = None
        self.folder_path: str | None = None  # to find the nearest surviving folder if this one disappears
        self.history_back: list[int] = []
        self.history_forward: list[int] = []
        # Handing files to Windows can stall (a slow drive, a busy shell), so it happens off the UI thread.
        # These three are replaced in tests, which never launch anything.
        self.launch = fsops.open_with_default_app
        self.show_in_explorer = fsops.open_in_explorer
        self.playlist_dir: Path | None = None  # None: %LOCALAPPDATA%\MusicDesk\playlists
        self._shell = ThreadPoolExecutor(1, thread_name_prefix="shell")
        self.setWindowTitle(APP_TITLE)
        self.resize(1200, 760)
        self.index_label = QLabel()
        self.index_label.hide()
        self.statusBar().addPermanentWidget(self.index_label)
        self.rsgain_label = QLabel("Checking rsgain…")
        self.statusBar().addPermanentWidget(self.rsgain_label)

        conn = lambda: self.library.conn if self._library_open() else None  # noqa: E731
        self.sidebar = Sidebar(conn, self.settings.expanded_folders)
        self.sidebar.model.only_music = self.settings.hide_folders_without_music
        self.file_view = FileView(conn)
        self.tag_io = TagIO(self)
        self.tag_writer = TagWriter(
            library.locks if library is not None else PathLocks(),
            on_written=lambda path: self.library.indexer.update_file(path) if self._library_open() else None,
            parent=self)
        self.side_panel = SidePanel(self.tag_io, self.tag_writer, conn)
        self.side_panel.summary.playRequested.connect(self.play_folder)
        self.splitter = QSplitter()
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(self.file_view)
        self.splitter.addWidget(self.side_panel)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setCollapsible(1, False)
        self.splitter.setSizes([260, 640, 340])
        self._selected_ids: list[int] = []
        self._restoring_selection = False
        self.setCentralWidget(self.splitter)
        self._build_navigation(conn)
        self._restore_state()

        self.add_folder_action = QAction("&Add folder…", self, shortcut=QKeySequence("Ctrl+O"))
        self.add_folder_action.triggered.connect(self.add_folder)
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(self.add_folder_action)
        file_menu.addSeparator()
        file_menu.addAction("E&xit", self.close)
        view_menu = self.menuBar().addMenu("&View")
        self.hide_empty_action = QAction("&Hide folders without music", self, checkable=True)
        self.hide_empty_action.setChecked(self.settings.hide_folders_without_music)
        self.hide_empty_action.toggled.connect(self.set_hide_folders_without_music)
        view_menu.addAction(self.hide_empty_action)

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
            menu, entry_id, rename=lambda: self.sidebar.tree.start_rename(entry_id))
        self.file_view.renameRequested.connect(self.rename_entry)
        self.sidebar.renameRequested.connect(self.rename_entry)
        self.renameDone.connect(self._renamed)
        self.renameFailed.connect(lambda message: self.toast.show_message(message))
        self.file_view.selectionChanged.connect(self._selection_changed)
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
            library.indexer.progress.connect(self.show_index_progress)
            library.indexer.idle.connect(self.index_label.hide)
            library.indexer.failed.connect(lambda message: self.statusBar().showMessage(message, STATUS_MS))
            library.indexer.folderUpdated.connect(self.folder_updated)
            library.indexer.scanFinished.connect(self.scan_finished)
            self.refresh_roots()
            self._reopen_last_folder()

    # -- rsgain --

    def show_rsgain_version(self, version: str | None) -> None:
        if version:
            self.rsgain_label.setText(version)
            self.rsgain_label.setToolTip("")
        else:
            self.rsgain_label.setText("rsgain unavailable: ReplayGain tools disabled")
            self.rsgain_label.setToolTip("The bundled rsgain.exe could not run. See the log for details.")

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
        self.addToolBar(toolbar)

        # Backspace goes up, but only from the lists (it edits text everywhere else).
        for view in (self.file_view.view, self.sidebar.tree):
            QShortcut(QKeySequence(Qt.Key.Key_Backspace), view, activated=self.go_up,
                      context=Qt.ShortcutContext.WidgetShortcut)
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

    def rescan_folder(self) -> None:
        """F5: reconcile the open folder with the disk now."""
        if self._library_open() and self.folder_id is not None:
            self.library.indexer.scan_folder(self.folder_id)
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
            self.file_view.select_ids(self._selected_ids)
            self._restoring_selection = False
            return
        self._show_selection(ids)

    def _show_selection(self, ids: list[int]) -> None:
        self._selected_ids = ids
        items, folder_ids = [], []
        for entry_id in ids:
            row = self.file_view.model.row_for(entry_id)
            if row is not None and self.folder_path:
                r = self.file_view.model.rows[row]
                items.append(SelectedItem(Path(self.folder_path, r.name), r.is_dir, r.is_audio))
                if r.is_dir:
                    folder_ids.append(entry_id)
        if len(items) == 1 and folder_ids:
            self.side_panel.show_folder(folder_ids[0])  # one folder selected: its summary
        elif not items and self.folder_id is not None:
            self.side_panel.show_folder(self.folder_id)  # nothing selected: the open folder's summary
        else:
            self.side_panel.show_selection(items)

    def _file_saved(self, path: str) -> None:
        """The index already caught up through TagWriter.on_written; the list row follows on folderUpdated."""
        self.statusBar().showMessage(f"Saved the tags of {os.path.basename(path)}.", STATUS_MS)

    def folder_updated(self, entry_id: int) -> None:
        if not self._library_open():
            return
        self._keep_open_folder()
        self.sidebar.model.refresh(entry_id)
        if entry_id == self.folder_id:
            self.file_view.refresh()
            self._show_selection(self._selected_ids)  # a renamed file reloads under its new name
        self._refresh_location()
        self.side_panel.refresh_folder()

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
        self._keep_open_folder()
        self.sidebar.model.refresh_loaded()
        if self.folder_id is not None:
            self.file_view.refresh()
        self.side_panel.refresh_folder()

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

    def save_state(self) -> None:
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
        self.tag_writer.close()  # the queued saves finish first
        self.tag_io.close()
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
            self.add_folder_actions(menu, ids[0], rename=lambda: self.file_view.start_rename(ids[0]))
            return menu
        open_action = menu.addAction("Open", lambda: self.open_files(ids))
        open_action.setEnabled(len(ids) == 1)
        play = menu.addAction("Play selection", lambda: self.play_selection(ids))
        play.setEnabled(bool(queries.playlist_for_ids(self.library.conn, ids)))
        menu.addSeparator()
        menu.addAction("Open in File Explorer", lambda: self.reveal(ids[0]))
        menu.addAction("Copy path" if len(ids) == 1 else "Copy paths", lambda: self.copy_paths(ids))
        if len(ids) == 1:
            menu.addSeparator()
            menu.addAction("Rename\tF2", lambda: self.file_view.start_rename(ids[0]))
        return menu

    def add_folder_actions(self, menu: QMenu, entry_id: int, include_open: bool = True, rename=None) -> None:
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
        menu.addAction("Rescan", lambda: self.library.indexer.scan_folder(entry_id))

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
        self.index_label.setText(text)
        self.index_label.show()

    # -- roots --

    def refresh_roots(self) -> None:
        if not self._library_open():
            return
        self._keep_open_folder()  # its root may have been removed, or merged into a new one
        self.sidebar.set_roots(self.library.roots())

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
