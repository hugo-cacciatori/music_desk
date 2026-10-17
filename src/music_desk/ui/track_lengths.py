"""Fill from track lengths… (SPEC §22, "M13 additions"): a small modal dialog beside the Split page's Add / Delete
track. A tracklist (a back cover, Discogs) gives each track's length, while the sheet needs each start: the user
types the lengths, and OK sets the starts. The page itself stays one-to-one with the cue grammar (Length is
computed there), so this helper works in other terms in its own window (Hugo, 2026-10-17).

Tests never open it: the controller's `ask_track_lengths` is replaced.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from music_desk.core.cue_edit import check_lengths, format_length

MAX_TRACKS = 99  # what a cue sheet numbers


class TrackLengthsDialog(QDialog):
    def __init__(self, lengths: list[int | None], first_start: int, audio_frames: int,
                 parent: QWidget | None = None) -> None:
        """`lengths`: the current tracks' lengths in frames but the last's (None where a start isn't valid)."""
        super().__init__(parent)
        self.setWindowTitle("Fill from track lengths")
        self.setModal(True)
        self.setFixedSize(380, 460)
        self.first_start, self.audio_frames = first_start, audio_frames
        self.current_tracks = len(lengths) + 1
        self.result_lengths: list[int] | None = None
        self.edits: list[QLineEdit] = []
        self._typed = [format_length(n) if n is not None else "" for n in lengths]  # kept when the count shrinks

        intro = QLabel("Type each track's length, as m:ss or m:ss:ff. Track 1 keeps its start; each next track "
                       "starts where the one before ends.")
        intro.setWordWrap(True)
        self.count = QSpinBox()
        self.count.setRange(2, MAX_TRACKS)
        self.count.setValue(max(2, self.current_tracks))
        self.count.valueChanged.connect(self._rebuild)
        count_row = QFormLayout()
        count_row.addRow("Tracks:", self.count)

        self.rows = QWidget()
        self.form = QFormLayout(self.rows)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.rows)
        self.rest = QLabel()
        self.deleted = QLabel()
        self.reason = QLabel()
        self.reason.setWordWrap(True)
        self.reason.setStyleSheet("color: #c42b1c;")
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(count_row)
        layout.addWidget(scroll, 1)
        layout.addWidget(self.rest)
        layout.addWidget(self.deleted)
        layout.addWidget(self.reason)
        layout.addWidget(self.buttons)
        self._rebuild()

    def _rebuild(self) -> None:
        for i, edit in enumerate(self.edits):
            if i < len(self._typed):
                self._typed[i] = edit.text()
        while self.form.rowCount():
            self.form.removeRow(0)
        self.edits = []
        for i in range(self.count.value() - 1):
            edit = QLineEdit(self._typed[i] if i < len(self._typed) else "")
            edit.setPlaceholderText("m:ss")
            edit.textChanged.connect(self._check)
            self.form.addRow(f"Track {i + 1}:", edit)
            self.edits.append(edit)
        count = self.count.value()
        if count < self.current_tracks:
            first, last = count + 1, self.current_tracks
            self.deleted.setText(f"Track {first} will be deleted." if first == last
                                 else f"Tracks {first}–{last} will be deleted.")
        else:
            self.deleted.setText("")
        self.deleted.setVisible(bool(self.deleted.text()))
        self._check()

    def values(self) -> list[str]:
        return [edit.text() for edit in self.edits]

    def _check(self) -> None:
        lengths, why = check_lengths(self.values(), self.first_start, self.audio_frames)
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(lengths is not None)
        self.reason.setText(why)
        last = self.count.value()
        if lengths is not None:
            rest = self.audio_frames - self.first_start - sum(lengths)
            self.rest.setText(f"Track {last}: the rest, {format_length(rest)}.")
        else:
            self.rest.setText(f"Track {last}: the rest of the audio.")
        self.result_lengths = lengths

    def _accept(self) -> None:
        if self.result_lengths is not None:
            self.accept()


def ask(parent: QWidget, lengths: list[int | None], first_start: int, audio_frames: int) -> list[int] | None:
    dialog = TrackLengthsDialog(lengths, first_start, audio_frames, parent)
    dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
    return dialog.result_lengths if dialog.exec() == QDialog.DialogCode.Accepted else None
