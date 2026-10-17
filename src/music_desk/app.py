import logging
import sys

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from music_desk import __version__
from music_desk.core.flac import flac_version
from music_desk.core.rsgain import rsgain_version
from music_desk.instance import InstanceServer, hand_over
from music_desk.logging_setup import setup_logging
from music_desk.paths import app_data_dir, flac_path, icon_path, library_db_path, rsgain_path
from music_desk.services.library import LibraryService
from music_desk.settings import Settings
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
    return next((argument for argument in arguments if argument and not argument.startswith("-")), None)


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

    # One MusicDesk at a time (SPEC §29): a running one gets this start's folder and comes to the front.
    folder = folder_argument(app.arguments()[1:])
    if hand_over(folder):
        log.info("Another MusicDesk is running: handed over, exiting")
        return 0
    instance = InstanceServer(parent=app)

    settings = Settings.load()
    try:
        library = LibraryService(library_db_path())
    except Exception as e:  # noqa: BLE001 - the window still opens, without a library
        log.exception("could not open the library index")
        library = None
        QMessageBox.critical(None, APP_TITLE, f"The library index could not be opened:\n{e}\n\n"
                             f"{library_db_path()}\nThe app opens without a library. See the log for details.")
    else:
        app.aboutToQuit.connect(library.close)

    window = MainWindow(library, settings)
    window.show()
    instance.folderRequested.connect(window.open_from_outside)
    if folder:
        window.open_from_outside(folder)
    if not getattr(sys, "frozen", False):  # running from source: log UI-thread stalls (SPEC §18)
        StallWatch(app)

    check = ToolCheck()
    check.signals.rsgain.connect(window.show_rsgain_version)
    check.signals.flac.connect(window.show_flac_version)
    QThreadPool.globalInstance().start(check)

    code = app.exec()
    QThreadPool.globalInstance().waitForDone(5000)
    log.info("Exiting with code %s", code)
    return code
