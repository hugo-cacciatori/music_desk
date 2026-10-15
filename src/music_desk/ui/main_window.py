import base64
import os
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, QObject, Qt
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
    QMessageBox,
    QSplitter,
    QStyle,
    QToolBar,
    QWidget,
)

from music_desk.core.index import queries
from music_desk.core.index.roots import AddResult, AddStatus, Root
from music_desk.core.index.scanner import ScanProgress, ScanReport, find_folder
from music_desk.core.index.store import path_of
from music_desk.services.library import LibraryService
from music_desk.settings import Settings
from music_desk.ui.address_bar import AddressBar
from music_desk.ui.file_view import FileView
from music_desk.ui.sidebar import Sidebar
from music_desk.ui.toast import Toast

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
    def __init__(self, library: LibraryService | None = None, settings: Settings | None = None) -> None:
        super().__init__()
        self.library = library
        self.settings = settings or Settings()
        self.folder_id: int | None = None
        self.folder_path: str | None = None  # to find the nearest surviving folder if this one disappears
        self.history_back: list[int] = []
        self.history_forward: list[int] = []
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
        self.splitter = QSplitter()
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(self.file_view)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([280, 920])
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
        if record and self.folder_id is not None:
            self.history_back.append(self.folder_id)
            self.history_forward.clear()
        self.folder_id = entry_id
        self.folder_path = str(path_of(self.library.conn, entry_id))
        self.file_view.set_folder(entry_id)
        crumbs = queries.folder_crumbs(self.library.conn, entry_id)
        self.address_bar.set_location(crumbs, self.folder_path)
        self.sidebar.tree.select_entry([entry for entry, _name in crumbs])
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

    def folder_updated(self, entry_id: int) -> None:
        if not self._library_open():
            return
        self._keep_open_folder()
        self.sidebar.model.refresh(entry_id)
        if entry_id == self.folder_id:
            self.file_view.refresh()

    def scan_finished(self, _report: ScanReport) -> None:
        if not self._library_open():
            return
        self._keep_open_folder()
        self.sidebar.model.refresh_loaded()
        if self.folder_id is not None:
            self.file_view.refresh()

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
        self.save_state()
        QApplication.instance().removeEventFilter(self)
        super().closeEvent(event)

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
