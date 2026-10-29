import logging
import sys
from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from music_desk import __version__, self_test
from music_desk.context_menu.cleanup_entry import CleanupEntry
from music_desk.context_menu.remove_entry import RemoveEntry
from music_desk.context_menu.rename_entry import RenameWindow
from music_desk.context_menu.replaygain_entry import ReplayGainWindow
from music_desk.context_menu.request import (
    CLEANUP,
    EDIT_CUE,
    NEW_CUE,
    REMOVE_REPLAYGAIN,
    RENAME,
    REPLAYGAIN_ENTRIES,
    SETTINGS,
    SPLIT,
    TIDY,
    TRIM,
    parse_arguments,
)
from music_desk.context_menu.settings_entry import SettingsEntry
from music_desk.context_menu.split_recap import SplitRecapWindow
from music_desk.context_menu.tidy_entry import TidyWindow
from music_desk.context_menu.tool_host import CueEditorWindow, TrimWindow
from music_desk.core.flac import flac_version
from music_desk.core.path_locks import PathLocks
from music_desk.core.rsgain import rsgain_version
from music_desk.instance import InstanceServer, hand_over
from music_desk.logging_setup import setup_logging
from music_desk.paths import (
    app_data_dir,
    flac_path,
    icon_path,
    is_frozen,
    library_db_path,
    move_old_app_data,
    rsgain_path,
)
from music_desk.services.library import LibraryService
from music_desk.services.replaygain_job import Finished
from music_desk.settings import Settings
from music_desk.ui import qt_util, theme
from music_desk.ui.main_window import APP_TITLE, MainWindow
from music_desk.ui.stall_watch import StallWatch
from music_desk.ui.tools.tool_cards import ToolCards

log = logging.getLogger(__name__)


class ToolCheckSignals(QObject):
    rsgain = Signal(object)  # str | None
    flac = Signal(object)  # str | None


class ToolCheck(QRunnable):
    """Runs `rsgain --version` and `flac --version` off the UI thread (Ground rule 2, SPEC §13)."""

    def __init__(self) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.signals = ToolCheckSignals()

    def run(self) -> None:
        for name, exe, check, signal in (("rsgain", rsgain_path(), rsgain_version, self.signals.rsgain),
                                         ("flac", flac_path(), flac_version, self.signals.flac)):
            version = check(exe)
            if version:
                log.info("%s self-check OK: %s (%s)", name, version, exe)
            else:
                log.warning("%s self-check failed: %s", name, exe)
            signal.emit(version)


def set_app_user_model_id() -> None:
    """Windows groups taskbar buttons by this id: without it, a run from source shows Python's icon."""
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"Refrain.Refrain.{__version__}")
    except (AttributeError, OSError):
        pass  # not Windows, or an older shell: only the taskbar grouping differs


def folder_argument(arguments: list[str]) -> str | None:
    """The folder to open (SPEC §29): the first argument that isn't an option, as RefrainHere.exe passes it."""
    return parse_arguments(arguments).folder


EntryWindow = (ReplayGainWindow | RenameWindow | RemoveEntry | TidyWindow | CleanupEntry | TrimWindow
               | SplitRecapWindow | CueEditorWindow)
# The entries' own windows, and what runs them (ReplayGain has its own rules: run_replaygain).
ALONE = {RENAME: RenameWindow, REMOVE_REPLAYGAIN: RemoveEntry, TIDY: TidyWindow, CLEANUP: CleanupEntry,
         TRIM: TrimWindow}
# Entries that change the clicked files themselves: unsaved Metadata edits on them are asked about first.
CHANGES_FILES = {RENAME, REMOVE_REPLAYGAIN, TIDY, *REPLAYGAIN_ENTRIES}


