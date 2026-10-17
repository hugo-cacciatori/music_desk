"""The tools' option cards and their dialogs (SPEC §30): File names (§21), Rename and ReplayGain (§10).

The cards replaced the Tools page (M21). They live as long as the window, each in its own non-modal dialog opened
from the toolbar's Options… or the Options menu, so a run reads their options whether a dialog is open or not.
The options are the saved defaults (Settings.name_masks / name_replacement, rename_options, replaygain_options),
saved again as they change. Starting a tool is the toolbar's: `request` turns a tool and its target into a
ToolRequest, or says why it can't.
"""

import dataclasses
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QScrollArea, QVBoxLayout, QWidget

from music_desk.core import rename, replaygain
from music_desk.settings import Settings
from music_desk.ui.tools.names_card import FileNamesCard
from music_desk.ui.tools.rename_card import FIX_FIRST, Card, RenameCard
from music_desk.ui.tools.replaygain_card import ReplayGainCard

NAMES, RENAME, REPLAYGAIN, TIDY = "names", "rename", "replaygain", "tidy"
DIALOG_TITLES = {NAMES: "File names", RENAME: "Rename options", REPLAYGAIN: "ReplayGain options"}


@dataclass(frozen=True)
class ToolScope:
    """What the tools act on: a folder, or a selection of files inside it."""

    folder: Path
    folder_id: int
    files: tuple[Path, ...] = ()  # a selection; empty for the whole folder
    first_track: Path | None = None  # for the File names card's live example

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


class CardDialog(QDialog):
    """One card, its options and Reset, and Close. Not modal: the window stays usable, and every change is saved
    as it is made, so Close (or Esc) only closes."""

    def __init__(self, parent: QWidget | None, title: str, card: Card) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(False)
        self.card = card
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # the card wraps instead
        holder = QWidget()  # the card at its own height, the space below it empty
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(card)
        column.addStretch(1)
        scroll.setWidget(holder)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        close = buttons.button(QDialogButtonBox.StandardButton.Close)
        close.setToolTip("Close this window; the options are already saved")
        layout = QVBoxLayout(self)
        layout.addWidget(scroll, 1)
        layout.addWidget(buttons)
        self.resize(640, 520)


class ToolCards(QObject):
    changed = Signal()  # any card's options changed (the toolbar re-reads what Start can do)

    def __init__(self, settings: Settings, window: QWidget | None = None) -> None:
        super().__init__(window)
        self.settings = settings
        self.window = window
        self.names_card = FileNamesCard()
        self.names_card.set_masks(settings.name_masks, settings.name_replacement)
        self.names_card.changed.connect(self._save_names)
        self.rename_card = RenameCard(self.names_card)
        self.replaygain_card = ReplayGainCard()
        self.rename_card.set_options(rename.options_from_json(settings.rename_options))
        self.replaygain_card.set_options(replaygain.options_from_json(settings.replaygain_options))
        self.rename_card.changed.connect(self._save_rename)
        self.replaygain_card.changed.connect(self._save_replaygain)
        for card in (self.rename_card, self.replaygain_card):
            card.run_button.hide()  # the toolbar starts the tools (SPEC §30)
            card.set_selection(False)  # the dialogs show every option; a selection is the run's business
        self.dialogs: dict[str, CardDialog] = {}

    def card(self, which: str) -> Card:
        return {NAMES: self.names_card, RENAME: self.rename_card, REPLAYGAIN: self.replaygain_card}[which]

    def open_options(self, which: str) -> CardDialog:
        """The card's dialog, made on first use and raised again after."""
        dialog = self.dialogs.get(which)
        if dialog is None:
            dialog = self.dialogs[which] = CardDialog(self.window, DIALOG_TITLES[which], self.card(which))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return dialog

    def set_example(self, fields: dict | None, extension: str) -> None:
        self.names_card.set_example(fields, extension)

    def set_rsgain_unavailable(self, reason: str | None) -> None:
        self.replaygain_card.set_unavailable(reason)

    # -- starting a tool --

    def problems(self, tool: str) -> list[str]:
        """What stops a tool's Start, whatever the target: its options (and rsgain for ReplayGain and Tidy)."""
        if tool == RENAME:
            return self.rename_card.problems_list()
        if tool == REPLAYGAIN:
            unavailable = self.replaygain_card.unavailable
            return self.replaygain_card.problems_list() + ([unavailable] if unavailable else [])
        problems = []
        if not self.names_card.valid:
            problems.append(FIX_FIRST)
        elif not self.rename_card.valid:
            problems.append("The Rename options need fixing.")
        if not self.replaygain_card.valid:
            problems.append("The ReplayGain options need fixing.")
        if self.replaygain_card.unavailable:
            problems.append(self.replaygain_card.unavailable)
        return problems

    def rename_options(self) -> rename.RenameOptions:
        """The Rename options for a run: the ReplayGain log (its configured name) is never junk."""
        options = self.rename_card.options()
        return dataclasses.replace(options, protected_names=(self.replaygain_card.options().log_file,))

    def request(self, tool: str, scope: ToolScope) -> ToolRequest | None:
        """The run's request, or None while the options need fixing. ReplayGain and Tidy work on folders: a
        selection's files are left to Rename."""
        if self.problems(tool):
            return None
        if tool == RENAME:
            return ToolRequest(RENAME, scope, rename_options=self.rename_options())
        folder_scope = dataclasses.replace(scope, files=())
        if tool == REPLAYGAIN:
            return ToolRequest(REPLAYGAIN, folder_scope, replaygain_options=self.replaygain_card.options(),
                               discography=self.replaygain_card.scope == "discography")
        return ToolRequest(TIDY, folder_scope, rename_options=self.rename_options(),
                           replaygain_options=self.replaygain_card.options())

    # -- saving --

    def _save_names(self) -> None:
        self.settings.name_masks = list(self.names_card.mask_list())
        self.settings.name_replacement = self.names_card.replacement_text()
        self.changed.emit()

    def _save_rename(self) -> None:
        self.settings.rename_options = rename.options_to_json(self.rename_card.options())
        self.changed.emit()

    def _save_replaygain(self) -> None:
        self.settings.replaygain_options = replaygain.options_to_json(self.replaygain_card.options())
        self.changed.emit()
