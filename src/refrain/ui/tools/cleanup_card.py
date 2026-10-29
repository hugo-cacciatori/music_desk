"""The Clean up card, the Options window's Clean up page (SPEC §32): core.cleanup's options, checked as typed.

Today's junk cleanup is the default (it left the Rename card); pictures, videos and clutter-only folders are off.
The picture and video lists are also what Tidy offers as clutter (SPEC §28).
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QFormLayout, QLineEdit, QPushButton, QWidget

from refrain.core import cleanup
from refrain.core.cleanup import CleanupOptions
from refrain.ui.tools.rename_card import Card, format_extensions, parse_extensions

DESCRIPTION = (
    "Clean up lists the files to send to the Recycle Bin in this folder (and its subfolders): junk files by type, "
    "and, if you tick them, pictures and videos beside the music and folders holding only pictures, videos or "
    "nothing. The list opens with every box checked: uncheck what to keep, then Delete selected. Music files, "
    "playlists, the ReplayGain log and the cue sheet of an album still in one file are never junk.")


class CleanupCard(Card):
    changed = Signal()  # the options changed (valid or not)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Clean up", DESCRIPTION, parent)
        self._loading = False
        self.recursive = QCheckBox("Include subfolders")
        self.recursive.setToolTip("Look in every subfolder too, not only in the folder itself")
        self.junk = QCheckBox("Junk files")
        self.junk.setToolTip("List the files of the junk types below")
        self.junk_extensions = QLineEdit()
        self.junk_extensions.setToolTip("Extensions, separated by spaces. Music files, playlists, pictures and "
                                        "videos can't be junk: pictures and videos have their own boxes.")
        self.pictures = QCheckBox("Pictures beside music")
        self.pictures.setToolTip("List the pictures in folders holding music, covers included")
        self.picture_extensions = QLineEdit()
        self.picture_extensions.setToolTip("What a picture is, for this box and for Tidy: extensions, separated "
                                           "by spaces")
        self.videos = QCheckBox("Videos beside music")
        self.videos.setToolTip("List the videos in folders holding music")
        self.video_extensions = QLineEdit()
        self.video_extensions.setToolTip("What a video is, for this box and for Tidy: extensions, separated by "
                                         "spaces (.mp4 is music)")
        self.clutter_folders = QCheckBox("Empty and clutter-only folders")
        self.clutter_folders.setToolTip("List each folder holding only pictures, videos or nothing (empty "
                                        "folders inside count as nothing) as one item, sent whole")
        for box in (self.recursive, self.junk, self.pictures, self.videos, self.clutter_folders):
            box.toggled.connect(self._edited)
        for field in (self.junk_extensions, self.picture_extensions, self.video_extensions):
            field.textEdited.connect(self._edited)

        form = QFormLayout()
        form.addRow("", self.recursive)
        form.addRow("", self.junk)
        form.addRow("Junk file types:", self.junk_extensions)
        form.addRow("", self.pictures)
        form.addRow("Picture file types:", self.picture_extensions)
        form.addRow("", self.videos)
        form.addRow("Video file types:", self.video_extensions)
        form.addRow("", self.clutter_folders)
        self.body.addLayout(form)

        self.reset_button = QPushButton("Restore defaults")
        self.reset_button.setToolTip("Put back this page's default settings")
        self.reset_button.clicked.connect(self.reset)
        self.buttons.addStretch(1)
        self.buttons.addWidget(self.reset_button)
        self.set_options(cleanup.DEFAULT_OPTIONS)

    def options(self) -> CleanupOptions:
        return CleanupOptions(
            recursive=self.recursive.isChecked(), junk=self.junk.isChecked(),
            junk_extensions=parse_extensions(self.junk_extensions.text()), pictures=self.pictures.isChecked(),
            picture_extensions=parse_extensions(self.picture_extensions.text()), videos=self.videos.isChecked(),
            video_extensions=parse_extensions(self.video_extensions.text()),
            clutter_folders=self.clutter_folders.isChecked())

    def set_options(self, options: CleanupOptions) -> None:
        self._loading = True
        self.recursive.setChecked(options.recursive)
        self.junk.setChecked(options.junk)
        self.junk_extensions.setText(format_extensions(options.junk_extensions))
        self.pictures.setChecked(options.pictures)
        self.picture_extensions.setText(format_extensions(options.picture_extensions))
        self.videos.setChecked(options.videos)
        self.video_extensions.setText(format_extensions(options.video_extensions))
        self.clutter_folders.setChecked(options.clutter_folders)
        self._loading = False
        self._edited()

    def reset(self) -> None:
        self.set_options(cleanup.DEFAULT_OPTIONS)

    def problems_list(self) -> list[str]:
        return cleanup.validate(self.options())

    @property
    def valid(self) -> bool:
        return not self.problems_list()

    def _edited(self, *_args) -> None:
        if self._loading:
            return
        self.show_problems(self.problems_list())
        self.changed.emit()
