"""The ReplayGain card (SPEC §10 B, §30): options and validation. The scope is the entry's (SPEC §35).

The cards only collect options and validate them (core.replaygain.validate). Run (ReplayGain) and Preview rename
(Tidy) stay disabled until they pass, and while rsgain is unavailable. The job and the preview come from the
window.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from music_desk.core import replaygain
from music_desk.core.replaygain import ReplayGainOptions
from music_desk.ui.tools.rename_card import Card

DESCRIPTION = (
    "Measures loudness with rsgain at the target level, lowers any track gain that would clip, sets the album "
    "gain to the lowest track gain and the album peak to the highest track peak, and writes 'LUFS Target : X' in "
    "the comment. It then copies the album values into the lite/bonus/CD2 subfolders, plus the track values into "
    "'lite' subfolders for files with the same name. Its report replaces replaygain.log in the album folder.")


def parse_keywords(text: str) -> tuple[str, ...]:
    """"lite, bonus, disc 2" -> the keywords (they may hold spaces, so commas separate them)."""
    return tuple(part.strip() for part in text.split(",")) if text.strip() else ()


def format_keywords(keywords) -> str:
    return ", ".join(keywords)


class ReplayGainCard(Card):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("ReplayGain", DESCRIPTION, parent)
        self._loading = False
        self.unavailable: str | None = None  # why rsgain can't run, if it can't
        self.scope_note = QLabel()
        self.scope_note.setWordWrap(True)

        self.target = QDoubleSpinBox()
        self.target.setRange(*replaygain.TARGET_RANGE)
        self.target.setDecimals(1)
        self.target.setSingleStep(0.5)
        self.target.setSuffix(" LUFS")
        self.target.valueChanged.connect(self._edited)
        self.target.setToolTip("The loudness rsgain levels the tracks to, from -30 to -5 LUFS")
        self.target.setMaximumWidth(140)

        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0, 1)
        self.threshold.setDecimals(5)
        self.threshold.setSingleStep(0.00001)
        self.threshold.setToolTip("The highest peak a track may reach with its gain (1.0 is full scale); a gain "
                                  "that would go above it is lowered")
        self.threshold.valueChanged.connect(self._edited)
        self.margin = QDoubleSpinBox()
        self.margin.setRange(0, 6)
        self.margin.setDecimals(2)
        self.margin.setSingleStep(0.01)
        self.margin.setSuffix(" dB")
        self.margin.setToolTip("How far below the threshold a lowered gain leaves the peak")
        self.margin.valueChanged.connect(self._edited)
        self.threshold.setMaximumWidth(140)
        self.margin.setMaximumWidth(140)
        self.subfolders = QLineEdit()
        self.track_keywords = QLineEdit()
        self.excluded = QLineEdit()
        self.log_file = QLineEdit()
        self.comment = QLineEdit()
        self.subfolders.setToolTip("Subfolders whose name holds one of these words, separated by commas, get the "
                                   "album's values")
        self.track_keywords.setToolTip("In subfolders whose name holds one of these words, a file named like one "
                                       "of the album's also gets its track values")
        self.excluded.setToolTip("Subfolders whose name holds one of these words never get track values")
        self.log_file.setToolTip("The report written in each album folder; each run replaces it")
        self.comment.setToolTip("Written in each file's comment; {target} becomes the target loudness")
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
        form.addRow("Target loudness:", self.target)
        self.body.addLayout(form)
        self.body.addWidget(self.scope_note)
        self.body.addWidget(self.advanced_toggle(advanced))
        self.body.addWidget(advanced)

        self.reset_button = QPushButton("Restore defaults")
        self.reset_button.setToolTip("Put back this page's default settings")
        self.reset_button.clicked.connect(self.reset)
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

    def problems_list(self) -> list[str]:
        return replaygain.validate(self.options())

    @property
    def valid(self) -> bool:
        return not self.problems_list()

    def set_unavailable(self, reason: str | None) -> None:
        self.unavailable = reason
        self._edited()

    def _edited(self, *_args) -> None:
        if self._loading:
            return
        problems = self.problems_list()
        self.show_problems(problems)
        self.scope_note.setText(self.unavailable or "")
        self.scope_note.setVisible(bool(self.unavailable))
        self.changed.emit()

