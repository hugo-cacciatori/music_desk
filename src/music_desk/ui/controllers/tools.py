"""The tools' controller (SPEC §10, §16, §30): the toolbar's buttons and their hover texts, the Options window,
Rename with its preview, ReplayGain (at once, or after its recap), their jobs and reports, and Remove ReplayGain
tags. Tidy has its own controller (ui/controllers/tidy.py, SPEC
§28), which the toolbar's Tidy opens.

A tool runs one job at a time: while its job runs, its page shows that job instead. Rename is previewed and
applied on its runner; ReplayGain has no preview, but outside the habitual case (the open folder's own tracks, no
subfolder, no selection, This album) a recap window lists every file first.

Dialogs go through ask_* and confirm_* methods, which tests replace.
"""

import os
from pathlib import Path

from PySide6.QtCore import QObject

from music_desk.context_menu import request as menu
from music_desk.context_menu.remove_report import RemoveReport, ask_remove, split_tracks
from music_desk.core import rename
from music_desk.core.formats import AUDIO_EXTENSIONS
from music_desk.core.index import queries
from music_desk.core.index.store import path_of
from music_desk.core.path_locks import LockedError
from music_desk.core.replaygain import plural
from music_desk.services import jobs as jobs_module
from music_desk.services.rename_job import Applied, Planned, RenameRunner
from music_desk.services.replaygain_job import (
    ALBUM,
    ALBUMS,
    DISCOGRAPHY,
    LOOSE,
    TRACKS,
    Finished,
    Recapped,
    ReplayGainRunner,
)
from music_desk.ui.controllers import STATUS_MS
from music_desk.ui.tools.rename_preview import RenamePreview
from music_desk.ui.tools.rg_recap import RecapDialog
from music_desk.ui.tools.rg_report import ReplayGainView, short_result
from music_desk.ui.tools.tool_cards import (
    CLEANUP,
    RENAME,
    REPLAYGAIN,
    TIDY,
    ToolCards,
    ToolRequest,
    ToolScope,
)

TOOL_NAMES = {RENAME: "Rename", REPLAYGAIN: "ReplayGain", TIDY: "Tidy", CLEANUP: "Clean up"}

# The right-click menu's ReplayGain entries (SPEC §31) and the job each runs.
ENTRY_KINDS = {menu.REPLAYGAIN_ALBUM: ALBUM, menu.REPLAYGAIN_DISCOGRAPHY: DISCOGRAPHY,
               menu.REPLAYGAIN_ALBUMS: ALBUMS, menu.REPLAYGAIN_TRACKS: TRACKS, menu.REPLAYGAIN_LOOSE: LOOSE}


def entry_title(kind: str, targets: list[Path]) -> str:
    """The job page's heading for a right-click run, as its menu entry reads."""
    count = len(targets)
    if kind == ALBUMS and count > 1:
        return f"ReplayGain · {count} folders, each as an album"
    if kind == TRACKS:
        return f"ReplayGain · {plural(count, 'track')} as an album"
    if kind == LOOSE:
        return "ReplayGain · this track" if count == 1 else f"ReplayGain · each of {count} tracks on its own"
    return f"ReplayGain · {'Discography' if kind == DISCOGRAPHY else 'This album'}"


def rename_target_text(targets: list[Path]) -> str:
    """The rename preview's heading for a right-click Rename: what was clicked."""
    if len(targets) == 1:
        return str(targets[0])
    folders = sum(1 for path in targets if path.is_dir())
    parts = [plural(count, noun) for count, noun in ((folders, "folder"), (len(targets) - folders, "file")) if count]
    return f"{' and '.join(parts)} in {targets[0].parent}"


def entry_problem(kind: str, targets: list[Path]) -> str | None:
    """Why a right-click run can't start whatever the options: nothing clicked, or (as an album) tracks of several
    folders, which the menu doesn't offer but a run from the command line could ask for."""
    if not targets:
        return "nothing was clicked"
    if kind == TRACKS and len({path.parent for path in targets}) > 1:
        return "the tracks are in several folders, and an album is one folder's tracks"
    return None