class Starter(QObject):
    """What this process shows: the main window and the right-click entries' windows (SPEC §31, §35). Every entry
    opens its own window, whether the main window is open or not, and the process ends with its last window.
    `open_library` opens the index; tests replace it and leave out `check_tools` (the rsgain and flac
    self-check)."""

    def __init__(self, settings: Settings, open_library: Callable[[], LibraryService | None],
                 parent: QObject | None = None, check_tools: bool = True) -> None:
        super().__init__(parent)
        self.settings = settings
        self.open_library = open_library
        self.check_tools = check_tools
        self.window: MainWindow | None = None
        self.settings_entry: SettingsEntry | None = None
        self.entry_windows: list[EntryWindow] = []  # the entries' own windows, open or running
        self._cards: ToolCards | None = None  # the process's Options cards: the main window takes them as its own
        self._closed_windows: list[MainWindow] = []  # closed main windows, kept: they own the cards they took
        self.locks = PathLocks()  # the entries' folder locks; the main window's library shares them

    def cards(self) -> ToolCards:
        """One copy of the settings in memory for the whole process, shared by the entries and the main window."""
        if self._cards is None:
            self._cards = ToolCards(self.settings)
            qt_util.app().aboutToQuit.connect(self.release)
        return self._cards

    def release(self) -> None:
        """Before Qt shuts down (and after a test): close the entries' own windows, and free the cards made
        without a main window, which nothing else frees (`ToolCards.dispose`). A main window owns them once it took
        them."""
        for alone in list(self.entry_windows):
            alone.shutdown()
            if isinstance(alone, QWidget):
                alone.hide()
                alone.deleteLater()
        self.entry_windows.clear()
        if self._cards is not None and self.window is None and not self._closed_windows:
            self._cards.dispose()
        for closed in self._closed_windows:
            closed.deleteLater()
        self._closed_windows.clear()
        self._cards = None

    def main_window(self) -> MainWindow:
        """The main window, made on first use, on the entries' cards and folder locks if they came first."""
        if self.window is None:
            library = self.open_library()
            if library is not None:
                library.locks = self.locks  # a right-click ReplayGain run may hold some already
            self.window = MainWindow(library, self.settings, self.cards())
            self.window.closed.connect(self._main_closed)
            self.window.entryRequested.connect(self.run_entry)  # its menus' entries: the same windows (SPEC §35)
            if not self.check_tools:
                return self.window
            check = ToolCheck()
            check.signals.rsgain.connect(self.window.show_rsgain_version)
            check.signals.flac.connect(self.window.show_flac_version)
            QThreadPool.globalInstance().start(check)
            self.check = check  # not auto-deleted: kept alive here until it reports
        return self.window

    def open_folder(self, folder: str | None) -> None:
        """A normal start (or a later one): the main window, in front, on `folder` if any (SPEC §29)."""
        self.main_window().open_from_outside(folder)

    def run_entry(self, entry: str, paths: list[str]) -> None:
        """A right-click entry (SPEC §31, §35): a window of its own, alone in front, the main window (if open) left
        where it is. Its folder locks are the main window's, and the main window's index re-reads what it
        changed."""
        if entry in CHANGES_FILES and self.window is not None and not self.window.allow_changes(paths):
            return  # unsaved Metadata edits on a clicked file, and Cancel
        if entry in REPLAYGAIN_ENTRIES:
            self.run_replaygain(entry, paths)
            return
        if entry == SPLIT:
            # A recap, then Split or Cancel (Hugo, 2026-10-24).
            recap = SplitRecapWindow(self.cards(), self.locks)
            self._own(recap)
            recap.start(paths)
            return
        if entry in (EDIT_CUE, NEW_CUE):
            # The cue editor alone, with Refrain open or closed, as Split's recap (SPEC §31); New cue sheet… on
            # the Metadata tab opens it on a new sheet (SPEC §35).
            editor = CueEditorWindow(self.cards(), self.locks)
            self._own(editor)
            if entry == NEW_CUE:
                editor.start_new(paths)
            else:
                editor.start(paths)
            return
        if entry in ALONE:
            # The Rename preview alone; Remove's question and report; the Tidy page then Redundant files; Clean
            # up's Redundant files; the Trim page (SPEC §31).
            alone = ALONE[entry](self.cards(), self.locks)
            self._own(alone)
            alone.start(paths)
            return
        if entry != SETTINGS:
            log.warning("ignored an unknown right-click entry %r", entry)
            return
        if self.window is not None:
            self.window.tools.open_options_on(paths)
            return
        if self.settings_entry is not None:
            self.settings_entry.open(paths)
            return
        self.settings_entry = SettingsEntry(self.cards(), self)
        qt_util.app().aboutToQuit.connect(self.settings_entry.close)
        self.settings_entry.open(paths).finished.connect(self._entry_closed)

    def _own(self, alone: EntryWindow) -> None:
        """An entry's own window: kept until it closes; the process ends with its last window."""
        self.entry_windows.append(alone)
        alone.closed.connect(lambda: self._window_closed(alone))
        if isinstance(alone, RenameWindow):
            alone.renamed.connect(self._renamed)
        alone.finished.connect(self._refresh_index)
        qt_util.app().aboutToQuit.connect(alone.shutdown)

    def run_replaygain(self, entry: str, paths: list[str]) -> None:
        """A ReplayGain entry: the ReplayGain page alone in a new window (SPEC §31), started at once. Runs go side by
        side (SPEC §35): only folders another task holds are refused, in the window."""
        window = ReplayGainWindow(self.cards(), self.locks)
        self._own(window)
        window.start(entry, paths)

    def _refresh_index(self, finished) -> None:
        """The main window, if one is open, catches up on the folders an entry's run changed (a ReplayGain run's
        Finished, or a list of folders): its index, and its Metadata tab (`MainWindow.entry_finished`)."""
        if self.window is not None and self.window._library_open():
            self.window.entry_finished(list(finished.folders if isinstance(finished, Finished) else finished))

    def _renamed(self, steps) -> None:
        """A Rename window's renames: an open main window's index applies them in place, ids and tags kept."""
        if self.window is not None and self.window._library_open():
            self.window.apply_renames(steps)

    def _window_closed(self, alone: EntryWindow) -> None:
        if alone in self.entry_windows:
            self.entry_windows.remove(alone)
        alone.deleteLater()
        self._entry_closed()

    def _main_closed(self) -> None:
        """The main window closed: the entries' windows stay, and their runs go on (SPEC §35). Its library is closed;
        a later normal start makes a new main window."""
        window = self.window
        if window is None:
            return
        self.window = None
        self._closed_windows.append(window)
        if window.library is not None:
            window.library.close()
        self._entry_closed()

    def _entry_closed(self) -> None:
        """Without a main window, closing the last entry's window ends the process (SPEC §31, §35)."""
        if self.window is not None or self.entry_windows:
            return
        options = self._cards.options_window if self._cards is not None else None
        if options is None or not options.isVisible():
            QApplication.quit()


