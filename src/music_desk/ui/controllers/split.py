"""The cue split's controller (SPEC §22, §16): the Split page, its sheet, its saves and its job.

One page, one `.cue`: the side panel's "Split into tracks…" on a .cue preview opens it. The sheet is a
cue_edit.CueDocument; every edit is applied to it, validated at once on the UI thread (pure Python, no file read),
then planned again on the runner's worker after a short pause, so the lengths and new names follow. The app keeps
no split state: edits live only in the open document until they are saved into a .cue, and the ticks and options
start afresh each time the page opens.

Dialogs go through ask_* / confirm_* / choose_* methods, which tests replace.
"""

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QFileDialog, QMessageBox

from music_desk.core import fsops
from music_desk.core.cue import FRAMES_PER_SECOND, CueError, SourceInfo
from music_desk.core.cue_edit import (
    CueDocument,
    CueEncodingError,
    Problem,
    numbered_path,
    parse_document,
    save_cue,
    validate,
)
from music_desk.core.path_locks import LockedError
from music_desk.core.split import SplitOptions
from music_desk.services import jobs as jobs_module
from music_desk.services.split_job import Loaded, Planned, SplitFinished, SplitRunner
from music_desk.ui.split_page import SplitPage

log = logging.getLogger(__name__)

PLAN_DELAY_MS = 150  # after an edit, the plan follows once typing pauses
FIX_NAMES = "Fix the file names first (Tools page)."


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def short_result(result: SplitFinished) -> str:
    """One line for the jobs panel (after its state word)."""
    if result.error:
        return result.error
    if result.cancelled:
        return "nothing left half-done"
    if result.split:
        return f"{plural(len(result.finished.tracks), 'track')} split"
    return "not split"


def report_text(result: SplitFinished) -> str:
    plan = result.plan
    if result.error:
        return f"The split failed: {result.error}\nThe album is as it was."
    if result.cancelled:
        return "Stopped: nothing left half-done. The tracks being written were deleted; the album is as it was."
    if not result.split:
        reason = result.finished.error if result.finished else result.cut.error if result.cut else "not started"
        return f"Not split: {reason}\nNothing was written; the album is as it was."
    written, total = len(result.finished.tracks), len(plan.tracks)
    if plan.all_ticked:
        lines = [f"Done: {plural(written, 'track')} split, all identical to the original."]
    else:
        lines = [f"Done: {written} of {total} tracks split, each identical to its part of the original."]
    lines += ["", "Written:"] + [f"    {path.name}" for path in result.finished.tracks]
    lines += ["", "The proof: each track has its planned number of samples, and its audio has the same MD5 as its "
                  "part of the original."]
    if result.cut.md5:
        lines.append(f"Together, the tracks have the original's MD5 ({result.cut.md5[:8]}…).")
    lines.append("")
    if result.finished.recycled:
        lines.append("The album file and its .cue are in the Recycle Bin.")
    elif result.finished.kept_reason:
        lines.append(f"The originals stayed: {result.finished.kept_reason}")
    else:
        lines.append("The album file and its .cue stay where they are.")
    lines += [f"Warning: {w}" for w in result.finished.warnings]
    if result.replaygain is not None:
        lines.append(f"ReplayGain: {result.replaygain.result()}")
    elif result.replaygain_note:
        lines.append(result.replaygain_note)
    return "\n".join(lines)


