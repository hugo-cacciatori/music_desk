"""A small window that says why a right-click entry did nothing (SPEC §31), as "Nothing to clean up in Kid A": from
the menu there is no status bar to read, so a click never seems to do nothing. It stays until it is closed.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class NoticeWindow(QWidget):
    closed = Signal()

    def __init__(self, title: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(f"MusicDesk · {title}")
        self.message = QLabel(text)
        self.message.setWordWrap(True)
        self.message.setMinimumWidth(360)
        self.close_button = QPushButton("Close")
        self.close_button.setToolTip("Close this window")
        self.close_button.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.message)
        layout.addLayout(buttons)

    def bring_to_front(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event: QCloseEvent) -> None:
        super().closeEvent(event)
        self.closed.emit()
