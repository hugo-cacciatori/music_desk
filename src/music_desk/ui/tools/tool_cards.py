"""The Options window and its pages (SPEC §12, §30): File names (§21), Rename and ReplayGain (§10), Clean up (§32),
Split (§22), Trim (§23) and Browser (§5): every setting of the app, and only there.

The cards replaced the Tools page (M21). They live as long as the window, together in one non-modal Options window
opened from File > Options…, so a run reads their options whether it is open or not. The options are the saved
defaults (Settings.name_masks / name_replacement, rename_options, replaygain_options, cleanup_options,
save_cue_beside, trim_subfolder, hide_folders_without_music), saved again as they change. Starting a tool is the
toolbar's: `request` turns a tool and its target into a ToolRequest, or says why it can't.
"""

import dataclasses

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QListWidget,
    QMessageBox,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from music_desk.core import cleanup, rename, replaygain
from music_desk.settings import Settings
from music_desk.ui.tools.cleanup_card import CleanupCard
from music_desk.ui.tools.names_card import FileNamesCard
from music_desk.ui.tools.rename_card import FIX_FIRST, Card, RenameCard
from music_desk.ui.tools.replaygain_card import ReplayGainCard
from music_desk.ui.tools.settings_cards import BrowserCard, SplitCard, TrimCard

NAMES, RENAME, REPLAYGAIN, TIDY, CLEANUP = "names", "rename", "replaygain", "tidy", "cleanup"
SPLIT, TRIM, BROWSER = "split", "trim", "browser"
PAGE_TITLES = {NAMES: "File names", RENAME: "Rename", REPLAYGAIN: "ReplayGain", CLEANUP: "Clean up",
               SPLIT: "Split", TRIM: "Trim", BROWSER: "Browser"}  # the Options window's list, in order


