"""The Tools page's controller (SPEC §10, §16): Rename, ReplayGain and Tidy, their previews, jobs and reports,
and Remove ReplayGain tags.

A tool runs one job at a time: while its job runs, its page shows that job instead. Rename is previewed and
applied on its runner; ReplayGain has no preview (it only writes tags and the logs); Tidy previews its rename,
then runs ReplayGain on the final names as the same job's second step.

Dialogs go through ask_* methods, which tests replace.
"""

import dataclasses
import os
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMessageBox

from music_desk.core import rename
from music_desk.core.index import queries
from music_desk.core.index.store import path_of
from music_desk.core.path_locks import LockedError
from music_desk.services import jobs as jobs_module
from music_desk.services.rename_job import Applied, Planned, RenameRunner
from music_desk.services.replaygain_job import Finished, ReplayGainRunner
from music_desk.ui.controllers import STATUS_MS
from music_desk.ui.tools.rename_preview import RenamePreview
from music_desk.ui.tools.rg_report import ReplayGainView, short_result
from music_desk.ui.tools.tools_page import RENAME, REPLAYGAIN, TIDY, ToolRequest, ToolScope, ToolsPage


class ToolsController(QObject):
    def __init__(self, window, locks, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.window = window
        self.page = ToolsPage(window.settings)
        self.page.backRequested.connect(window.close_tools)
        self.page.runRequested.connect(self.run_tool)
        window.tag_io.renameFields.connect(self._tool_example)
        window.tag_writer.replaygainRemoved.connect(self._replaygain_removed)
        self._example_token: int | None = None
        self._rg_remove_token: int | None = None

        self.rename_preview = RenamePreview()
        self.rename_preview.backRequested.connect(self._rename_back)
        self.rename_preview.doneRequested.connect(window.close_tools)
        self.rename_preview.applyRequested.connect(self.apply_rename)
        self.rename_preview.cancelRequested.connect(lambda: self.rename_runner.cancel())
        self.rename_runner = RenameRunner(locks, self)
        self.rename_runner.planned.connect(self._rename_planned)
        self.rename_runner.applied.connect(self._rename_applied)
        self._rename_token: int | None = None
        self._rename_request: ToolRequest | None = None
        self.rename_job: int | None = None  # the job id of the running rename (or Tidy's rename step)

        self.rg_view = ReplayGainView()
        self.rg_view.backRequested.connect(lambda: window.center.setCurrentWidget(self.page))
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
        self._rg_token: int | None = None
        self._rg_request: ToolRequest | None = None
        self.rg_job: int | None = None

    @property
    def pages(self) -> tuple:
        """The centre pages this controller shows."""
        return self.page, self.rename_preview, self.rg_view

    def shutdown(self) -> None:
        self.rename_runner.close()  # a running rename stops between two files
        self.rg_runner.close()  # a running rsgain is killed; its album is reported as cancelled

    # -- the page --

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

    def open(self, ids: list[int]) -> None:
        window = self.window
        scope = self.scope_for(ids)
        if scope is None:
            window.statusBar().showMessage("Select a folder, or music files, to use the tools.", STATUS_MS)
            return
        self.page.set_scope(scope)
        self._example_token = window.tag_io.read_rename_fields(scope.first_track) if scope.first_track else None
        window.center.setCurrentWidget(self.page)

    def cancel_planning(self) -> None:
        """Leaving the rename preview while it plans: the plan isn't needed any more."""
        if self.rename_preview.state == "planning":
            self.rename_runner.cancel()

    def _tool_example(self, token: int, fields: dict | None, extension: str) -> None:
        if token == self._example_token:
            self.page.set_example(fields, extension)

    def run_tool(self, request: ToolRequest) -> None:
        """A card's button: the rename preview, the ReplayGain job (no preview), or Tidy's rename preview. A tool
        runs one job at a time: while its job runs, its page shows that job instead."""
        window = self.window
        busy = None
        if request.tool in (RENAME, TIDY) and self.rename_job is not None:
            busy = self.rename_job
        elif request.tool in (REPLAYGAIN, TIDY) and self.rg_job is not None:
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
        elif request.tool == REPLAYGAIN:
            self.run_replaygain(request)  # no preview: it only writes tags and the logs (Hugo, 2026-10-15)
        elif request.tool == TIDY:
            self.preview_rename(request)  # Tidy: the rename step is previewed; ReplayGain follows Apply

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

    def run_replaygain(self, request: ToolRequest | None = None, title: str | None = None) -> bool:
        """Start the ReplayGain job (the card's Run, or Tidy's second step). False when it didn't start."""
        window = self.window
        request = request or self._rg_request
        if request is None or not window._library_open():
            return False
        folder = request.scope.folder
        if window._unsaved_edits_under(folder):
            return False
        assert request.replaygain_options is not None  # set for every ReplayGain and Tidy request
        try:
            self._rg_token = self.rg_runner.run(folder, request.replaygain_options, request.discography)
        except LockedError as e:
            window.toast.show_message(f"{e.path} is in use by {e.owner}. Try again when it has finished.")
            return False
        self._rg_request = request
        window._pause_indexing(True)  # rsgain gets the drive to itself; folders you open are still served
        if request.tool == TIDY and self.rename_job is not None:
            self.rg_job, self.rename_job = self.rename_job, None  # the same Tidy job, step 2
            window.jobs.update(self.rg_job, status="ReplayGain")
        else:
            self.rg_job = window._start_job(jobs_module.REPLAYGAIN, folder)
        follow = window.center.currentWidget() in self.pages
        self.rg_view.show_running(folder, title, request.discography)
        if follow:  # Tidy's step 2 doesn't pull you back if you went browsing meanwhile
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
            window.refresh_after_tool(finished.folder)  # new tags and replaygain.log files
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
        self._rename_request = request
        assert request.rename_options is not None  # set for every Rename and Tidy request
        self.rename_preview.show_planning(scope.folder, len(scope.files), tidy=request.tool == TIDY)
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

    def _rename_back(self) -> None:
        self.cancel_planning()
        self.window.center.setCurrentWidget(self.page)

    def ask_recycle(self, names: list[str]) -> str:
        """Junk files listed for the Recycle Bin: "recycle", "keep" (rename only) or "cancel"."""
        shown = "\n".join(names[:30]) + (f"\n… and {len(names) - 30} more" if len(names) > 30 else "")
        box = QMessageBox(QMessageBox.Icon.Question, "Send junk files to the Recycle Bin",
                          f"These {len(names)} file{'s' if len(names) != 1 else ''} will be sent to the Recycle "
                          f"Bin (you can restore them from there):\n\n{shown}", parent=self.window)
        recycle = box.addButton("Rename and recycle", QMessageBox.ButtonRole.AcceptRole)
        keep = box.addButton("Rename only", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return "recycle" if box.clickedButton() is recycle else "keep" if box.clickedButton() is keep else "cancel"

    def apply_rename(self) -> None:
        window = self.window
        plan = self.rename_preview.plan
        if plan is None or not window._library_open():
            return
        if window._unsaved_edits_under(plan.folder):  # unsaved tag edits on a file about to be renamed
            return
        junk = [i.source.relative_to(plan.folder).as_posix() for i in plan.items if i.status is rename.Status.JUNK]
        if junk:
            answer = self.ask_recycle(junk)
            if answer == "cancel":
                return
            if answer == "keep":
                plan = dataclasses.replace(plan, items=[i for i in plan.items if i.status is not rename.Status.JUNK])
        try:
            self._rename_token = self.rename_runner.apply(plan)
        except LockedError as e:
            window.toast.show_message(f"{e.path} is in use by {e.owner}. Try again when it has finished.")
            return
        tidy = self._rename_request is not None and self._rename_request.tool == TIDY
        self.rename_job = window._start_job(jobs_module.TIDY if tidy else jobs_module.RENAME, plan.folder)
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
            if self._rename_request is not None:  # then the whole scope: recycled junk, sidecars, anything else
                window.refresh_after_tool(self._rename_request.scope.folder)
        self.rename_preview.show_report(report)
        count = len(report.renamed)
        window.statusBar().showMessage(f"Renamed {count} file{'s' if count != 1 else ''}.", STATUS_MS)
        request = self._rename_request
        summary = rename.summary_sections(report, dry_run=False)[0][0]
        if request is not None and request.tool == TIDY:  # Tidy, step 2: ReplayGain on the final names
            if report.cancelled:
                self.rename_preview.message.setText("Stopped, so ReplayGain was not run.")
            elif self.run_replaygain(request, "Tidy, step 2 of 2: ReplayGain"):
                return  # the job goes on (its rename report is kept)
            else:
                self.rename_preview.message.setText("Renamed. ReplayGain was not started (see the message).")
        if job is not None:
            window.jobs.finish(job, jobs_module.STOPPED if report.cancelled else jobs_module.DONE, summary)
            self.rename_job = None

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
        options = self.page.replaygain_card.options()
        if not self.ask_remove_replaygain(len(paths), where, options.comment):
            return
        self._rg_remove_token = window.tag_writer.remove_replaygain(paths, options)
        count = len(paths)
        window.statusBar().showMessage(f"Removing the ReplayGain tags of {count} file{'s' if count != 1 else ''}…",
                                       STATUS_MS)

    def ask_remove_replaygain(self, count: int, where: str, comment: str) -> bool:
        """Confirm the removal (replaced in tests)."""
        noun = f"{count} track{'s' if count != 1 else ''}"
        box = QMessageBox(QMessageBox.Icon.Question, "Remove ReplayGain tags",
                          f"Remove the ReplayGain tags from {noun} in {where}?\n\n"
                          f"This deletes their track and album gain and peak values, and the comment line "
                          f"\"{comment}\" that ReplayGain writes. Other tags, other comment lines and the audio "
                          f"itself are not changed, and replaygain.log files stay.", parent=self.window)
        remove = box.addButton("Remove", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return box.clickedButton() is remove

    def _replaygain_removed(self, result) -> None:
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
