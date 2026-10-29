"""The right-click Settings entry with Refrain not open (SPEC §31): only the Options window, no main window.

The window is the app's own (`ToolCards.open_options`, made without a main window), on the settings the app loads.
Its File names example is the clicked items' first track, read off the UI thread.
"""

from PySide6.QtCore import QObject

from refrain.services.tag_io import TagIO
from refrain.ui.tools.tool_cards import OptionsWindow, ToolCards


class SettingsEntry(QObject):
    def __init__(self, cards: ToolCards, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.cards = cards
        self.tag_io = TagIO(self)
        self.tag_io.renameFields.connect(self._example)
        self._token: int | None = None

    def open(self, paths: list[str]) -> OptionsWindow:
        """The Options window, raised again if it is open, with the example of these paths."""
        self.cards.set_example(None, "")
        self._token = self.tag_io.read_first_track_fields(paths)
        return self.cards.open_options()

    def close(self) -> None:
        self.tag_io.close()

    def _example(self, token: int, fields: dict | None, extension: str) -> None:
        if token == self._token:
            self.cards.set_example(fields, extension)
