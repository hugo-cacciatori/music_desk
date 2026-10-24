import logging
import sys
from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from music_desk import __version__
from music_desk.context_menu.cleanup_entry import CleanupEntry
from music_desk.context_menu.remove_entry import RemoveEntry
from music_desk.context_menu.rename_entry import RenameWindow
from music_desk.context_menu.replaygain_entry import ReplayGainWindow
from music_desk.context_menu.request import (
    CLEANUP,
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
from music_desk.context_menu.tidy_entry import TidyWindow
from music_desk.core.flac import flac_version
from music_desk.core.path_locks import PathLocks
from music_desk.core.rsgain import rsgain_version
from music_desk.instance import InstanceServer, hand_over
from music_desk.logging_setup import setup_logging
from music_desk.paths import app_data_dir, flac_path, icon_path, library_db_path, rsgain_path
from music_desk.services.library import LibraryService
from music_desk.services.replaygain_job import Finished
from music_desk.settings import Settings
from music_desk.ui import qt_util
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

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"MusicDesk.MusicDesk.{__version__}")
    except (AttributeError, OSError):
        pass  # not Windows, or an older shell: only the taskbar grouping differs


def folder_argument(arguments: list[str]) -> str | None:
    """The folder to open (SPEC §29): the first argument that isn't an option, as MusicDeskHere.exe passes it."""
    return parse_arguments(arguments).folder


EntryWindow = ReplayGainWindow | RenameWindow | RemoveEntry | TidyWindow | CleanupEntry
# The entries that run alone with MusicDesk closed, and what runs them (ReplayGain has its own rules: run_replaygain).
ALONE = {RENAME: RenameWindow, REMOVE_REPLAYGAIN: RemoveEntry, TIDY: TidyWindow, CLEANUP: CleanupEntry}


class Starter(QObject):
    """What this process shows: the main window, or only right-click entries' windows until a normal start asks
    for the main window (SPEC §31). `open_library` opens the index; tests replace it and leave out `check_tools`
    (the rsgain and flac self-check)."""

    def __init__(self, settings: Settings, open_library: Callable[[], LibraryService | None],
                 parent: QObject | None = None, check_tools: bool = True) -> None:
        super().__init__(parent)
        self.settings = settings
        self.open_library = open_library
        self.check_tools = check_tools
        self.window: MainWindow | None = None
        self.settings_entry: SettingsEntry | None = None
        self.entry_windows: list[EntryWindow] = []  # the entries' own windows, while there is no main window
        self._cards: ToolCards | None = None  # the entries' Options cards, until the main window takes them
        self.locks = PathLocks()  # the entries' folder locks; the main window's library shares them

    def cards(self) -> ToolCards:
        """One copy of the settings in memory for the whole process: the main window's cards once it exists."""
        if self.window is not None:
            return self.window.tools.cards
        if self._cards is None:
            self._cards = ToolCards(self.settings)
        return self._cards

    def main_window(self) -> MainWindow:
        """The main window, made on first use, on the entries' cards and folder locks if they came first."""
        if self.window is None:
            library = self.open_library()
            if library is not None:
                library.locks = self.locks  # a right-click ReplayGain run may hold some already
            self.window = MainWindow(library, self.settings, self._cards)
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
        """A right-click entry (SPEC §31): in the main window if it is open, else in a window of its own."""
        if entry in REPLAYGAIN_ENTRIES:
            self.run_replaygain(entry, paths)
            return
        if entry in (SPLIT, TRIM):
            # Until the Split and Trim pages have windows of their own (slice 5 task 3), the main window shows them.
            self.main_window().tools.run_entry(entry, paths)
            return
        if entry in ALONE:
            if self.window is not None:
                self.window.tools.run_entry(entry, paths)
                return
            # The Rename preview alone; Remove's question and report; the Tidy page then Redundant files; Clean
            # up's Redundant files (SPEC §31).
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

    @property
    def replaygain_windows(self) -> list[ReplayGainWindow]:
        return [w for w in self.entry_windows if isinstance(w, ReplayGainWindow)]

    def _own(self, alone: EntryWindow) -> None:
        """An entry's own window: kept until it closes; the process ends with the last one (no main window)."""
        self.entry_windows.append(alone)
        alone.closed.connect(lambda: self._window_closed(alone))
        alone.finished.connect(self._refresh_index)
        qt_util.app().aboutToQuit.connect(alone.shutdown)

    def run_replaygain(self, entry: str, paths: list[str]) -> None:
        """A ReplayGain entry: refused while one of the entries' runs is going (one rsgain at a time), else a job of
        the main window, else the ReplayGain page alone in a new window (SPEC §31)."""
        running = next((w for w in self.replaygain_windows if w.running), None)
        if running is not None:
            running.refuse()
            return
        if self.window is not None:
            self.window.tools.run_entry(entry, paths)
            return
        window = ReplayGainWindow(self.cards(), self.locks)
        self._own(window)
        window.start(entry, paths)

    def _refresh_index(self, finished) -> None:
        """A main window opened meanwhile: its index catches up on the folders an entry's run changed (a
        ReplayGain run's Finished, or a list of folders)."""
        if self.window is not None and self.window._library_open():
            for folder in finished.folders if isinstance(finished, Finished) else finished:
                self.window.refresh_after_tool(folder)

    def _window_closed(self, alone: EntryWindow) -> None:
        if alone in self.entry_windows:
            self.entry_windows.remove(alone)
        alone.deleteLater()
        self._entry_closed()

    def _entry_closed(self) -> None:
        """Without a main window, closing the last entry's window ends the process (SPEC §31)."""
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
    setup_logging()
    log.info("%s %s starting (Python %s, frozen=%s)", APP_TITLE, __version__, sys.version.split()[0],
             getattr(sys, "frozen", False))
    log.info("App data: %s", app_data_dir())

    set_app_user_model_id()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QIcon(str(icon_path())))

    # One MusicDesk at a time (SPEC §29): a running one gets this start's folder, or its right-click entry (§31),
    # and comes to the front.
    request = parse_arguments(app.arguments()[1:], app_data_dir() / "menu")
    if hand_over(request.folder, entry=request.entry, paths=request.paths):
        log.info("Another MusicDesk is running: handed over, exiting")
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
