"""A small non-modal notice at the bottom of the window, with an optional action button."""

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

TOAST_MS = 8000


class Toast(QFrame):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("toast")
        self.setStyleSheet("#toast { background: palette(tool-tip-base); border: 1px solid palette(mid);"
                           " border-radius: 6px; }")
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.button = QPushButton()
        self.close_button = QPushButton("✕")
        self.close_button.setFlat(True)
        self.close_button.clicked.connect(self.hide)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 8, 8)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.button)
        layout.addWidget(self.close_button)
        self._action: Callable[[], object] | None = None
        self.button.clicked.connect(self._run_action)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        parent.installEventFilter(self)
        self.hide()

    def show_message(self, text: str, action_text: str | None = None, action: Callable[[], object] | None = None,
                     timeout_ms: int = TOAST_MS) -> None:
        self.label.setText(text)
        self._action = action
        self.button.setVisible(bool(action_text and action))
        self.button.setText(action_text or "")
        self._place()
        self.show()
        self.raise_()
        self._timer.start(timeout_ms)

    def _run_action(self) -> None:
        action, self._action = self._action, None
        self.hide()
        if action:
            action()

    def _place(self) -> None:
        parent = self.parentWidget()
        assert parent is not None  # a toast always sits on its window
        width = min(560, parent.width() - 32)
        self.setFixedWidth(max(width, 200))
        self.adjustSize()
        self.move((parent.width() - self.width()) // 2, parent.height() - self.height() - 48)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize and self.isVisible():
            self._place()
        return False
