"""The Options window's Split, Trim and Browser pages (SPEC §12): the preferences that lived on their own pages
(the Split page's Save-beside box, the Trim page's subfolder field, the folder tree's menu entry) live here only.
Each page saves as it changes, as the tool cards do, and has Restore defaults.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QFormLayout, QLineEdit, QPushButton, QWidget

from music_desk.core import trim
from music_desk.ui.tools.rename_card import Card

RESTORE = "Restore defaults"
RESTORE_TIP = "Put back this page's default settings"


class _SettingsCard(Card):
    changed = Signal()

    def __init__(self, title: str, description: str, parent: QWidget | None = None) -> None:
        super().__init__(title, description, parent)
        self._loading = False
        self.form = QFormLayout()
        self.body.addLayout(self.form)
        self.reset_button = QPushButton(RESTORE)
        self.reset_button.setToolTip(RESTORE_TIP)
        self.reset_button.clicked.connect(self.reset)
        self.buttons.addStretch(1)
        self.buttons.addWidget(self.reset_button)

    def problems_list(self) -> list[str]:
        return []

    @property
    def valid(self) -> bool:
        return not self.problems_list()

    def _edited(self, *_args) -> None:
        if self._loading:
            return
        self.show_problems(self.problems_list())
        self.changed.emit()


class SplitCard(_SettingsCard):
    """§22: where Save puts a new cue sheet."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Split", "The cue split's page: how it saves a new cue sheet (New cue sheet… on an audio "
                                  "file).", parent)
        self.save_beside = QCheckBox("Save new cue sheets beside their audio file without asking")
        self.save_beside.setToolTip("Save puts a new sheet next to its audio file at once, named after it (numbered "
                                    "if the name is taken), without the Save dialog")
        self.save_beside.toggled.connect(self._edited)
        self.form.addRow(self.save_beside)
        self.set_value(False)

    def value(self) -> bool:
        return self.save_beside.isChecked()

    def set_value(self, on: bool) -> None:
        self._loading = True
        self.save_beside.setChecked(on)
        self._loading = False
        self._edited()

    def reset(self) -> None:
        self.set_value(False)


class TrimCard(_SettingsCard):
    """§23: the subfolder the trimmed copy goes in."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Trim", "Trimming one track: the copy goes in this subfolder of the track's folder, "
                                 "created if missing. The original is never changed.", parent)
        self.subfolder = QLineEdit()
        self.subfolder.setToolTip("The subfolder's name, the same for every track (lite by default)")
        self.subfolder.textEdited.connect(self._edited)
        self.form.addRow("Subfolder:", self.subfolder)
        self.set_value(trim.DEFAULT_SUBFOLDER)

    def value(self) -> str:
        return self.subfolder.text()

    def set_value(self, name: str) -> None:
        self._loading = True
        self.subfolder.setText(name)
        self._loading = False
        self._edited()

    def reset(self) -> None:
        self.set_value(trim.DEFAULT_SUBFOLDER)

    def problems_list(self) -> list[str]:
        why = trim.check_subfolder_name(self.value())
        return [why] if why else []


class BrowserCard(_SettingsCard):
    """§5: what the folder tree shows."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Browser", "The folder tree on the left.", parent)
        self.hide_without_music = QCheckBox("Hide folders without music")
        self.hide_without_music.setToolTip("Leave out of the folder tree the folders with no music in them or below")
        self.hide_without_music.toggled.connect(self._edited)
        self.form.addRow(self.hide_without_music)
        self.set_value(False)

    def value(self) -> bool:
        return self.hide_without_music.isChecked()

    def set_value(self, on: bool) -> None:
        self._loading = True
        self.hide_without_music.setChecked(on)
        self._loading = False
        self._edited()

    def reset(self) -> None:
        self.set_value(False)
