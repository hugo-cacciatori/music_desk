"""The Rename card of the Tools page (SPEC §10 A): the options of core.rename, checked as they are typed.

The card only collects options and validates them (core.rename.validate: the script's validate_config plus the
cleanup protections); Preview stays disabled until they pass. The preview and the run come from the window.
"""

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
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
MASK_FIELDS = ("track:02", "track", "track_total", "disc", "disc_total", "title", "artist", "albumartist", "album",
               "year", "genre", "composer")


def parse_extensions(text: str) -> frozenset[str]:
    """".cue .nfo, .txt" -> the extensions as typed (validation reports the ones that aren't lowercase dotted)."""
    return frozenset(part for part in re.split(r"[\s,;]+", text.strip()) if part)


def format_extensions(extensions) -> str:
    return " ".join(sorted(extensions))


def problems_text(problems: list[str]) -> str:
    return "\n".join(f"• {problem}" for problem in problems)


class Card(QFrame):
    """A tool card: a title, a plain-language description, options, problems and buttons."""

    def __init__(self, title: str, description: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.title = QLabel(f"<b>{title}</b>")
        self.description = QLabel(description)
        self.description.setWordWrap(True)
        self.body = QVBoxLayout()
        self.problems = QLabel()
        self.problems.setWordWrap(True)
        self.problems.setStyleSheet("color: #c42b1c;")
        self.problems.hide()
        self.buttons = QHBoxLayout()
        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addWidget(self.description)
        layout.addLayout(self.body)
        layout.addWidget(self.problems)
        layout.addLayout(self.buttons)

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
        button.setAutoRaise(True)
        widget.hide()

        def toggled(on: bool) -> None:
            widget.setVisible(on)
            button.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)

        button.toggled.connect(toggled)
        return button


