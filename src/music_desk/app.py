import logging
import sys
from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from music_desk import __version__
from music_desk.context_menu.request import SETTINGS, parse_arguments
from music_desk.context_menu.settings_entry import SettingsEntry
from music_desk.core.flac import flac_version
from music_desk.core.rsgain import rsgain_version
from music_desk.instance import InstanceServer, hand_over
from music_desk.logging_setup import setup_logging
from music_desk.paths import app_data_dir, flac_path, icon_path, library_db_path, rsgain_path
from music_desk.services.library import LibraryService
from music_desk.settings import Settings
from music_desk.ui import qt_util
from music_desk.ui.main_window import APP_TITLE, MainWindow
from music_desk.ui.stall_watch import StallWatch

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


class Starter(QObject):
    """What this process shows: the main window, or only a right-click entry's window until a normal start asks
    for the main window (SPEC §31). `open_library` opens the index (replaced in tests)."""

    def __init__(self, settings: Settings, open_library: Callable[[], LibraryService | None],
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.open_library = open_library
        self.window: MainWindow | None = None
        self.settings_entry: SettingsEntry | None = None

    def main_window(self) -> MainWindow:
        """The main window, made on first use, on the Settings entry's cards if it came first."""
        if self.window is None:
            cards = self.settings_entry.cards if self.settings_entry is not None else None
            self.window = MainWindow(self.open_library(), self.settings, cards)
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
        if entry != SETTINGS:
            log.warning("ignored an unknown right-click entry %r", entry)
            return
        if self.window is not None:
            self.window.tools.open_options_on(paths)
            return
        if self.settings_entry is not None:
            self.settings_entry.open(paths)
            return
        self.settings_entry = SettingsEntry(self.settings, self)
        qt_util.app().aboutToQuit.connect(self.settings_entry.close)
        self.settings_entry.open(paths).finished.connect(self._settings_closed)

    def _settings_closed(self) -> None:
        """Without a main window, closing the Options window ends the process (SPEC §31)."""
        if self.window is None:
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