class SplitController(QObject):
    def __init__(self, window, locks, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.window = window
        self.page = SplitPage()
        self.runner = SplitRunner(locks, self)
        self.check_recycle = fsops.can_recycle  # replaced in tests with the recycler
        self.unavailable: str | None = None  # flac's self-check failed
        self.doc: CueDocument | None = None
        self.path: Path | None = None
        self.codec: str | None = None  # the encoding picked (None: Auto)
        self.auto_label: str | None = None  # what Auto chose when the sheet was read
        self.plan = None
        self.info: SourceInfo | None = None
        self.problems: list[Problem] = []
        self.ticked: dict[int, bool] = {}  # sheet row -> ticked; a hidden track is HIDDEN
        self.hidden_ticked = True
        self.hidden = False  # the last plan that had tracks found a hidden track (the table's row 0)
        self.job: int | None = None
        self._run_token: int | None = None
        self._load_token: int | None = None
        self._plan_token: int | None = None
        self._planning = False
        self._split_after_plan = False
        self._plan_timer = QTimer(self, singleShot=True, interval=PLAN_DELAY_MS)
        self._plan_timer.timeout.connect(self._request_plan)

        page = self.page
        page.backRequested.connect(self.back)
        page.doneRequested.connect(self.done)
        page.saveRequested.connect(self.save)
        page.saveAsRequested.connect(self.save_as)
        page.splitRequested.connect(self.split)
        page.stopRequested.connect(self.runner.cancel)
        page.encodingPicked.connect(self.pick_encoding)
        page.fileEdited.connect(lambda name: self._edit(lambda d: d.set_file(name)))
        page.albumEdited.connect(lambda key, value: self._edit(lambda d: d.set_album(key, value)))
        page.albumOtherEdited.connect(lambda text: self._edit(lambda d: d.set_album_other(text)))
        page.trackEdited.connect(lambda row, key, value: self._edit(lambda d: d.set_track(row, key, value)))
        page.trackOtherEdited.connect(lambda row, text: self._edit(lambda d: d.set_track_other(row, text)))
        page.tickToggled.connect(self._tick)
        page.addRequested.connect(self.add_track)
        page.deleteRequested.connect(self.delete_tracks)
        page.optionsChanged.connect(self._refresh_buttons)
        self.runner.loaded.connect(self._loaded)
        self.runner.planned.connect(self._planned)
        self.runner.progress.connect(self._progress)
        self.runner.finished.connect(self._finished)

    # -- opening and leaving --

    @property
    def showing(self) -> bool:
        return self.window.center.currentWidget() is self.page

    def open(self, path: str | Path) -> None:
        """"Split into tracks…" on a .cue preview. The same sheet comes back as it was left, unsaved edits
        included; another one asks first about those edits."""
        path = Path(path)
        if self.job is not None:
            job = self.window.jobs.get(self.job)
            self.window.toast.show_message(f"A split is still running in {job.folder}. Open another cue sheet when "
                                           "it has finished.")
            self.window.show_job(self.job)
            return
        if self.doc is not None and self.path is not None and self.page.state in ("editing", "loading") and \
                fsops.is_same_file(self.path, path):
            self.window.center.setCurrentWidget(self.page)
            return
        if not self.can_leave():
            return
        self.doc, self.path, self.codec, self.plan, self.info = None, path, None, None, None
        self.ticked, self.hidden_ticked, self.hidden, self.problems = {}, True, False, []
        self.page.reset_options()
        self.page.set_encoding(None)
        self.page.show_loading(path)
        self._load_token = self.runner.load(path)
        self.window.center.setCurrentWidget(self.page)

    def can_leave(self) -> bool:
        """True if the page's sheet may be dropped: no unsaved edits, or they were saved or discarded."""
        if self.doc is None or not self.doc.modified or self.page.state != "editing":
            return True
        answer = self.ask_leave(self.path.name)
        if answer == "cancel":
            return False
        if answer == "save":
            return self.save()
        self._reload_saved()
        return True

    def back(self) -> None:
        if self.page.state != "editing" or self.can_leave():
            self.window.close_tools()

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
            self.page.show_error(loaded.error)
            return
        self.doc = loaded.document
        if self.codec is None:
            self.auto_label = self.doc.encoding
        self.ticked = {row: True for row in range(len(self.doc.tracks()))}
        self.hidden_ticked = True
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
            row = self.doc.add_track(after, frames)
        except CueError as e:
            self.window.toast.show_message(str(e))
            return
        ticks = [self.ticked.get(r, True) for r in range(len(self.doc.tracks()) - 1)]
        ticks.insert(row, True)
        self.ticked = dict(enumerate(ticks))
        self._refresh()

    def delete_tracks(self, rows: list[int]) -> None:
        if self.doc is None or not rows:
            return
        ticks = [t for r, t in sorted(self.ticked.items()) if r not in rows]
        self.doc.delete_tracks(rows)
        self.ticked = dict(enumerate(ticks))
        self._refresh()

    def _tick(self, number: int, on: bool) -> None:
        if self.doc is None:
            return
        if number == 0 and self.hidden:
            self.hidden_ticked = on
        else:
            for view in self.doc.tracks():
                if (view.number if view.number is not None else view.row + 1) == number:
                    self.ticked[view.row] = on
        self._refresh()

    def ticked_numbers(self) -> set[int]:
        numbers = {v.number if v.number is not None else v.row + 1
                   for v in self.doc.tracks() if self.ticked.get(v.row, True)} if self.doc else set()
        if self.hidden_ticked:
            numbers.add(0)
        return numbers

    def pick_encoding(self, codec) -> None:
        if self.doc is None or codec == self.codec:
            return
        if self.doc.modified and not self.confirm_discard_for_encoding():
            self.page.set_encoding(self.codec)
            return
        self.codec = codec
        self.page.set_encoding(codec)
        self.page.show_loading(self.path)
        self._load_token = self.runner.load(self.path, codec)

    # -- validation and the plan --

    def _refresh(self) -> None:
        """Validate now, show it, and plan again after a pause."""
        if self.doc is None:
            return
        self.problems = validate(self.doc, self.info)
        self.page.show_document(self.doc, self.path, self.auto_label)
        self.page.show_tracks(self.doc, self.plan, self.ticked_numbers(), self.problems, self.hidden)
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
        self._plan_token = self.runner.plan(sheet, self.path, masks, replacement, self.ticked_numbers())

    def _planned(self, planned: Planned) -> None:
        if planned.token != self._plan_token or self.doc is None:
            return
        self._planning = self._plan_timer.isActive()
        self.plan = planned.plan
        if self.plan.tracks:
            self.hidden = self.plan.tracks[0].row is None
        if self.plan.info is not None and self.plan.info != self.info:
            self.info = self.plan.info
            self.problems = validate(self.doc, self.info)  # a start past the end of the audio
        self.page.show_tracks(self.doc, self.plan, self.ticked_numbers(), self.problems, self.hidden)
        self._show_problems()
        self._refresh_buttons()
        if self._split_after_plan and not self._planning:
            self._split_after_plan = False
            self.split()

    def _show_problems(self) -> None:
        plan = self.plan
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
        # From the ticks themselves: a refused plan has no tracks to count.
        rows = len(self.doc.tracks()) + (1 if self.hidden else 0)
        ticked = sum(self.ticked.get(row, True) for row in range(len(self.doc.tracks()))) + (
            1 if self.hidden and self.hidden_ticked else 0)
        all_ticked = ticked == rows
        reasons = []
        if not self.window.tools_page.names_card.valid:
            reasons.append(FIX_NAMES)
        if self.unavailable:
            reasons.append(self.unavailable)
        if not sheet_ok:
            reasons.append("Fix the marked values first.")
        elif self.plan is not None and self.plan.refusal and self.plan.refusal != "No track is ticked.":
            reasons.append(f"Can't split: {self.plan.refusal}")
        if not ticked:
            reasons.append("Tick the tracks to write.")
        ready = (not reasons and self.plan is not None and not self._planning and self.plan.refusal is None
                 and ticked > 0 and self.job is None)
        self.page.set_buttons(
            save=sheet_ok and self.doc.modified, save_as=sheet_ok, split=ready,
            split_text=f"Split {plural(ticked, 'track')}",
            split_note=" ".join(reasons), recycle_enabled=all_ticked, replaygain_enabled=all_ticked,
            can_delete=bool(self.doc.tracks()))

    # -- saving --

    def save(self) -> bool:
        """Save over the sheet's own file. True once saved."""
        return self._save_to(self.path)

    def save_as(self) -> bool:
        if self.doc is None or any(p.blocks_save for p in self.problems):
            return False
        chosen = self.choose_cue_file(str(self.path))
        if not chosen:
            return False
        target = Path(chosen)
        if target.suffix.lower() != ".cue":
            target = target.with_name(target.name + ".cue")
        if target.exists() and not fsops.is_same_file(target, self.path):
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
            self.window.toast.show_message(f"{target.parent} is in use by {owner}. Save when it has finished.")
            return False
        overwrite = False
        while True:
            try:
                save_cue(self.doc, target, self.runner.recycle, self.check_recycle, overwrite_for_good=overwrite)
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
        moved = target != self.path
        self.path = target
        self.window.statusBar().showMessage(f"Saved {target.name}.", 8000)
        if self.window._library_open():
            self.window.refresh_after_tool(target.parent)
        self._refresh()
        if moved:
            self._request_plan()  # the page now works on the new file
        return True

    # -- the job --

    def options(self) -> SplitOptions:
        recycle = self.page.recycle.isChecked() and self.page.recycle.isEnabled()
        return SplitOptions(recycle=recycle, replaygain=recycle and self.page.replaygain.isChecked())

    def split(self) -> None:
        if self.doc is None or not self.page.split_button.isEnabled() and not self._split_after_plan:
            return
        if self.doc.modified:
            answer = self.ask_save_first(self.path.name)
            if answer == "cancel":
                return
            if not (self.save() if answer == "save" else self.save_as()):
                return
            if self._planning or self.plan is None or self.plan.cue != self.path:
                self._split_after_plan = True  # once the saved sheet is planned
                return
        plan = self.plan
        if plan is None or plan.refusal is not None or not self.window._library_open():
            return
        folder = plan.cue.parent
        if self.window._unsaved_edits_under(folder):
            return
        try:
            token = self.runner.run(plan, self.options(), self.window.tools_page.replaygain_card.options())
        except LockedError as e:
            self.window.toast.show_message(f"{e.path} is in use by {e.owner}. Try again when it has finished.")
            return
        self._run_token = token
        self.job = self.window._start_job(jobs_module.SPLIT, folder)
        self.window._pause_indexing(True)  # flac reads the whole album: the drive is the split's meanwhile
        self.page.show_running()
        self._refresh_buttons()

    def _progress(self, done: int, total: int, name: str) -> None:
        self.page.set_progress(done, total, name)
        if self.job is not None and name != "done":
            album = self.plan.cue.parent.name if self.plan is not None else ""
            self.window.jobs.update(self.job, status=f"Running ({done + 1} of {total}): {album}",
                                    progress=(done, total))

    def _finished(self, result: SplitFinished) -> None:
        if result.token != self._run_token:
            return
        self.window._pause_indexing(False)
        if self.window._library_open():
            self.window.refresh_after_tool(result.folder)  # new tracks, recycled originals
        self.page.show_result(report_text(result))
        if self.job is not None:
            self.window.jobs.report(self.job, "split", result)
            state = (jobs_module.FAILED if result.error else jobs_module.STOPPED if result.cancelled
                     else jobs_module.DONE)
            self.window.jobs.finish(self.job, state, short_result(result))
            self.job = None
        self.doc = None  # the page shows the report; the sheet is read again if opened again
        if result.split:
            self.window.statusBar().showMessage(f"Split {plural(len(result.finished.tracks), 'track')}.", 8000)

    def show_report(self, result: SplitFinished) -> None:
        """A finished split's report, from the jobs panel."""
        if self.page.state == "editing" and self.doc is not None and self.doc.modified and not self.can_leave():
            return
        self.page.show_result(report_text(result), f"<b>Split</b> · {result.plan.cue}")
        self.window.center.setCurrentWidget(self.page)

    def set_unavailable(self, reason: str | None) -> None:
        self.unavailable = reason
        self._refresh_buttons()

    def close(self) -> None:
        self.runner.close()  # a running flac is killed; this run's .partial files are deleted

    # -- dialogs (tests replace these) --

    def ask_leave(self, name: str) -> str:
        """'save', 'discard' or 'cancel'."""
        return self._ask("Unsaved changes", f"Save the changes to {name}?",
                         (("Save", "save"), ("Discard", "discard"), ("Cancel", "cancel")))

    def ask_save_first(self, name: str) -> str:
        """Split with unsaved edits: 'save', 'save_as' or 'cancel'. The tracks always match a .cue on disk."""
        return self._ask("Save the cue sheet first?",
                         f"The tracks are made from the cue sheet on disk. Save the changes to {name} first?",
                         (("Save", "save"), ("Save as…", "save_as"), ("Cancel", "cancel")))

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
