"""The "Split albums (cue)" card, last on the Tools page (SPEC §22): cut single-file albums into tracks.

The card only collects options; Run opens the preview, where each album is checked before anything is written.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QFormLayout, QLabel, QPushButton, QWidget

from music_desk.core.split import SplitOptions
from music_desk.ui.tools.rename_card import FIX_FIRST, MASKS_LINE, Card

DESCRIPTION = (
    "Cuts each single-file album (one audio file and its .cue) in this folder into one FLAC file per track, named "
    "with the File names masks and tagged from the cue sheet. The new files are checked to be identical to the "
    "original, sample for sample. The original files stay unless you choose otherwise.")
REPLAYGAIN_TIP = ("ReplayGain measures every audio file in the album's folder, so it would count the album file "
                  "too. It runs only once the originals are in the Recycle Bin, with the ReplayGain card's options.")


class SplitCard(Card):
    changed = Signal()
    runRequested = Signal()

    def __init__(self, names, parent: QWidget | None = None) -> None:
        """`names` is the File names card, whose masks name the tracks."""
        super().__init__("Split albums (cue)", DESCRIPTION, parent)
        self.names = names
        self.unavailable: str | None = None  # flac's self-check failed
        self.selection = False
        self._loading = False
        self.recursive = QCheckBox("Include subfolders")
        self.recycle = QCheckBox("Send the album file and its .cue to the Recycle Bin once the tracks are verified")
        self.replaygain = QCheckBox("Then run ReplayGain on each album")
        self.replaygain.setToolTip(REPLAYGAIN_TIP)
        for box in (self.recursive, self.recycle, self.replaygain):
            box.toggled.connect(self._edited)
        self.note = QLabel()
        self.note.setWordWrap(True)
        form = QFormLayout()
        form.addRow(QLabel(MASKS_LINE, wordWrap=True))
        for box in (self.recursive, self.recycle, self.replaygain):
            form.addRow("", box)
        self.body.addLayout(form)
        self.body.addWidget(self.note)
        self.run_button = QPushButton("Run split…")  # "…": the preview comes first
        self.run_button.clicked.connect(self.runRequested)
        self.buttons.addWidget(self.run_button)
        self.buttons.addStretch(1)
        names.changed.connect(self._edited)
        self.set_options(SplitOptions())

    def options(self) -> SplitOptions:
        return SplitOptions(recursive=self.recursive.isChecked(), recycle=self.recycle.isChecked(),
                            replaygain=self.recycle.isChecked() and self.replaygain.isChecked())

    def set_options(self, options: SplitOptions) -> None:
        self._loading = True
        self.recursive.setChecked(options.recursive)
        self.recycle.setChecked(options.recycle)
        self.replaygain.setChecked(options.replaygain)
        self._loading = False
        self._edited()

    def set_unavailable(self, reason: str | None) -> None:
        self.unavailable = reason
        self._edited()

    def set_selection(self, selection: bool) -> None:
        """The split works on folders: a file selection uses "Split into tracks…" on the album instead."""
        self.selection = selection
        self._edited()

    @property
    def valid(self) -> bool:
        return self.names.valid and not self.unavailable and not self.selection

    def _edited(self, *_args) -> None:
        self.replaygain.setEnabled(self.recycle.isChecked())
        self.show_problems([FIX_FIRST] if not self.names.valid else [])
        notes = [n for n in (self.unavailable, "The split works on folders. To split one album, use Split into "
                             "tracks… on its .cue." if self.selection else None) if n]
        self.note.setText("\n".join(notes))
        self.note.setVisible(bool(notes))
        self.run_button.setEnabled(self.valid)
        if not self._loading:
            self.changed.emit()
