"""The Rename card of the Tools page (SPEC §10 A): the options of core.rename, checked as they are typed.

The card only collects options and validates them (core.rename.validate: the script's validate_config plus the
cleanup protections); Preview stays disabled until they pass. The preview and the run come from the window. The
masks are the File names card's (SPEC §21): this card only shows where they are.
"""

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from music_desk.core import rename
from music_desk.core.rename import RenameOptions

DESCRIPTION = (
    "Renames every music file in this folder (and its subfolders) from its tags, using the first mask whose fields "
    "are all present. Characters not allowed in file names are replaced. A file is never renamed over another "
    "one; files that fit no mask keep their name. A .lrc file with the same name follows its music file.")
SELECTION_DESCRIPTION = (
    "Renames the selected music files from their tags, using the first mask whose fields are all present. "
    "Characters not allowed in file names are replaced. A file is never renamed over another one; files that fit "
    "no mask keep their name. A .lrc file with the same name follows its music file. Junk files are never "
    "touched when renaming a selection.")
FIX_FIRST = "Fix the file names first."  # what the cards that use the masks say while they are invalid
MASKS_LINE = "File names: the masks in the File names card above."


def parse_extensions(text: str) -> frozenset[str]:
    """".cue .nfo, .txt" -> the extensions as typed (validation reports the ones that aren't lowercase dotted)."""
    return frozenset(part for part in re.split(r"[\s,;]+", text.strip()) if part)


def format_extensions(extensions) -> str:
    return " ".join(sorted(extensions))


def problems_text(problems: list[str]) -> str:
    return "\n".join(f"• {problem}" for problem in problems)


TITLE_SCALE = 1.3
CARD_MARGIN = 14  # px inside each card


class Card(QFrame):
    """A tool card: a title, a plain-language description, options, problems and buttons."""

    def __init__(self, title: str, description: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        # A clear hierarchy (Hugo, M17): a larger title, the description in a softer colour, the Run button bold.
        self.title = QLabel(title)
        font = self.title.font()
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * TITLE_SCALE)
        self.title.setFont(font)
        self.description = QLabel(description)
        self.description.setWordWrap(True)
        self.description.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        self.body = QVBoxLayout()
        self.problems = QLabel()
        self.problems.setWordWrap(True)
        self.problems.setStyleSheet("color: #c42b1c;")
        self.problems.hide()
        self.buttons = QHBoxLayout()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(CARD_MARGIN, CARD_MARGIN, CARD_MARGIN, CARD_MARGIN)
        layout.addWidget(self.title)
        layout.addWidget(self.description)
        layout.addLayout(self.body)
        layout.addWidget(self.problems)
        layout.addLayout(self.buttons)

    @staticmethod
    def primary(button: QPushButton) -> QPushButton:
        """The card's main action, in bold so it stands out from Reset to defaults."""
        font = button.font()
        font.setBold(True)
        button.setFont(font)
        return button

    def show_problems(self, problems: list[str]) -> None:
        self.problems.setText(problems_text(problems))
        self.problems.setVisible(bool(problems))

    @staticmethod
    def advanced_toggle(widget: QWidget, text: str = "Advanced") -> QToolButton:
        """A collapsed section: the button shows or hides `widget`."""
        button = QToolButton()
        button.setText(text)
        button.setCheckable(True)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setArrowType(Qt.ArrowType.RightArrow)
        button.setToolTip("Show or hide the less common options")
        button.setAutoRaise(True)
        widget.hide()

        def toggled(on: bool) -> None:
            widget.setVisible(on)
            button.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)

        button.toggled.connect(toggled)
        return button


