"""The Tools page (SPEC §10): the Rename, ReplayGain and Tidy cards for one folder or a file selection.

It takes the centre of the window, with a Back button, so the tree stays usable. The cards' options are the
saved defaults (Settings.rename_options / replaygain_options), saved again as they change. The page itself runs
nothing: `runRequested` hands the tool, its scope and the checked options to the window.
"""

import dataclasses
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from music_desk.core import rename, replaygain
from music_desk.settings import Settings
from music_desk.ui.tools.rename_card import RenameCard
from music_desk.ui.tools.replaygain_card import ReplayGainCard, TidyCard

RENAME, REPLAYGAIN, TIDY = "rename", "replaygain", "tidy"


@dataclass(frozen=True)
class ToolScope:
    """What the tools act on: a folder, or a selection of files inside it."""

    folder: Path
    folder_id: int
    files: tuple[Path, ...] = ()  # a selection; empty for the whole folder
    first_track: Path | None = None  # for the Rename card's live example

    @property
    def is_selection(self) -> bool:
        return bool(self.files)


@dataclass(frozen=True)
class ToolRequest:
    tool: str  # RENAME, REPLAYGAIN or TIDY
    scope: ToolScope
    rename_options: rename.RenameOptions | None = None
    replaygain_options: replaygain.ReplayGainOptions | None = None
    discography: bool = False


class ToolsPage(QWidget):
    backRequested = Signal()
    runRequested = Signal(object)  # ToolRequest

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.scope: ToolScope | None = None

        self.back_button = QPushButton("← Back")
        self.back_button.clicked.connect(self.backRequested)
        self.heading = QLabel()
        self.heading.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.heading.setWordWrap(True)
        header = QHBoxLayout()
        header.addWidget(self.back_button)
        header.addWidget(self.heading, 1)

        self.rename_card = RenameCard()
        self.replaygain_card = ReplayGainCard()
        self.tidy_card = TidyCard(self.rename_card, self.replaygain_card)
        self.rename_card.set_options(rename.options_from_json(settings.rename_options))
        self.replaygain_card.set_options(replaygain.options_from_json(settings.replaygain_options))
        self.rename_card.changed.connect(self._save_rename)
        self.replaygain_card.changed.connect(self._save_replaygain)
        self.rename_card.previewRequested.connect(lambda: self.request(RENAME))
        self.replaygain_card.runRequested.connect(lambda: self.request(REPLAYGAIN))
        self.tidy_card.previewRequested.connect(lambda: self.request(TIDY))

        cards = QWidget()
        cards_layout = QVBoxLayout(cards)
        for card in (self.rename_card, self.replaygain_card, self.tidy_card):
            cards_layout.addWidget(card)
        cards_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # the cards wrap instead
        scroll.setWidget(cards)
        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(scroll, 1)

    def set_scope(self, scope: ToolScope) -> None:
        self.scope = scope
        if scope.is_selection:
            count = len(scope.files)
            self.heading.setText(f"<b>Tools</b> · {count} selected file{'s' if count != 1 else ''} in "
                                 f"{scope.folder}")
        else:
            self.heading.setText(f"<b>Tools</b> · {scope.folder}")
        self.rename_card.set_selection(scope.is_selection)
        self.replaygain_card.set_selection(scope.is_selection)
        self.rename_card.set_example(None)

    def set_example(self, fields: dict | None, extension: str) -> None:
        self.rename_card.set_example(fields, extension)

    def set_rsgain_unavailable(self, reason: str | None) -> None:
        self.replaygain_card.set_unavailable(reason)

    def rename_options(self) -> rename.RenameOptions:
        """The Rename card's options for a run: the ReplayGain log (its configured name) is never junk."""
        options = self.rename_card.options()
        return dataclasses.replace(options, protected_names=(self.replaygain_card.options().log_file,))

    def request(self, tool: str) -> None:
        if self.scope is None:
            return
        if tool == RENAME and self.rename_card.valid:
            self.runRequested.emit(ToolRequest(RENAME, self.scope, rename_options=self.rename_options()))
        elif tool == REPLAYGAIN and self.replaygain_card.run_button.isEnabled():
            self.runRequested.emit(ToolRequest(
                REPLAYGAIN, self.scope, replaygain_options=self.replaygain_card.options(),
                discography=self.replaygain_card.scope == "discography"))
        elif tool == TIDY and self.tidy_card.preview_button.isEnabled():
            self.runRequested.emit(ToolRequest(
                TIDY, self.scope, rename_options=self.rename_options(),
                replaygain_options=self.replaygain_card.options(), discography=self.tidy_card.scope == "discography"))

    def _save_rename(self) -> None:
        self.settings.rename_options = rename.options_to_json(self.rename_card.options())

    def _save_replaygain(self) -> None:
        self.settings.replaygain_options = replaygain.options_to_json(self.replaygain_card.options())
