import logging
import sys

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtWidgets import QApplication

from music_desk import __version__
from music_desk.core.rsgain import rsgain_version
from music_desk.logging_setup import setup_logging
from music_desk.paths import app_data_dir, rsgain_path
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

    window = MainWindow()
    window.show()

    check = RsgainCheck()
    check.signals.finished.connect(window.show_rsgain_version)
    QThreadPool.globalInstance().start(check)

    code = app.exec()
    QThreadPool.globalInstance().waitForDone(5000)
    log.info("Exiting with code %s", code)
    return code
