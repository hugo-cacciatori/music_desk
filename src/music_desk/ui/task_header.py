"""A task window's header (SPEC §36 task 4): the task as a large title, then what it works on. A folder or file shows
its name, then its full path small in the secondary colour; a phrase ("2 folders in D:\\Music") shows as is."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from music_desk.ui import theme


class TaskHeader(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title = QLabel()
        self.title.setFont(theme.scaled(self.title.font(), theme.SCALE_TASK_TITLE, strong=True))
        self.name = QLabel()
        self.name.setFont(theme.scaled(self.name.font(), theme.SCALE_TASK_NAME))
        self.path = QLabel()
        theme.secondary(self.path)
        for label in (self.name, self.path):
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, theme.SPACE_S)
        layout.setSpacing(0)
        for label in (self.title, self.name, self.path):
            layout.addWidget(label)
        self._text = ""

    def set(self, title: str, what: str | Path = "") -> None:
        """`what`: an absolute path (a str or a Path) shows its name above it; anything else, as written."""
        what = str(what)
        self.title.setText(title)
        is_path = bool(what) and Path(what).is_absolute()
        self.name.setText(Path(what).name or what if is_path else what)
        self.path.setText(what if is_path else "")
        self.name.setVisible(bool(what))
        self.path.setVisible(is_path)
        self._text = f"{title} · {what}" if what else title

    def text(self) -> str:
        """The header as one plain line, "Tidy · D:\\Music\\Album"."""
        return self._text
