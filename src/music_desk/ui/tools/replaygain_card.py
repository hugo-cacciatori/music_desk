"""The ReplayGain and Tidy cards of the Tools page (SPEC §10 B and C): options, scope and validation.

The cards only collect options and validate them (core.replaygain.validate). Run (ReplayGain) and Preview rename
(Tidy) stay disabled until they pass, and while rsgain is unavailable. The job and the preview come from the
window.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QWidget,
)

from music_desk.core import replaygain
from music_desk.core.replaygain import ReplayGainOptions
from music_desk.ui.tools.rename_card import Card

DESCRIPTION = (
    "Measures loudness with rsgain at the target level, lowers any track gain that would clip, sets the album "
    "gain to the lowest track gain and the album peak to the highest track peak, and writes 'LUFS Target : X' in "
    "the comment. It then copies the album values into the lite/bonus/CD2 subfolders, plus the track values into "
    "'lite' subfolders for files with the same name. A report is appended to replaygain.log in the album folder.")
DISCOGRAPHY_NOTE = (
    "Discography: every folder below that holds music is an album (its lite/bonus/CD2 subfolders go with it). "
    "Then every album is leveled to the lowest album gain, and a summary goes to replaygain.log in this folder.")
TIDY_DESCRIPTION = (
    "Renames the music files from their tags (the Rename card's options), then runs ReplayGain on the final names "
    "(the ReplayGain card's options). This is the script run without flags. The preview covers the rename step "
    "only: ReplayGain can't be previewed on names that are about to change.")
SELECTION_NOTE = ("ReplayGain works on album folders. Open the album folder (or the artist folder, for a "
                  "discography) to use it.")
ALBUM, DISCOGRAPHY = "album", "discography"


def parse_keywords(text: str) -> tuple[str, ...]:
    """"lite, bonus, disc 2" -> the keywords (they may hold spaces, so commas separate them)."""
    return tuple(part.strip() for part in text.split(",")) if text.strip() else ()


def format_keywords(keywords) -> str:
    return ", ".join(keywords)


def scope_buttons(owner: QWidget, changed) -> tuple[QButtonGroup, QWidget]:
    group = QButtonGroup(owner)
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    for scope, text in ((ALBUM, "This album"), (DISCOGRAPHY, "Discography")):
        button = QRadioButton(text)
        button.setProperty("scope", scope)
        group.addButton(button)
        layout.addWidget(button)
    layout.addStretch(1)
    group.buttons()[0].setChecked(True)
    group.buttonToggled.connect(lambda *_args: changed())
    return group, row


def checked_scope(group: QButtonGroup) -> str:
    return group.checkedButton().property("scope")



class NoWheelSpinBox(QDoubleSpinBox):
    """A number field the mouse wheel never changes (Hugo, 2026-10-15): scrolling the page over it used to change
    the value by accident. The wheel scrolls the page instead; type the value or use the arrows."""

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt's name
        event.ignore()

class ReplayGainCard(Card):
    changed = Signal()
    runRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("ReplayGain", DESCRIPTION, parent)
        self._loading = False
        self.unavailable: str | None = None  # why rsgain can't run, if it can't
        self.selection = False
        self.scope_group, scope_row = scope_buttons(self, self._edited)
        self.scope_note = QLabel()
        self.scope_note.setWordWrap(True)

        self.target = NoWheelSpinBox()
        self.target.setRange(*replaygain.TARGET_RANGE)
        self.target.setDecimals(1)
        self.target.setSingleStep(0.5)
        self.target.setSuffix(" LUFS")
        self.target.valueChanged.connect(self._edited)
        self.target.setMaximumWidth(140)

        self.threshold = NoWheelSpinBox()
        self.threshold.setRange(0, 1)
        self.threshold.setDecimals(5)
        self.threshold.setSingleStep(0.00001)
        self.threshold.valueChanged.connect(self._edited)
        self.margin = NoWheelSpinBox()
        self.margin.setRange(0, 6)
        self.margin.setDecimals(2)
        self.margin.setSingleStep(0.01)
        self.margin.setSuffix(" dB")
        self.margin.valueChanged.connect(self._edited)
        self.threshold.setMaximumWidth(140)
        self.margin.setMaximumWidth(140)
        self.subfolders = QLineEdit()
        self.track_keywords = QLineEdit()
        self.excluded = QLineEdit()
        self.log_file = QLineEdit()
        self.comment = QLineEdit()
        for edit in (self.subfolders, self.track_keywords, self.excluded, self.log_file, self.comment):
            edit.textEdited.connect(self._edited)
        advanced = QWidget()
        advanced_form = QFormLayout(advanced)
        advanced_form.setContentsMargins(0, 0, 0, 0)
        advanced_form.addRow("Safety threshold (peak):", self.threshold)
        advanced_form.addRow("Safety margin:", self.margin)
        advanced_form.addRow("Album-data subfolder keywords:", self.subfolders)
        advanced_form.addRow("Track-data keywords:", self.track_keywords)
        advanced_form.addRow("Excluded keywords:", self.excluded)
        advanced_form.addRow("Log file name:", self.log_file)
        advanced_form.addRow("Comment text:", self.comment)

        form = QFormLayout()
        form.addRow("Scope:", scope_row)
        form.addRow("Target loudness:", self.target)
        self.body.addLayout(form)
        self.body.addWidget(self.scope_note)
        self.body.addWidget(self.advanced_toggle(advanced))
        self.body.addWidget(advanced)

        self.run_button = QPushButton("Run ReplayGain")  # no preview: it starts the job
        self.run_button.clicked.connect(self.runRequested)
        self.reset_button = QPushButton("Reset to defaults")
        self.reset_button.clicked.connect(self.reset)
        self.buttons.addWidget(self.run_button)
        self.buttons.addStretch(1)
        self.buttons.addWidget(self.reset_button)
        self.set_options(replaygain.DEFAULT_OPTIONS)

    def options(self) -> ReplayGainOptions:
        return ReplayGainOptions(
            target=self.target.value(), safety_threshold=self.threshold.value(), safety_margin=self.margin.value(),
            subfolder_keywords=parse_keywords(self.subfolders.text()),
            track_data_keywords=parse_keywords(self.track_keywords.text()),
            track_data_excluded_keywords=parse_keywords(self.excluded.text()),
            log_file=self.log_file.text(), comment=self.comment.text())

    def set_options(self, options: ReplayGainOptions) -> None:
        self._loading = True
        self.target.setValue(options.target)
        self.threshold.setValue(options.safety_threshold)
        self.margin.setValue(options.safety_margin)
        self.subfolders.setText(format_keywords(options.subfolder_keywords))
        self.track_keywords.setText(format_keywords(options.track_data_keywords))
        self.excluded.setText(format_keywords(options.track_data_excluded_keywords))
        self.log_file.setText(options.log_file)
        self.comment.setText(options.comment)
        self._loading = False
        self._edited()

    def reset(self) -> None:
        self.set_options(replaygain.DEFAULT_OPTIONS)

    @property
    def scope(self) -> str:
        return checked_scope(self.scope_group)

    def problems_list(self) -> list[str]:
        return replaygain.validate(self.options())

    @property
    def valid(self) -> bool:
        return not self.problems_list()

    def set_unavailable(self, reason: str | None) -> None:
        self.unavailable = reason
        self._edited()

    def set_selection(self, selection: bool) -> None:
        self.selection = selection
        self._edited()

    def _edited(self, *_args) -> None:
        if self._loading:
            return
        problems = self.problems_list()
        self.show_problems(problems)
        notes = []
        if self.selection:
            notes.append(SELECTION_NOTE)
        elif self.scope == DISCOGRAPHY:
            notes.append(DISCOGRAPHY_NOTE)
        if self.unavailable:
            notes.append(self.unavailable)
        self.scope_note.setText("\n".join(notes))
        self.scope_note.setVisible(bool(notes))
        self.run_button.setEnabled(not problems and not self.unavailable and not self.selection)
        self.changed.emit()


class TidyCard(Card):
    """Rename, then ReplayGain on the final names, with the two cards' options (SPEC §10 C)."""

    previewRequested = Signal()

    def __init__(self, rename_card, replaygain_card: ReplayGainCard, parent: QWidget | None = None) -> None:
        super().__init__("Tidy album", TIDY_DESCRIPTION, parent)
        self.rename_card = rename_card
        self.replaygain_card = replaygain_card
        self.scope_group, scope_row = scope_buttons(self, self.update_state)
        self.note = QLabel()
        self.note.setWordWrap(True)
        form = QFormLayout()
        form.addRow("Scope:", scope_row)
        self.body.addLayout(form)
        self.body.addWidget(self.note)
        self.preview_button = QPushButton("Preview rename…")
        self.preview_button.clicked.connect(self.previewRequested)
        self.buttons.addWidget(self.preview_button)
        self.buttons.addStretch(1)
        rename_card.changed.connect(self.update_state)
        replaygain_card.changed.connect(self.update_state)
        self.update_state()

    @property
    def scope(self) -> str:
        return checked_scope(self.scope_group)

    def update_state(self, *_args) -> None:
        problems = []
        if not self.rename_card.valid:
            problems.append("The Rename card's options need fixing.")
        if not self.replaygain_card.valid:
            problems.append("The ReplayGain card's options need fixing.")
        self.show_problems(problems)
        notes = []
        if self.replaygain_card.selection:
            notes.append("Tidy works on album folders. " + SELECTION_NOTE.split(". ", 1)[1])
        elif self.scope == DISCOGRAPHY:
            notes.append("Discography: the rename runs on every subfolder, then ReplayGain levels every album.")
        if self.replaygain_card.unavailable:
            notes.append(self.replaygain_card.unavailable)
        self.note.setText("\n".join(notes))
        self.note.setVisible(bool(notes))
        self.preview_button.setEnabled(not problems and not self.replaygain_card.unavailable
                                       and not self.replaygain_card.selection)