class ToolsController(QObject):
    def __init__(self, window, locks, parent: QObject | None = None, cards: ToolCards | None = None) -> None:
        super().__init__(parent)
        self.window = window
        self.locks = locks
        # The options, in dialogs (SPEC §30); `cards`: those the right-click Settings entry already made (§31).
        self.cards = cards or ToolCards(window.settings, window)
        if cards is not None:
            cards.adopt(window)
        window.tag_io.renameFields.connect(self._tool_example)
        window.tag_writer.replaygainRemoved.connect(self._replaygain_removed)
        self._example_token: int | None = None
        self._rg_remove_token: int | None = None
        self._remove_reports: dict[int, tuple[RemoveReport, list[str]]] = {}  # right-click removals running

        self.rename_preview = RenamePreview()
        self.rename_preview.backRequested.connect(window.close_tools)
        self.rename_preview.doneRequested.connect(window.close_tools)
        self.rename_preview.applyRequested.connect(self.apply_rename)
        self.rename_preview.cancelRequested.connect(lambda: self.rename_runner.cancel())
        self.rename_runner = RenameRunner(locks, self)
        self.rename_runner.planned.connect(self._rename_planned)
        self.rename_runner.applied.connect(self._rename_applied)
        self._rename_token: int | None = None
        self.rename_job: int | None = None  # the job id of the running rename (or Tidy's rename step)

        self.rg_view = ReplayGainView()
        self.rg_view.backRequested.connect(window.close_tools)
        self.rg_view.doneRequested.connect(window.close_tools)
        self.rg_view.cancelRequested.connect(lambda: self.rg_runner.cancel())
        self.rg_view.openLogRequested.connect(self.open_log)
        self.rg_view.revealRequested.connect(lambda path: window._in_shell(
            lambda: window.show_in_explorer(path, True), f"File Explorer couldn't show {path}.", "", True))
        self.rg_runner = ReplayGainRunner(locks, self)
        self.rg_runner.lines.connect(self.rg_view.add_lines)
        self.rg_runner.progress.connect(self.rg_view.set_progress)
        self.rg_runner.progress.connect(self._rg_progress)
        self.rg_runner.finished.connect(self._rg_finished)
        self.rg_runner.recapped.connect(self._rg_recapped)
        self._rg_token: int | None = None
        self._recap_token: int | None = None
        self._recap_request: ToolRequest | None = None
        self._rg_request: ToolRequest | None = None
        self.rg_job: int | None = None

    @property
    def pages(self) -> tuple:
        """The centre pages this controller shows."""
        return self.rename_preview, self.rg_view

    def shutdown(self) -> None:
        self.rename_runner.close()  # a running rename stops between two files
        self.rg_runner.close()  # a running rsgain is killed; its album is reported as cancelled

    # -- the target and the toolbar --

    def scope_for(self, ids: list[int]) -> ToolScope | None:
        """What the tools act on: one folder (from the list, the tree or the folder summary); else the audio
        files among the selected items in the open folder (a selection); with nothing selected, the open folder."""
        window = self.window
        if not window._library_open():
            return None
        conn = window.open_library.conn
        kinds = {i: row[0] for i in ids
                 if (row := conn.execute("SELECT is_dir FROM entries WHERE id = ?", (i,)).fetchone())}
        if len(ids) == 1 and kinds.get(ids[0]):
            folder_id, files = ids[0], ()
        elif ids:
            if window.folder_id is None or window.folder_path is None:
                return None
            model = window.file_view.model
            rows = [model.rows[r] for i in ids if (r := model.row_for(i)) is not None]
            files = tuple(Path(window.folder_path, row.name) for row in rows if row.is_audio)
            if not files:
                return None
            folder_id = window.folder_id
        elif window.folder_id is not None:
            folder_id, files = window.folder_id, ()
        else:
            return None
        if not window._folder_exists(folder_id):
            return None
        folder = path_of(conn, folder_id)
        summary = None if files else queries.folder_summary(conn, folder_id)
        first = files[0] if files else summary.first_track if summary else None
        return ToolScope(folder, folder_id, files, Path(first) if first else None)

    def target(self, tool: str) -> ToolScope | None:
        """What the toolbar's Start acts on: the file list's selection, else the open folder. ReplayGain and Tidy
        take a file selection's folder (SPEC §30)."""
        scope = self.scope_for(self.window.file_view.selected_ids())
        if scope is not None and tool != RENAME and scope.is_selection:
            return ToolScope(scope.folder, scope.folder_id)
        return scope

    @staticmethod
    def target_text(scope: ToolScope) -> str:
        count = len(scope.files)
        if count:
            return f"{count} selected file{'s' if count != 1 else ''}"
        return scope.folder.name or str(scope.folder)

    def blocked(self, tool: str, scope: ToolScope | None) -> str | None:
        """Why a click can't start the tool (SPEC §30), said in the status bar and the hover text; None if it can."""
        if scope is None:
            return "open a folder first"
        cards = self.cards
        if tool in (REPLAYGAIN, TIDY) and cards.replaygain_card.unavailable:
            return "rsgain couldn't run"
        if cards.problems(tool):
            if tool in (RENAME, TIDY) and not cards.names_card.valid:
                which = "File names"
            elif tool in (RENAME, TIDY) and not cards.rename_card.valid:
                which = "Rename"
            elif tool in (REPLAYGAIN, TIDY) and not cards.replaygain_card.valid:
                which = "ReplayGain"
            else:
                which = "Clean up"
            return f"fix the {which} options first: File > Options…"
        return None

    def hover_text(self, tool: str) -> tuple[str, bool]:
        """The button's hover text, read as the mouse comes over it: the target, or why it can't start."""
        scope = self.target(tool)
        why = self.blocked(tool, scope)
        if why is not None or scope is None:
            return f"{TOOL_NAMES[tool]}: {why}", False
        name = self.target_text(scope)
        if tool == RENAME:
            if scope.is_selection:
                return f"Rename {name}", True
            below = " and its subfolders" if self.cards.rename_card.options().recursive else ""
            return f"Rename the music files in {name}{below}", True
        if tool == REPLAYGAIN:
            discography = self.cards.replaygain_card.scope == "discography"
            return f"ReplayGain on {name}{', as a discography' if discography else ''}", True
        if tool == CLEANUP:
            below = " and its subfolders" if self.cards.cleanup_card.options().recursive else ""
            return f"Clean up {name}{below}", True
        return f"Tidy {name} and its subfolders", True

    def start(self, tool: str, folder_id: int | None = None) -> None:
        """A toolbar button's click: the rename preview, ReplayGain (at once or after its recap), Tidy's plan, or
        Clean up's list; `folder_id`: a folder menu's folder instead of the target. A click that can't start says
        why in the status bar and starts nothing."""
        scope = self.target(tool) if folder_id is None else self.scope_for([folder_id])
        why = self.blocked(tool, scope)
        request = self.cards.request(tool, scope) if why is None and scope is not None else None
        if request is None:
            self.window.statusBar().showMessage(f"{TOOL_NAMES[tool]}: {why or 'its options need fixing'}.",
                                                STATUS_MS)
            return
        self.run_tool(request)

    def open_options(self, which: str | None = None) -> None:
        """The Options window, on `which`'s page. File names gets its live example from the first track of the
        target, whichever page it opens on (its page is a click away)."""
        scope = self.target(RENAME)
        first = scope.first_track if scope is not None else None
        self.cards.set_example(None, "")
        self._example_token = self.window.tag_io.read_rename_fields(first) if first else None
        self.cards.open_options(which)

    def open_options_on(self, paths: list[str]) -> None:
        """The right-click Settings entry, with MusicDesk open (SPEC §31): the Options window as it was, its File
        names example from the clicked items' first track; the main window stays where it is."""
        self.cards.set_example(None, "")
        self._example_token = self.window.tag_io.read_first_track_fields(paths)
        self.cards.open_options()

    def cancel_planning(self) -> None:
        """Leaving the rename preview while it plans: the plan isn't needed any more."""
        if self.rename_preview.state == "planning":
            self.rename_runner.cancel()

    def _tool_example(self, token: int, fields: dict | None, extension: str) -> None:
        if token == self._example_token:
            self.cards.set_example(fields, extension)

    def run_tool(self, request: ToolRequest, confirmed: bool = False) -> None:
        """A click: the rename preview, ReplayGain (`confirmed`: its recap was OKed), or Tidy's plan (its
        controller). A tool runs one job at a time: while its job runs, its page shows that job instead."""
        window = self.window
        if request.tool == TIDY:
            window.tidy.open(request)
            return
        if request.tool == CLEANUP:
            window.cleanup.open(request)
            return
        busy = None
        if request.tool == RENAME and self.rename_job is not None:
            busy = self.rename_job
        elif request.tool == REPLAYGAIN and self.rg_job is not None:
            busy = self.rg_job
        if busy is not None:
            job = window.jobs.get(busy)
            assert job is not None  # a running job stays in the list
            window.toast.show_message(f"{job.kind} is still running in {job.folder}. It can be followed in the jobs "
                                      "panel; start another run when it has finished.")
            window.show_job(busy)
            return
        if request.tool == RENAME:
            self.preview_rename(request)
        elif request.tool == REPLAYGAIN and confirmed:
            self.run_replaygain(request)  # no preview: it only writes tags and the logs (Hugo, 2026-10-15)
        elif request.tool == REPLAYGAIN:
            self.start_replaygain(request)

    # -- the jobs panel --

    def show_job(self, job_id: int, job) -> bool:
        """The page of one of this controller's jobs (live, or its report). False if it isn't one of them."""
        center = self.window.center
        if job_id == self.rename_job:
            center.setCurrentWidget(self.rename_preview)
        elif job_id == self.rg_job:
            center.setCurrentWidget(self.rg_view)
        elif "replaygain" in job.reports:
            self.rg_view.show_result(job.reports["replaygain"])
            self.rg_view.heading.setText(f"<b>{job.kind}</b> · {job.folder}")
            center.setCurrentWidget(self.rg_view)
        elif "rename" in job.reports:
            self.rename_preview.heading.setText(f"<b>{job.kind}</b> · {job.folder}")
            self.rename_preview.show_report(job.reports["rename"])
            center.setCurrentWidget(self.rename_preview)
        else:
            return False
        return True

    def stop_job(self, job_id: int) -> None:
        if job_id == self.rename_job:
            self.rename_runner.cancel()
        elif job_id == self.rg_job:
            self.rg_runner.cancel()

    # -- the ReplayGain tool (SPEC §10 B) --

    def start_replaygain(self, request: ToolRequest) -> None:
        """At once in the habitual case, else after the recap (SPEC §30). A blocked run shows no recap."""
        window = self.window
        folder = request.scope.folder
        owner = self.locks.conflict(folder)
        if owner is not None:
            window.statusBar().showMessage(f"ReplayGain: {folder} is in use by {owner}. Try again when it has "
                                           "finished.", STATUS_MS)
            return
        if self.habitual(request):
            self.run_replaygain(request)  # no preview: it only writes tags and the logs (Hugo, 2026-10-15)
            return
        self._recap_request = request
        assert request.replaygain_options is not None  # set for every ReplayGain request
        self._recap_token = self.rg_runner.recap(folder, request.replaygain_options, request.discography)
        window.statusBar().showMessage(f"ReplayGain: reading what the run will do in {folder}…", STATUS_MS)

    def habitual(self, request: ToolRequest) -> bool:
        """'ReplayGain this album': no selection, so the open folder, holding tracks and no subfolder, with the
        This album scope. Read from the index (the UI thread never lists a folder)."""
        window = self.window
        if request.discography or window.file_view.selected_ids() or request.scope.folder_id != window.folder_id:
            return False
        rows = window.open_library.conn.execute("SELECT is_dir, ext FROM entries WHERE parent_id = ?",
                                                (request.scope.folder_id,)).fetchall()
        # By extension: a folder just added has its entries before its tags are read.
        return not any(is_dir for is_dir, _ext in rows) and any(ext in AUDIO_EXTENSIONS for _is_dir, ext in rows)

    def _rg_recapped(self, result: Recapped) -> None:
        if result.token != self._recap_token or self._recap_request is None:
            return
        request, self._recap_token, self._recap_request = self._recap_request, None, None
        window = self.window
        recap = result.recap
        if recap is None:
            window.statusBar().showMessage(f"ReplayGain couldn't read {request.scope.folder}: {result.error}",
                                           STATUS_MS)
            return
        if not recap.measured:
            window.statusBar().showMessage(f"ReplayGain: there is nothing to measure in {request.scope.folder}.",
                                           STATUS_MS)
            return
        if self.confirm_replaygain(recap):
            self.run_tool(request, confirmed=True)  # a job may have started meanwhile: run_tool shows it instead

    def confirm_replaygain(self, recap) -> bool:
        """The recap window, OK or Cancel (replaced in tests)."""
        return RecapDialog(recap, self.window).exec() == RecapDialog.DialogCode.Accepted

    def run_replaygain(self, request: ToolRequest | None = None, title: str | None = None) -> bool:
        """Start the ReplayGain job (the card's Run). False when it didn't start."""
        window = self.window
        request = request or self._rg_request
        if request is None or not window._library_open():
            return False
        folder = request.scope.folder
        if window._unsaved_edits_under(folder):
            return False
        assert request.replaygain_options is not None  # set for every ReplayGain request
        try:
            self._rg_token = self.rg_runner.run(folder, request.replaygain_options, request.discography)
        except LockedError as e:
            window.toast.show_message(f"{e.path} is in use by {e.owner}. Try again when it has finished.")
            return False
        self._rg_request = request
        window._pause_indexing(True)  # rsgain gets the drive to itself; folders you open are still served
        self.rg_job = window._start_job(jobs_module.REPLAYGAIN, folder)
        self.rg_view.show_running(folder, title, request.discography)
        window.center.setCurrentWidget(self.rg_view)
        return True

    def run_entry(self, entry: str, paths: list[str]) -> bool:
        """A right-click ReplayGain entry (SPEC §31), with MusicDesk open: its run starts at once (no recap, as the
        tool), as this app's ReplayGain job, with the current options. The window comes to the front on the job's
        page. Rename: its preview (`rename_entry`). False when it didn't start (the status bar or a toast says
        why)."""
        if entry == menu.RENAME:
            return self.rename_entry(paths)
        if entry == menu.REMOVE_REPLAYGAIN:
            return self.remove_entry(paths)
        if entry == menu.TIDY:
            return self.window.tidy.open_paths(paths)
        if entry == menu.CLEANUP:
            return self.window.cleanup.open_paths(paths)
        if entry == menu.SPLIT:
            return self.window.split.open_entry(paths)
        if entry == menu.TRIM:
            return self.window.trim.open_entry(paths)
        window = self.window
        window.bring_to_front()
        kind = ENTRY_KINDS[entry]
        targets = [Path(path) for path in paths]
        problem = entry_problem(kind, targets) or next(iter(self.cards.problems(REPLAYGAIN)), None)
        if problem:
            window.statusBar().showMessage(f"ReplayGain: {problem}", STATUS_MS)
            return False
        if self.rg_job is not None:
            job = window.jobs.get(self.rg_job)
            assert job is not None  # a running job stays in the list
            window.toast.show_message(f"{job.kind} is still running in {job.folder}. Start the other run when it "
                                      "has finished.")
            window.show_job(self.rg_job)
            return False
        folders = sorted({p.parent for p in targets}) if kind in (TRACKS, LOOSE) else targets
        if any(window._unsaved_edits_under(folder) for folder in folders):
            return False
        options = self.cards.replaygain_card.options()
        try:
            self._rg_token = self.rg_runner.start(kind, targets, options)
        except LockedError as e:
            window.toast.show_message(f"{e.path} is in use by {e.owner}. Try again when it has finished.")
            return False
        self._rg_request = None  # the card's Run doesn't repeat a right-click run
        window._pause_indexing(True)
        self.rg_job = window._start_job(jobs_module.REPLAYGAIN, folders[0])
        self.rg_view.show_running(folders[0], entry_title(kind, targets), kind == DISCOGRAPHY,
                                  "Track" if kind == LOOSE else "Album")
        window.center.setCurrentWidget(self.rg_view)
        return True

    def _rg_progress(self, done: int, total: int, label: str) -> None:
        if self.rg_job is not None and label != "done":
            self.window.jobs.update(self.rg_job, status=label, progress=(done, total))

    def _rg_finished(self, finished: Finished) -> None:
        if finished.token != self._rg_token:
            return
        window = self.window
        window._pause_indexing(False)
        if window._library_open():
            for folder in finished.folders:
                window.refresh_after_tool(folder)  # new tags and replaygain.log files
        self.rg_view.show_result(finished)
        if self.rg_job is not None:
            window.jobs.report(self.rg_job, "replaygain", finished)
            state = jobs_module.FAILED if finished.error else jobs_module.STOPPED if finished.cancelled else \
                jobs_module.DONE
            window.jobs.finish(self.rg_job, state, short_result(finished))
            self.rg_job = None
        if not finished.error and not finished.nothing:
            albums = len(finished.album_logs)
            what = "stopped" if finished.cancelled else "done"
            window.statusBar().showMessage(f"ReplayGain {what}: {albums} album{'s' if albums != 1 else ''}.",
                                           STATUS_MS)

    def open_log(self, path: str) -> None:
        window = self.window
        window._in_shell(lambda: window.launch(path), f"Windows couldn't open {os.path.basename(path)}.",
                         os.path.dirname(path), True)

    # -- the Rename tool (SPEC §10 A) --

    def preview_rename(self, request: ToolRequest) -> None:
        scope = request.scope
        assert request.rename_options is not None  # set for every Rename request
        self.rename_preview.show_planning(scope.folder, len(scope.files))
        self.window.center.setCurrentWidget(self.rename_preview)
        self._rename_token = self.rename_runner.plan(scope.folder, request.rename_options,
                                                     list(scope.files) or None)

    def _rename_planned(self, result: Planned) -> None:
        if result.token != self._rename_token or result.cancelled:
            return
        if result.plan is None:
            self.rename_preview.show_error(f"The preview failed: {result.error}")
        else:
            self.rename_preview.show_plan(result.plan)

    def apply_rename(self) -> None:
        window = self.window
        plan = self.rename_preview.plan
        if plan is None or not window._library_open():
            return
        if any(window._unsaved_edits_under(tree) for tree in plan.trees):  # unsaved edits on a file to be renamed
            return
        # Junk listed in the preview goes to the Recycle Bin without a question (Hugo, 2026-10-17): the option is
        # ticked on the Rename card, and the preview names every file.
        try:
            self._rename_token = self.rename_runner.apply(plan)
        except LockedError as e:
            window.toast.show_message(f"{e.path} is in use by {e.owner}. Try again when it has finished.")
            return
        self.rename_job = window._start_job(jobs_module.RENAME, plan.folder)
        window.jobs.update(self.rename_job, status="Renaming")
        self.rename_preview.show_applying()

    def _rename_applied(self, result: Applied) -> None:
        if result.token != self._rename_token:
            return
        window = self.window
        if result.report is None:
            self.rename_preview.show_error(f"Renaming failed: {result.error}")
            if self.rename_job is not None:
                window.jobs.finish(self.rename_job, jobs_module.FAILED, f"Renaming failed: {result.error}")
                self.rename_job = None
            return
        report = result.report
        job = self.rename_job
        if job is not None:
            window.jobs.report(job, "rename", report)
        if window._library_open():
            by_folder: dict[Path, list] = {}
            for step in report.steps:  # the index applies renames one folder at a time
                by_folder.setdefault(Path(step[0]).parent, []).append(step)
            for steps in by_folder.values():
                window.open_library.indexer.apply_renames(steps)  # in place: ids, tags and selection kept
            plan = self.rename_preview.plan
            for tree in plan.trees if plan is not None else []:  # then the whole scope: sidecars, anything else
                window.refresh_after_tool(tree)
        self.rename_preview.show_report(report)
        count = len(report.renamed)
        window.statusBar().showMessage(f"Renamed {count} file{'s' if count != 1 else ''}.", STATUS_MS)
        summary = rename.summary_sections(report, dry_run=False)[0][0]
        if job is not None:
            window.jobs.finish(job, jobs_module.STOPPED if report.cancelled else jobs_module.DONE, summary)
            self.rename_job = None

    def rename_entry(self, paths: list[str]) -> bool:
        """The right-click Rename (SPEC §31), with MusicDesk open: one preview of every clicked folder (as the tool
        on a folder) and file (as a selection), in the main window, brought to the front. Apply then runs it as the
        app's Rename job. False when the preview didn't start (the status bar or a toast says why)."""
        window = self.window
        window.bring_to_front()
        targets = [Path(path) for path in paths]
        problems = self.cards.problems(RENAME)
        if not targets or problems:
            why = "nothing was clicked" if not targets else "fix the File names or Rename options first: " \
                "File > Options…"
            window.statusBar().showMessage(f"Rename: {why}", STATUS_MS)
            return False
        if self.rename_job is not None:
            job = window.jobs.get(self.rename_job)
            assert job is not None  # a running job stays in the list
            window.toast.show_message(f"{job.kind} is still running in {job.folder}. Start the other run when it "
                                      "has finished.")
            window.show_job(self.rename_job)
            return False
        self.rename_preview.show_planning(targets[0], what=rename_target_text(targets))
        window.center.setCurrentWidget(self.rename_preview)
        self._rename_token = self.rename_runner.plan_paths(targets, self.cards.rename_options())
        return True

    # -- removing ReplayGain (SPEC §10 B) --

    def remove_replaygain(self, ids: list[int]) -> None:
        """The ReplayGain tags of the tracks among `ids`, folders included with their subfolders: the
        REPLAYGAIN_* values and the comment lines the ReplayGain step writes. Asks first; runs on the TagWriter."""
        window = self.window
        if not window._library_open():
            return
        conn = window.open_library.conn
        track_ids = queries.track_ids_for(conn, ids)
        rows = queries.track_rows(conn, track_ids)
        paths = [str(rows[i].path) for i in track_ids if i in rows]
        if not paths:
            window.statusBar().showMessage("There are no audio files here.", STATUS_MS)
            return
        folders = [i for i in ids if (r := conn.execute("SELECT is_dir FROM entries WHERE id = ?",
                                                         (i,)).fetchone()) and r[0]]
        if len(ids) == 1 and folders:
            where = f"{path_of(conn, folders[0])} and its subfolders"
        else:
            where = "the selection"
        options = self.cards.replaygain_card.options()
        if not self.ask_remove_replaygain(len(paths), where, options.comment):
            return
        self._rg_remove_token = window.tag_writer.remove_replaygain(paths, options)
        count = len(paths)
        window.statusBar().showMessage(f"Removing the ReplayGain tags of {count} file{'s' if count != 1 else ''}…",
                                       STATUS_MS)

    def remove_entry(self, paths: list[str]) -> bool:
        """The right-click Remove ReplayGain tags (SPEC §31), with MusicDesk open: the clicked tracks only, after
        the app's question; the removal runs on the TagWriter (its locks, the index following each file), and a
        report window says what was done and stays until it is closed. False when nothing started."""
        window = self.window
        window.bring_to_front()
        tracks, left_out = split_tracks(paths)
        if not tracks:
            window.statusBar().showMessage("Remove ReplayGain tags: no track ReplayGain works on was clicked.",
                                           STATUS_MS)
            return False
        if any(window._unsaved_edits_under(Path(path)) for path in tracks):
            return False
        folders = sorted({str(Path(path).parent) for path in tracks})
        options = self.cards.replaygain_card.options()
        if not self.ask_remove_replaygain(len(tracks), folders[0] if len(folders) == 1 else "several folders",
                                          options.comment):
            return False
        token = window.tag_writer.remove_replaygain(tracks, options)
        report = RemoveReport(folders[0], window)
        report.revealRequested.connect(lambda path: window._in_shell(
            lambda: window.show_in_explorer(path, True), f"File Explorer couldn't show {path}.", "", True))
        self._remove_reports[token] = (report, left_out)
        report.closed.connect(lambda: self._remove_reports.pop(token, None))
        report.closed.connect(report.deleteLater)
        report.bring_to_front()
        return True

    def ask_remove_replaygain(self, count: int, where: str, comment: str) -> bool:
        """Confirm the removal (replaced in tests)."""
        return ask_remove(self.window, count, where, comment)

    def _replaygain_removed(self, result) -> None:
        if result.token in self._remove_reports:  # a right-click removal: its report window
            report, left_out = self._remove_reports.pop(result.token)
            report.show_result(result, left_out)
            return
        if result.token != self._rg_remove_token:
            return
        window = self.window
        done = len(result.removed)
        if done:
            text = f"Removed the ReplayGain tags of {done} file{'s' if done != 1 else ''}."
        else:
            text = "There were no ReplayGain tags to remove."
        if result.unchanged and done:
            text += f" {result.unchanged} had none."
        window.statusBar().showMessage(text, STATUS_MS)
        if result.failed:
            first = result.failed[0][1]
            more = f" (and {len(result.failed) - 1} more)" if len(result.failed) > 1 else ""
            window.toast.show_message(f"Not changed: {first}{more}")
