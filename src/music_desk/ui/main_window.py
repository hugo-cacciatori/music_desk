from PySide6.QtWidgets import QLabel, QMainWindow

APP_TITLE = "MusicDesk"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1200, 760)
        self.rsgain_label = QLabel("Checking rsgain…")
        self.statusBar().addPermanentWidget(self.rsgain_label)

    def show_rsgain_version(self, version: str | None) -> None:
        if version:
            self.rsgain_label.setText(version)
            self.rsgain_label.setToolTip("")
        else:
            self.rsgain_label.setText("rsgain unavailable: ReplayGain tools disabled")
            self.rsgain_label.setToolTip("The bundled rsgain.exe could not run. See the log for details.")