class OptionsWindow(QDialog):
    """File > Options… (SPEC §12, §30): every page in one window, picked from the list on the left. Not modal: the
    window stays usable, and every change is saved as it is made, so Close (or Esc) only closes. Restore all
    defaults asks first: there is no Recycle Bin for settings."""

    def __init__(self, parent: QWidget | None, cards: list[tuple[str, Card]]) -> None:
        super().__init__(parent)
        self.cards = [card for _which, card in cards]
        self.setWindowTitle("Options")
        self.setModal(False)
        self.pages: list[str] = []
        self.list = QListWidget()
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.stack = QStackedWidget()
        for which, card in cards:
            self.pages.append(which)
            self.list.addItem(PAGE_TITLES[which])
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # the card wraps instead
            holder = QWidget()  # the card at its own height, the space below it empty
            column = QVBoxLayout(holder)
            column.setContentsMargins(0, 0, 0, 0)
            column.addWidget(card)
            column.addStretch(1)
            scroll.setWidget(holder)
            self.stack.addWidget(scroll)
        self.list.setFixedWidth(self.list.sizeHintForColumn(0) + 2 * self.list.frameWidth() + 24)
        self.list.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.list.setCurrentRow(0)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        close = buttons.button(QDialogButtonBox.StandardButton.Close)
        close.setToolTip("Close this window; the options are already saved")
        self.restore_all = buttons.addButton("Restore all defaults", QDialogButtonBox.ButtonRole.ResetRole)
        self.restore_all.setToolTip("Put back the default of every setting on every page, after asking")
        self.restore_all.clicked.connect(self.restore_all_defaults)
        row = QHBoxLayout()
        row.addWidget(self.list)
        row.addWidget(self.stack, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(row, 1)
        layout.addWidget(buttons)
        self.resize(820, 560)

    @property
    def current(self) -> str:
        return self.pages[self.stack.currentIndex()]

    def show_page(self, which: str) -> None:
        self.list.setCurrentRow(self.pages.index(which))

    def restore_all_defaults(self) -> bool:
        """Every page's Restore defaults at once, after asking. True when restored."""
        if not self.confirm_restore_all():
            return False
        for card in self.cards:
            card.reset()
        return True

    def confirm_restore_all(self) -> bool:
        """Ask before losing every setting (replaced in tests)."""
        box = QMessageBox(QMessageBox.Icon.Question, "Restore all defaults",
                          "Restore every setting to its default? Your masks, junk list and other changes will be "
                          "lost.", parent=self)
        restore = box.addButton("Restore", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return box.clickedButton() is restore


class ToolCards(QObject):
    changed = Signal()  # any card's options changed (the toolbar re-reads what Start can do)
    hideFoldersChanged = Signal(bool)  # the Browser page's box: the folder tree applies it at once

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
        self.cleanup_card = CleanupCard()
        self.cleanup_card.set_options(cleanup.options_from_json(settings.cleanup_options))
        self.cleanup_card.changed.connect(self._save_cleanup)
        self.split_card = SplitCard()
        self.split_card.set_value(settings.save_cue_beside)
        self.split_card.changed.connect(self._save_split)
        self.trim_card = TrimCard()
        self.trim_card.set_value(settings.trim_subfolder)
        self.trim_card.changed.connect(self._save_trim)
        self.browser_card = BrowserCard()
        self.browser_card.set_value(settings.hide_folders_without_music)
        self.browser_card.changed.connect(self._save_browser)
        for card in (self.rename_card, self.replaygain_card):
            card.run_button.hide()  # the toolbar starts the tools (SPEC §30)
            card.set_selection(False)  # the dialogs show every option; a selection is the run's business
        self.options_window: OptionsWindow | None = None
        if window is not None:
            self.adopt(window)

    def adopt(self, window: QWidget) -> None:
        """The main window takes these cards (made without one for the right-click Settings entry, SPEC §31), so
        the process keeps one copy of the settings."""
        self.window = window
        self.setParent(window)
        if self.options_window is None:
            # Until the Options window takes them, the cards are hidden windows of the main window, so they go
            # with it. Parentless, every closed window left its cards behind, and Python destroying hundreds of
            # them after the QApplication crashed the test run at exit (SPEC §18, 2026-10-23).
            for which in PAGE_TITLES:
                self.card(which).setParent(window, Qt.WindowType.Window)

    def dispose(self) -> None:
        """Free the cards and the Options window while Qt still runs. Made without a main window (the right-click
        entries, SPEC §31), they have no parent to go with: destroyed by Python after the QApplication, such windows
        crashed the process at exit (SPEC §18, 2026-10-23)."""
        for which in PAGE_TITLES:
            self.card(which).deleteLater()
        if self.options_window is not None:
            self.options_window.deleteLater()
        self.deleteLater()

    def card(self, which: str) -> Card:
        return {NAMES: self.names_card, RENAME: self.rename_card, REPLAYGAIN: self.replaygain_card,
                CLEANUP: self.cleanup_card, SPLIT: self.split_card, TRIM: self.trim_card,
                BROWSER: self.browser_card}[which]

    def open_options(self, which: str | None = None) -> OptionsWindow:
        """The Options window, made on first use and raised again after, on `which`'s page (None: as it was)."""
        dialog = self.options_window
        if dialog is None:
            dialog = self.options_window = OptionsWindow(self.window, [(w, self.card(w)) for w in PAGE_TITLES])
        if which is not None:
            dialog.show_page(which)
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
        if tool == CLEANUP:
            return self.cleanup_card.problems_list()
        problems = []
        if not self.names_card.valid:
            problems.append(FIX_FIRST)
        elif not self.rename_card.valid:
            problems.append("The Rename options need fixing.")
        if not self.replaygain_card.valid:
            problems.append("The ReplayGain options need fixing.")
        if self.replaygain_card.unavailable:
            problems.append(self.replaygain_card.unavailable)
        if cleanup.validate_lists(self.cleanup_card.options()):
            problems.append("The Clean up options' picture or video types need fixing.")
        return problems

    def rename_options(self) -> rename.RenameOptions:
        return self.rename_card.options()

    def cleanup_options(self) -> cleanup.CleanupOptions:
        """Clean up's options for a run: the ReplayGain log (its configured name) is never junk."""
        options = self.cleanup_card.options()
        return dataclasses.replace(options, protected_names=(self.replaygain_card.options().log_file,))

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

    def _save_cleanup(self) -> None:
        self.settings.cleanup_options = cleanup.options_to_json(self.cleanup_card.options())
        self.changed.emit()

    def _save_split(self) -> None:
        self.settings.save_cue_beside = self.split_card.value()
        self.changed.emit()

    def _save_trim(self) -> None:
        self.settings.trim_subfolder = self.trim_card.value()
        self.changed.emit()

    def _save_browser(self) -> None:
        hide = self.browser_card.value()
        if hide != self.settings.hide_folders_without_music:
            self.settings.hide_folders_without_music = hide
            self.hideFoldersChanged.emit(hide)
        self.changed.emit()