class RenameCard(Card):
    changed = Signal()  # the options changed (valid or not)
    previewRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Rename files from tags", DESCRIPTION, parent)
        self._loading = False
        self._example_fields: dict | None = None
        self._example_ext = ""
        self.selection = False

        self.masks = QListWidget()
        self.masks.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.masks.setMaximumHeight(110)
        self.masks.model().rowsMoved.connect(self._edited)
        self.masks.itemChanged.connect(self._edited)
        self.add_mask = QPushButton("Add")
        self.add_mask.clicked.connect(self._add_mask)
        self.remove_mask = QPushButton("Remove")
        self.remove_mask.clicked.connect(self._remove_mask)
        self.insert_field = QToolButton()
        self.insert_field.setText("Insert field")
        self.insert_field.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.insert_field)
        for name in MASK_FIELDS:
            menu.addAction(f"{{{name}}}", lambda n=name: self.insert(f"{{{n}}}"))
        self.insert_field.setMenu(menu)
        mask_buttons = QVBoxLayout()
        for widget in (self.add_mask, self.remove_mask, self.insert_field):
            mask_buttons.addWidget(widget)
        mask_buttons.addStretch(1)
        masks_row = QHBoxLayout()
        masks_row.addWidget(self.masks, 1)
        masks_row.addLayout(mask_buttons)
        self.example = QLabel()
        self.example.setWordWrap(True)

        self.replacement = QLineEdit()
        self.replacement.setMaximumWidth(80)
        self.replacement.textEdited.connect(self._edited)
        self.recursive = QCheckBox("Include subfolders")
        self.recursive.toggled.connect(self._edited)
        self.cleanup = QCheckBox("Send junk files to the Recycle Bin")
        self.cleanup.toggled.connect(self._edited)
        self.junk = QLineEdit()
        self.junk.textEdited.connect(self._edited)
        self.junk.setToolTip("Extensions, separated by spaces. Music files, playlists, cover images and the "
                             "ReplayGain log are never junk.")
        self.selection_note = QLabel()
        self.selection_note.setWordWrap(True)
        self.audio = QLineEdit()
        self.audio.textEdited.connect(self._edited)
        advanced = QWidget()
        advanced_form = QFormLayout(advanced)
        advanced_form.setContentsMargins(0, 0, 0, 0)
        advanced_form.addRow("Music file types:", self.audio)

        form = QFormLayout()
        form.addRow("Masks (first match wins):", masks_row)
        form.addRow("", self.example)
        form.addRow("Replace invalid characters with:", self.replacement)
        form.addRow("", self.recursive)
        form.addRow("", self.cleanup)
        form.addRow("Junk file types:", self.junk)
        self.body.addLayout(form)
        self.body.addWidget(self.selection_note)
        self.body.addWidget(self.advanced_toggle(advanced))
        self.body.addWidget(advanced)

        self.preview_button = QPushButton("Preview…")
        self.preview_button.clicked.connect(self.previewRequested)
        self.reset_button = QPushButton("Reset to defaults")
        self.reset_button.clicked.connect(self.reset)
        self.buttons.addWidget(self.preview_button)
        self.buttons.addStretch(1)
        self.buttons.addWidget(self.reset_button)
        self.set_options(rename.DEFAULT_OPTIONS)

    # -- options --

    def options(self) -> RenameOptions:
        masks = tuple(self.masks.item(i).text() for i in range(self.masks.count()))
        return RenameOptions(masks=masks, replacement=self.replacement.text(), recursive=self.recursive.isChecked(),
                             cleanup=self.cleanup.isChecked(), junk_extensions=parse_extensions(self.junk.text()),
                             audio_extensions=parse_extensions(self.audio.text()))

    def set_options(self, options: RenameOptions) -> None:
        self._loading = True
        self.masks.clear()
        for mask in options.masks:
            self._new_item(mask)
        self.replacement.setText(options.replacement)
        self.recursive.setChecked(options.recursive)
        self.cleanup.setChecked(options.cleanup)
        self.junk.setText(format_extensions(options.junk_extensions))
        self.audio.setText(format_extensions(options.audio_extensions))
        self._loading = False
        self._edited()

    def reset(self) -> None:
        self.set_options(rename.DEFAULT_OPTIONS)

    def problems_list(self) -> list[str]:
        return rename.validate(self.options())

    @property
    def valid(self) -> bool:
        return not self.problems_list()

    # -- scope and example --

    def set_selection(self, selection: bool) -> None:
        """A file selection: only those files and their sidecars are renamed, never junk (SPEC §10)."""
        self.selection = selection
        self.description.setText(SELECTION_DESCRIPTION if selection else DESCRIPTION)
        self.recursive.setEnabled(not selection)
        self.cleanup.setEnabled(not selection)
        self.junk.setEnabled(not selection)
        self.selection_note.setText("Subfolders and junk files don't apply to a selection." if selection else "")
        self.selection_note.setVisible(selection)

    def set_example(self, fields: dict | None, extension: str = "") -> None:
        """The first file's rename fields (core.tags rename_fields), for the live example."""
        self._example_fields, self._example_ext = fields, extension
        self._update_example()

    def _update_example(self) -> None:
        if self._example_fields is None:
            self.example.setText("")
            return
        options = self.options()
        try:
            values = rename.mask_values(self._example_fields, options.replacement)
            choice = rename.choose_name(values, options.masks)
        except (ValueError, KeyError, IndexError, TypeError):
            choice = None
        if choice is None:
            self.example.setText("Example: the first file fits no mask and keeps its name.")
        else:
            self.example.setText(f"Example: {choice[0]}{self._example_ext.lower()}")

    # -- editing --

    def _new_item(self, text: str) -> QListWidgetItem:
        item = QListWidgetItem(text)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsDragEnabled)
        self.masks.addItem(item)
        return item

    def _add_mask(self) -> None:
        item = self._new_item("{title}")
        self.masks.setCurrentItem(item)
        self.masks.editItem(item)
        self._edited()

    def _remove_mask(self) -> None:
        row = self.masks.currentRow()
        if row >= 0:
            self.masks.takeItem(row)
            self._edited()

    def insert(self, text: str) -> None:
        """Append a field to the selected mask (or to a new one)."""
        item = self.masks.currentItem() or self._new_item("")
        self.masks.setCurrentItem(item)
        item.setText(f"{item.text()} {text}".strip() if item.text() and not item.text().endswith(" ") else
                     item.text() + text)

    def _edited(self, *_args) -> None:
        if self._loading:
            return
        problems = self.problems_list()
        self.show_problems(problems)
        self.preview_button.setEnabled(not problems)
        self.remove_mask.setEnabled(self.masks.count() > 0)
        self._update_example()
        self.changed.emit()
