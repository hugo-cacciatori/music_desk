"""The File names card, in File > Options… (SPEC §21, §30): the masks that name music files from their tags,
shared by Rename, Tidy, the cue split and the rename hint after a tag save. The editor moved here unchanged from
the Rename card.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
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

from music_desk.core import masks
from music_desk.ui.tools.rename_card import Card

DESCRIPTION = (
    "How MusicDesk names music files from their tags: the first mask whose fields are all present is used. "
    "Rename, Split albums and the rename suggestion after you save tags all use these masks.")
MASK_FIELDS = ("track:02", "track", "track_total", "disc", "disc_total", "title", "artist", "albumartist", "album",
               "year", "genre", "composer")


class FileNamesCard(Card):
    changed = Signal()  # the masks or the replacement changed (valid or not)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("File names", DESCRIPTION, parent)
        self._loading = False
        self._example_fields: dict | None = None
        self._example_ext = ""

        self.masks = QListWidget()
        self.masks.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.masks.setMaximumHeight(110)
        self.masks.model().rowsMoved.connect(self._edited)
        self.masks.itemChanged.connect(self._edited)
        self.add_mask = QPushButton("Add")
        self.add_mask.setToolTip("Add a mask at the end of the list; drag the masks to change their order")
        self.add_mask.clicked.connect(self._add_mask)
        self.remove_mask = QPushButton("Remove")
        self.remove_mask.setToolTip("Remove the selected mask from the list")
        self.remove_mask.clicked.connect(self._remove_mask)
        self.insert_field = QToolButton()
        self.insert_field.setText("Insert field")
        self.insert_field.setToolTip("Insert a tag field, like {title}, into the selected mask")
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
        self.replacement.setToolTip('Put in place of each character Windows does not allow in file names '
                                    '(\\ / : * ? " < > |)')
        self.replacement.textEdited.connect(self._edited)

        form = QFormLayout()
        form.addRow("Masks (first match wins):", masks_row)
        form.addRow("", self.example)
        form.addRow("Replace invalid characters with:", self.replacement)
        self.body.addLayout(form)
        self.reset_button = QPushButton("Reset to defaults")
        self.reset_button.setToolTip("Put back the default masks and replacement")
        self.reset_button.clicked.connect(self.reset)
        self.buttons.addStretch(1)
        self.buttons.addWidget(self.reset_button)
        self.set_masks(masks.DEFAULT_MASKS, masks.DEFAULT_REPLACEMENT)

    # -- values --

    def mask_list(self) -> tuple[str, ...]:
        return tuple(self.masks.item(i).text() for i in range(self.masks.count()))

    def replacement_text(self) -> str:
        return self.replacement.text()

    def set_masks(self, mask_list, replacement: str) -> None:
        self._loading = True
        self.masks.clear()
        for mask in mask_list:
            self._new_item(mask)
        self.replacement.setText(replacement)
        self._loading = False
        self._edited()

    def reset(self) -> None:
        self.set_masks(masks.DEFAULT_MASKS, masks.DEFAULT_REPLACEMENT)

    def problems_list(self) -> list[str]:
        return masks.validate_masks(self.mask_list(), self.replacement_text())

    @property
    def valid(self) -> bool:
        return not self.problems_list()

    # -- the live example --

    def set_example(self, fields: dict | None, extension: str = "") -> None:
        """The first file's rename fields (core.tags rename_fields), for the live example."""
        self._example_fields, self._example_ext = fields, extension
        self._update_example()

    def _update_example(self) -> None:
        if self._example_fields is None:
            self.example.setText("")
            return
        try:
            name = masks.name_for(self._example_fields, self.mask_list(), self.replacement_text(), self._example_ext)
        except (ValueError, KeyError, IndexError, TypeError):
            name = None
        self.example.setText("Example: the first file fits no mask and keeps its name." if name is None
                             else f"Example: {name}")

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
        self.show_problems(self.problems_list())
        self.remove_mask.setEnabled(self.masks.count() > 0)
        self._update_example()
        self.changed.emit()
