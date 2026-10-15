import logging
import sys

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from music_desk import __version__
from music_desk.core.rsgain import rsgain_version
from music_desk.logging_setup import setup_logging
from music_desk.paths import app_data_dir, library_db_path, rsgain_path
from music_desk.services.library import LibraryService
from music_desk.settings import Settings
from music_desk.ui.main_window import APP_TITLE, MainWindow

log = logging.getLogger(__name__)


class RsgainCheckSignals(QObject):
    finished = Signal(object)  # str | None


class RsgainCheck(QRunnable):
    """Runs `rsgain --version` off the UI thread (Ground rule 2)."""

    def __init__(self) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.signals = RsgainCheckSignals()

    def run(self) -> None:
        exe = rsgain_path()
        version = rsgain_version(exe)
        if version:
            log.info("rsgain self-check OK: %s (%s)", version, exe)
        else:
            log.warning("rsgain self-check failed: %s", exe)
        self.signals.finished.emit(version)


def main() -> int:
    setup_logging()
    log.info("%s %s starting (Python %s, frozen=%s)", APP_TITLE, __version__, sys.version.split()[0],
             getattr(sys, "frozen", False))
    log.info("App data: %s", app_data_dir())

    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setApplicationVersion(__version__)

    settings = Settings.load()
    try:
        library = LibraryService(library_db_path(), lyrics_offline=settings.lyrics_offline)
    except Exception as e:  # noqa: BLE001 - the window still opens, without a library
        log.exception("could not open the library index")
        library = None
        QMessageBox.critical(None, APP_TITLE, f"The library index could not be opened:\n{e}\n\n"
                             f"{library_db_path()}\nThe app opens without a library. See the log for details.")
    else:
        app.aboutToQuit.connect(library.close)

    window = MainWindow(library, settings)
    window.show()

    check = RsgainCheck()
    check.signals.finished.connect(window.show_rsgain_version)
    QThreadPool.globalInstance().start(check)

    code = app.exec()
    QThreadPool.globalInstance().waitForDone(5000)
    log.info("Exiting with code %s", code)
    return code