def open_library() -> LibraryService | None:
    try:
        library = LibraryService(library_db_path())
    except Exception as e:  # noqa: BLE001 - the window still opens, without a library
        log.exception("could not open the library index")
        QMessageBox.critical(None, APP_TITLE, f"The library index could not be opened:\n{e}\n\n"
                             f"{library_db_path()}\nThe app opens without a library. See the log for details.")
        return None
    qt_util.app().aboutToQuit.connect(library.close)
    return library


def main() -> int:
    if self_test.FLAG in sys.argv[1:]:  # the release gate: no log, no other Refrain, no window (AGENTS.md rule 9)
        return self_test.main(sys.argv[1:])
    # MusicDesk's data moves to Refrain's folder before anything opens it (SPEC §36 task 6). The installed app only:
    # a run from source never takes the data of an installed MusicDesk.
    moved, left = move_old_app_data() if is_frozen() else ([], [])
    setup_logging()
    log.info("%s %s starting (Python %s, frozen=%s)", APP_TITLE, __version__, sys.version.split()[0],
             getattr(sys, "frozen", False))
    log.info("App data: %s", app_data_dir())
    if moved:
        log.info("Moved from MusicDesk's app data: %s", ", ".join(moved))
    if left:
        log.warning("Left in MusicDesk's app data: %s", ", ".join(left))
        QApplication(sys.argv)
        QMessageBox.warning(None, APP_TITLE, "Refrain couldn't move your library and settings from MusicDesk's "
                            f"folder ({', '.join(left)}). Close MusicDesk if it's open, then start Refrain again; "
                            "nothing was lost.")
        return 1

    set_app_user_model_id()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QIcon(str(icon_path())))
    theme.apply(app)  # the font, and the style sheet that follows Windows' colours (SPEC §36)

    # One Refrain at a time (SPEC §29): a running one gets this start's folder, or its right-click entry (§31),
    # and comes to the front.
    request = parse_arguments(app.arguments()[1:], app_data_dir() / "menu")
    if hand_over(request.folder, entry=request.entry, paths=request.paths):
        log.info("Another Refrain is running: handed over, exiting")
        return 0
    instance = InstanceServer(parent=app)

    starter = Starter(Settings.load(), open_library, app)
    instance.folderRequested.connect(starter.open_folder)
    instance.entryRequested.connect(starter.run_entry)
    if request.entry is not None:
        starter.run_entry(request.entry, list(request.paths))
    else:
        starter.open_folder(request.folder)
    if not getattr(sys, "frozen", False):  # running from source: log UI-thread stalls (SPEC §18)
        StallWatch(app)

    code = app.exec()
    QThreadPool.globalInstance().waitForDone(5000)
    log.info("Exiting with code %s", code)
    return code
