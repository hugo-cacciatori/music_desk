"""The cue editor's controller (SPEC §22, §16, §35): the "Edit this cue sheet…" window's page, its sheet and its
saves. The split itself is "Split this cue sheet…"'s recap window (context_menu/split_recap.py), which takes
`report_text` from here.

One window, one `.cue` (or a new sheet for an audio file, New cue sheet…). The sheet is a cue_edit.CueDocument;
every edit is applied to it, validated at once on the UI thread (pure Python, no file read), then planned again on
the runner's worker after a short pause, so the lengths and new names follow, and Preview in player plays it. The
app keeps no split state: edits live only in the open document until they are saved into a .cue.

Dialogs go through ask_* / confirm_* / choose_* methods, which tests replace.
"""

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QFileDialog, QMessageBox

from refrain.core import fsops, playlists, split
from refrain.core.cue import FRAMES_PER_SECOND, SUPPORTED, CueError, SourceInfo, frames_from_time
from refrain.core.cue_edit import (
    CueDocument,
    CueEncodingError,
    Problem,
    numbered_path,
    parse_document,
    save_cue,
    validate,
)
from refrain.core.path_locks import busy_text
from refrain.services.split_job import Loaded, Planned, SplitFinished, SplitRunner
from refrain.ui import track_lengths
from refrain.ui.controllers import NO_CUE_PLAYER
from refrain.ui.split_page import SplitPage

log = logging.getLogger(__name__)

PLAN_DELAY_MS = 150  # after an edit, the plan follows once typing pauses


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def report_text(result: SplitFinished) -> str:
    plan = result.plan
    if result.error:
        return f"The split failed: {result.error}\nThe album is as it was."
    if result.cancelled:
        return "Stopped: nothing left half-done. The tracks being written were deleted; the album is as it was."
    finished = result.finished
    if not result.split or finished is None:
        reason = finished.error if finished else result.cut.error if result.cut else "not started"
        return f"Not split: {reason}\nNothing was written; the album is as it was."
    written, total = len(finished.tracks), len(plan.tracks)
    if plan.all_ticked:
        lines = [f"Done: {plural(written, 'track')} split, all identical to the original."]
    else:
        lines = [f"Done: {written} of {total} tracks split, each identical to its part of the original."]
    lines += ["", f"Written to {result.folder}:"] + [f"    {path.name}" for path in finished.tracks]
    lines += ["", "The proof: each track has its planned number of samples, and its audio has the same MD5 as its "
                  "part of the original."]
    if result.cut is not None and result.cut.md5:
        lines.append(f"Together, the tracks have the original's MD5 ({result.cut.md5[:8]}…).")
    lines += ["", "The image and its cue sheet are as they were."]
    lines += [f"Warning: {w}" for w in finished.warnings]
    return "\n".join(lines)