class RenameCard(Card):
    changed = Signal()  # the options changed (valid or not), the shared masks included
    runRequested = Signal()

    def __init__(self, names, parent: QWidget | None = None) -> None:
        """`names` is the File names card (ui.tools.names_card), whose masks a run uses."""
        super().__init__("Rename files from tags", DESCRIPTION, parent)
        self.names = names
        self._loading = False
        self.selection = False

        self.masks_line = QLabel(MASKS_LINE)
        self.masks_line.setWordWrap(True)
        self.recursive = QCheckBox("Include subfolders")
        self.recursive.setToolTip("Rename the music files of every subfolder too, not only this folder's")
        self.recursive.toggled.connect(self._edited)
        self.cleanup = QCheckBox("Send junk files to the Recycle Bin")
        self.cleanup.setToolTip("Also send the files of the junk types below to the Recycle Bin; the preview "
                                "lists them first")
        self.cleanup.toggled.connect(self._edited)
        self.junk = QLineEdit()
        self.junk.textEdited.connect(self._edited)
        self.junk.setToolTip("Extensions, separated by spaces. Music files, playlists, cover images and the "
                             "ReplayGain log are never junk.")
        self.selection_note = QLabel()
        self.selection_note.setWordWrap(True)
        self.audio = QLineEdit()
        self.audio.setToolTip("The extensions renamed as music files, separated by spaces")
        self.audio.textEdited.connect(self._edited)
        advanced = QWidget()
        advanced_form = QFormLayout(advanced)
        advanced_form.setContentsMargins(0, 0, 0, 0)
        advanced_form.addRow("Music file types:", self.audio)

        form = QFormLayout()
        form.addRow(self.masks_line)
        form.addRow("", self.recursive)
        form.addRow("", self.cleanup)
        form.addRow("Junk file types:", self.junk)
        self.body.addLayout(form)
        self.body.addWidget(self.selection_note)
        self.body.addWidget(self.advanced_toggle(advanced))
        self.body.addWidget(advanced)

        self.run_button = self.primary(QPushButton("Run rename…"))  # "…": a preview to check comes first
        self.run_button.setToolTip("Show the new names first; nothing is renamed until you apply them")
        self.run_button.clicked.connect(self.runRequested)
        self.reset_button = QPushButton("Reset to defaults")
        self.reset_button.setToolTip("Put back this card's default options")
        self.reset_button.clicked.connect(self.reset)
        self.buttons.addWidget(self.run_button)
        self.buttons.addStretch(1)
        self.buttons.addWidget(self.reset_button)
        names.changed.connect(self._edited)
        self.set_options(rename.DEFAULT_OPTIONS)

    # -- options --

    def options(self) -> RenameOptions:
        """The options for a run: this card's, with the File names card's masks and replacement."""
        return RenameOptions(masks=self.names.mask_list(), replacement=self.names.replacement_text(),
                             recursive=self.recursive.isChecked(), cleanup=self.cleanup.isChecked(),
                             junk_extensions=parse_extensions(self.junk.text()),
                             audio_extensions=parse_extensions(self.audio.text()))

    def set_options(self, options: RenameOptions) -> None:
        """The card's own options (the masks in `options` are ignored: they are the shared setting)."""
        self._loading = True
        self.recursive.setChecked(options.recursive)
        self.cleanup.setChecked(options.cleanup)
        self.junk.setText(format_extensions(options.junk_extensions))
        self.audio.setText(format_extensions(options.audio_extensions))
        self._loading = False
        self._edited()

    def reset(self) -> None:
        self.set_options(rename.DEFAULT_OPTIONS)

    def problems_list(self) -> list[str]:
        """What stops a run: the file names first (said once, the File names card lists the details), then this
        card's own options."""
        own = rename.validate_file_types(self.options())
        return ([FIX_FIRST] if not self.names.valid else []) + own

    @property
    def valid(self) -> bool:
        return not self.problems_list()

    # -- scope --

    def set_selection(self, selection: bool) -> None:
        """A file selection: only those files and their sidecars are renamed, never junk (SPEC §10)."""
        self.selection = selection
        self.description.setText(SELECTION_DESCRIPTION if selection else DESCRIPTION)
        self.recursive.setEnabled(not selection)
        self.cleanup.setEnabled(not selection)
        self.junk.setEnabled(not selection)
        self.selection_note.setText("Subfolders and junk files don't apply to a selection." if selection else "")
        self.selection_note.setVisible(selection)

    def _edited(self, *_args) -> None:
        if self._loading:
            return
        problems = self.problems_list()
        self.show_problems(problems)
        self.run_button.setEnabled(not problems)
        self.changed.emit()