class SplitController(QObject):
    def __init__(self, window, locks, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.window = window
        self.page = SplitPage()
        self.runner = SplitRunner(locks, self)
        self.recycle = fsops.send_to_recycle_bin  # a saved-over .cue goes there; replaced in tests (a recorder)
        self.check_recycle = fsops.can_recycle  # replaced in tests with the recorder
        self.doc: CueDocument | None = None
        self.path: Path | None = None  # for a new sheet, the <stem>.cue it would be saved as
        self.new = False  # New cue sheet…: not on disk until saved
        self.codec: str | None = None  # the encoding picked (None: Auto)
        self.audio: tuple[str, ...] = ()  # the audio files beside the sheet (the source picker)
        self.auto_label: str | None = None  # what Auto chose when the sheet was read
        self.plan = None
        self.info: SourceInfo | None = None
        self.problems: list[Problem] = []
        self.hidden = False  # the last plan that had tracks found a hidden track (the table's row 0)
        self._load_token: int | None = None
        self._plan_token: int | None = None
        self._planning = False
        self._plan_timer = QTimer(self, singleShot=True, interval=PLAN_DELAY_MS)
        self._plan_timer.timeout.connect(self._request_plan)

        page = self.page
        page.doneRequested.connect(self.done)
        page.saveRequested.connect(self.save)
        page.saveAsRequested.connect(self.save_as)
        page.encodingPicked.connect(self.pick_encoding)
        page.sourcePicked.connect(lambda name: self._edit(lambda d: d.set_file(name)) if name else None)
        page.albumEdited.connect(lambda key, value: self._edit(lambda d: d.set_album(key, value)))
        page.albumOtherEdited.connect(lambda text: self._edit(lambda d: d.set_album_other(text)))
        page.trackEdited.connect(lambda row, key, value: self._edit(lambda d: d.set_track(row, key, value)))
        page.trackOtherEdited.connect(lambda row, text: self._edit(lambda d: d.set_track_other(row, text)))
        page.addRequested.connect(self.add_track)
        page.deleteRequested.connect(self.delete_tracks)
        page.fillRequested.connect(self.fill_from_lengths)
        page.previewRequested.connect(self.preview)
        self._audio_name = ""
        # Save's note follows the Options window's Split box, even while a new sheet is open.
        window.tools.cards.changed.connect(
            lambda: self.page.set_new(True, self._audio_name, window.settings.save_cue_beside) if self.new else None)
        self.runner.loaded.connect(self._loaded)
        self.runner.planned.connect(self._planned)

    # -- opening and leaving --

    def open(self, path: str | Path) -> None:
        """The sheet at `path`, read on the worker."""
        path = Path(path)
        self._start(path, new=False)
        self._load_token = self.runner.load(path)

    def new_sheet(self, audio: str | Path) -> None:
        """New cue sheet… on the Metadata tab: a new, unsaved sheet for this audio file, filled from its tags
        (SPEC §22)."""
        audio = Path(audio)
        self._start(audio.with_suffix(".cue"), new=True, audio_name=audio.name)
        self._load_token = self.runner.load_new(audio)

    def _start(self, path: Path, new: bool, audio_name: str = "") -> None:
        self.doc, self.path, self.codec, self.plan, self.info = None, path, None, None, None
        self.hidden, self.problems = False, []
        self.audio, self.new = (), new
        self.page.set_encoding(None)
        self.page.show_loading(path)
        self._audio_name = audio_name
        self.page.set_new(new, audio_name, self.window.settings.save_cue_beside)

    @property
    def cue_path(self) -> Path:
        """The sheet's file, for code that runs only once one was opened."""
        assert self.path is not None
        return self.path

    def can_leave(self) -> bool:
        """True if the page's sheet may be dropped: no unsaved edits, or they were saved or discarded."""
        if self.doc is None or not self.doc.modified or self.page.state != "editing":
            return True
        answer = self.ask_leave(self.cue_path.name)
        if answer == "cancel":
            return False
        if answer == "save":
            return self.save()
        self._reload_saved()
        return True

    def done(self) -> None:
        self.doc, self.plan = None, None
        self.page.show_error("")
        self.window.close_tools()

    def _reload_saved(self) -> None:
        """Discard: the document as it is on disk (the page keeps showing it if it stays open)."""
        if self.doc is not None and self.doc.saved is not None:
            self.doc = parse_document(self.doc.saved, self.codec, self.path)
            self._refresh()

    def _loaded(self, loaded: Loaded) -> None:
        if loaded.token != self._load_token:
            return
        if loaded.document is None:
            self._split_now = False
            self.page.show_error(loaded.error or "")
            return
        self.doc = loaded.document
        self.audio = loaded.audio
        if self.codec is None:
            self.auto_label = self.doc.encoding
        self._refresh()

    # -- edits --

    def _edit(self, change) -> None:
        if self.doc is None or self.page.state != "editing":
            return
        change(self.doc)
        self._refresh()

    def add_track(self, after: int | None) -> None:
        if self.doc is None:
            return
        frames = None
        if self.info is not None:
            frames = self.info.samples * FRAMES_PER_SECOND // self.info.rate
        try:
            self.doc.add_track(after, frames)
        except CueError as e:
            self.window.toast.show_message(str(e))
            return
        self._refresh()

    def delete_tracks(self, rows: list[int]) -> None:
        if self.doc is None or not rows:
            return
        self.doc.delete_tracks(rows)
        self._refresh()

    def track_numbers(self) -> set[int]:
        """Every track's number, a hidden track's 0 too: the plan covers them all."""
        numbers = {v.number if v.number is not None else v.row + 1 for v in self.doc.tracks()} if self.doc else set()
        return numbers | {0}

    def pick_encoding(self, codec) -> None:
        if self.doc is None or codec == self.codec:
            return
        if self.doc.modified and not self.confirm_discard_for_encoding():
            self.page.set_encoding(self.codec)
            return
        self.codec = codec
        self.page.set_encoding(codec)
        self.page.show_loading(self.cue_path)
        self._load_token = self.runner.load(self.cue_path, codec)

    def _show_sources(self) -> None:
        assert self.doc is not None  # called once a sheet is shown
        current = self.doc.file
        problem = None
        if current and Path(current).suffix.lower() not in SUPPORTED:
            problem = "not supported"
        elif current and not any(n.casefold() == current.casefold() for n in self.audio):
            problem = "not found"
        self.page.show_sources(self.audio, current, problem)

    # -- validation and the plan --

    def _refresh(self) -> None:
        """Validate now, show it, and plan again after a pause."""
        if self.doc is None:
            return
        self.problems = validate(self.doc, self.info)
        self.page.show_document(self.doc, f"{self.path} (new, not saved yet)" if self.new else self.path,
                                self.auto_label)
        self._show_sources()
        self.page.show_tracks(self.doc, self.plan, self.problems, self.hidden)
        self._show_problems()
        self._planning = True
        self._plan_timer.start()
        self._refresh_buttons()

    def _request_plan(self) -> None:
        if self.doc is None:
            return
        try:
            sheet = self.doc.sheet()
        except CueError:
            self._planning = False  # validate() marks the line; nothing to plan until it is fixed
            self.plan = None
            self._refresh_buttons()
            return
        masks, replacement = self.window._name_masks()
        self._plan_token = self.runner.plan(sheet, self.cue_path, masks, replacement, self.track_numbers(), None)

    def _planned(self, planned: Planned) -> None:
        if planned.token != self._plan_token or self.doc is None:
            return
        self._planning = self._plan_timer.isActive()
        self.plan = planned.plan
        if planned.audio != self.audio:
            self.audio = planned.audio
            self._show_sources()
        if self.plan.tracks:
            self.hidden = self.plan.tracks[0].row is None
        if self.plan.info is not None and self.plan.info != self.info:
            self.info = self.plan.info
            self.problems = validate(self.doc, self.info)  # a start past the end of the audio
        self.page.show_tracks(self.doc, self.plan, self.problems, self.hidden)
        self._show_problems()
        self._refresh_buttons()

    def source_warning(self) -> str | None:
        """Why the sheet's source can't be used, for the Source control's mark (SPEC §22): several files, none,
        a missing or unsupported one, or one that can't be read. None when it is usable (or not known yet)."""
        if self.doc is None:
            return None
        current = self.doc.file
        if self.doc.file_count > 1:
            return "This sheet names several files: the split needs one audio file for the whole album."
        if not current:
            return "This sheet names no audio file. Choose its audio file."
        if Path(current).suffix.lower() not in SUPPORTED:
            return (f"This sheet points to {current}, which the split can't read (it reads FLAC, WAV and AIFF). "
                    "Choose its audio file.")
        if not any(n.casefold() == current.casefold() for n in self.audio):
            return f"This sheet points to {current}, which isn't in this folder. Choose its audio file."
        plan = self.plan
        if plan is not None and plan.source is not None and plan.info is None and plan.refusal:
            return f"{plan.refusal} Choose its audio file."
        return None

    def _show_problems(self) -> None:
        plan = self.plan
        warning = self.source_warning()
        if warning:
            self.page.show_problems(self.problems, plan, warning, True)
            return
        if plan is not None and plan.info is not None and plan.source is not None:
            info = plan.info
            seconds = info.samples // info.rate if info.rate else 0
            text = f"{plan.source.name}: {info.format}, {info.rate / 1000:g} kHz, {seconds // 60}:{seconds % 60:02d}"
            bad = False
        elif plan is not None and plan.refusal:
            text, bad = plan.refusal, True
        else:
            text, bad = "", False
        self.page.show_problems(self.problems, plan, text, bad)

    def _refresh_buttons(self) -> None:
        if self.doc is None:
            return
        sheet_ok = not any(p.blocks_save for p in self.problems)
        # A new sheet can be saved once it could be split, as far as the sheet itself goes (Hugo, 2026-10-17).
        save = sheet_ok and (self.source_warning() is None if self.new else self.doc.modified)
        plan = self.plan
        preview = (sheet_ok and plan is not None and not self._planning and plan.info is not None
                   and bool(plan.tracks) and self.source_warning() is None)
        self.page.set_buttons(save=save, save_as=sheet_ok, can_delete=bool(self.doc.tracks()), preview=preview,
                              fill=self.info is not None and bool(self.doc.tracks()))

    # -- saving --

    def save(self) -> bool:
        """Save over the sheet's own file. A new sheet is saved as `<stem>.cue` beside its audio file: at once,
        numbered when the name is taken, with the Save-beside box; else through the Save dialog. True once saved."""
        if not self.new:
            return self._save_to(self.cue_path)
        if self.window.settings.save_cue_beside:
            if self.doc is None or any(p.blocks_save for p in self.problems):
                return False
            return self._save_to(numbered_path(self.cue_path))  # never replaces a file
        return self.save_as()

    def save_as(self) -> bool:
        if self.doc is None or any(p.blocks_save for p in self.problems):
            return False
        chosen = self.choose_cue_file(str(self.cue_path))
        if not chosen:
            return False
        target = Path(chosen)
        if target.suffix.lower() != ".cue":
            target = target.with_name(target.name + ".cue")
        if not fsops.is_same_file(target.parent, self.cue_path.parent):
            # A cue sheet is useless without its audio file (Hugo, 2026-10-17): its FILE line names a file beside it.
            self.window.toast.show_message(f"A cue sheet must sit beside its audio file: save it in "
                                           f"{self.cue_path.parent}.")
            return False
        if target.exists():  # the sheet's own name too: Keep both is how a version is made
            answer = self.ask_replace_or_keep(target.name)
            if answer == "cancel":
                return False
            if answer == "keep":
                target = numbered_path(target)
        return self._save_to(target)

    def _save_to(self, target: Path) -> bool:
        if self.doc is None or any(p.blocks_save for p in self.problems):
            return False
        owner = self.runner.locks.owner(target.parent)
        if owner is not None:
            self.window.toast.show_message(busy_text(target.parent, owner))
            return False
        overwrite = False
        while True:
            try:
                save_cue(self.doc, target, self.recycle, self.check_recycle, overwrite_for_good=overwrite)
                break
            except CueEncodingError as e:
                if not self.confirm_utf8(e.encoding, e.characters):
                    return False
                self.doc.use_utf8_bom()
                self.codec = "utf-8"
                self.page.set_encoding("utf-8")
            except fsops.CannotRecycle as e:
                if overwrite or not self.confirm_overwrite_for_good(target.name, str(e)):
                    return False
                overwrite = True
            except OSError as e:
                self.window.toast.show_message(f"{target.name} couldn't be saved: {e.strerror or e}")
                return False
        moved = target != self.path or self.new
        self.path = target
        if self.new:
            self.new = False
            self.page.set_new(False)
            self.auto_label = self.doc.encoding
        self.window.statusBar().showMessage(f"Saved {target.name}.", 8000)
        if self.window._library_open():
            self.window.refresh_after_tool(target.parent)
        self._refresh()
        if moved:
            self._request_plan()  # the page now works on the new file
        return True

    # -- Preview in player --

    def preview(self) -> None:
        """The page's sheet as it is now, unsaved edits included, as a temporary cue sheet in the default player:
        every track the split would write (SPEC §22). Nothing in the library is written."""
        plan = self.plan
        if plan is None or plan.info is None or not plan.tracks or self._planning:
            return
        sheet = split.preview_sheet(plan)
        window = self.window

        def run() -> None:
            window.shell.launch(playlists.write_preview(sheet, window.shell.playlist_dir))

        window.shell.run(run, NO_CUE_PLAYER, str(sheet.audio), False)
        window.statusBar().showMessage(f"Previewing {self.cue_path.name} in the player…", 8000)

    # -- Fill from track lengths… --

    def fill_from_lengths(self) -> None:
        """The starts from typed lengths (SPEC §22): an ordinary unsaved edit."""
        if self.doc is None or self.info is None or self.page.state != "editing":
            return
        views = self.doc.tracks()
        try:
            first = frames_from_time(views[0].values["start"])
        except (CueError, IndexError):
            self.window.toast.show_message("Track 1 needs a valid start first.")
            return
        starts: list[int | None] = []
        for view in views:
            try:
                starts.append(frames_from_time(view.values["start"]))
            except CueError:
                starts.append(None)
        lengths = [b - a if a is not None and b is not None and b > a else None
                   for a, b in zip(starts, starts[1:], strict=False)]
        audio_frames = self.info.samples * FRAMES_PER_SECOND // self.info.rate
        result = self.ask_track_lengths(lengths, first, audio_frames)
        if result is None:
            return
        self.doc.fill_from_lengths(result)
        self._refresh()

    def close(self) -> None:
        self.runner.close()  # a running flac is killed; this run's .partial files are deleted

    # -- dialogs (tests replace these) --

    def ask_track_lengths(self, lengths: list[int | None], first_start: int, audio_frames: int) -> list[int] | None:
        """The lengths in frames of every track but the last, or None when cancelled (a modal dialog)."""
        return track_lengths.ask(self.window, lengths, first_start, audio_frames)

    def ask_leave(self, name: str) -> str:
        """'save', 'discard' or 'cancel'."""
        return self._ask("Unsaved changes", f"Save the changes to {name}?",
                         (("Save", "save"), ("Discard", "discard"), ("Cancel", "cancel")))

    def ask_replace_or_keep(self, name: str) -> str:
        """Save as onto an existing file: 'replace', 'keep' or 'cancel'."""
        return self._ask("The file exists", f"{name} already exists. Replace it (it goes to the Recycle Bin), or "
                                            "keep both (the new one is numbered)?",
                         (("Replace", "replace"), ("Keep both", "keep"), ("Cancel", "cancel")))

    def confirm_overwrite_for_good(self, name: str, reason: str) -> bool:
        answer = QMessageBox.question(
            self.window, "No Recycle Bin",
            f"Overwrite {name} for good? Its current version can't go to the Recycle Bin: {reason}.")
        return answer == QMessageBox.StandardButton.Yes

    def confirm_utf8(self, encoding: str, characters: list[str]) -> bool:
        answer = QMessageBox.question(
            self.window, "Characters that don't fit",
            f"{encoding} can't hold {' '.join(characters)}. Save the cue sheet as UTF-8 (with a BOM) instead?")
        return answer == QMessageBox.StandardButton.Yes

    def confirm_discard_for_encoding(self) -> bool:
        answer = QMessageBox.question(
            self.window, "Unsaved changes",
            "Reading the cue sheet with another encoding loses the unsaved changes. Go on?")
        return answer == QMessageBox.StandardButton.Yes

    def choose_cue_file(self, suggested: str) -> str:
        """Save as: a path, or '' when cancelled. The app asks about an existing file itself."""
        path, _filter = QFileDialog.getSaveFileName(
            self.window, "Save the cue sheet as", suggested, "Cue sheets (*.cue)",
            options=QFileDialog.Option.DontConfirmOverwrite)
        return path

    def _ask(self, title: str, text: str, buttons) -> str:
        box = QMessageBox(QMessageBox.Icon.Question, title, text, parent=self.window)
        added = {}
        for label, value in buttons:
            role = QMessageBox.ButtonRole.RejectRole if value == "cancel" else QMessageBox.ButtonRole.AcceptRole
            added[box.addButton(label, role)] = value
        box.exec()
        return added.get(box.clickedButton(), "cancel")
