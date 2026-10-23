"""Tidy's controller (SPEC §28, §16): the toolbar's Tidy opens the plan page, Run Tidy runs the job, and the
Redundant files window (SPEC §32) opens at the end with the split images, their sheets and the clutter.

The plan comes first and lists everything; there is no separate step to start. Dialogs and the Recycle Bin go
through attributes that tests replace.
"""

from pathlib import Path

from PySide6.QtCore import QObject

from music_desk.core import cleanup, fsops
from music_desk.core.path_locks import LockedError
from music_desk.core.tidy import TidyPlan
from music_desk.services import jobs as jobs_module
from music_desk.services.tidy_job import TidyFinished, TidyPlanned, TidyRunner, report_text, short_result
from music_desk.ui.controllers import STATUS_MS
from music_desk.ui.recycle_window import RecycleWindow
from music_desk.ui.tools.tidy_page import TidyPage
from music_desk.ui.tools.tool_cards import ToolRequest


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


class TidyController(QObject):
    def __init__(self, window, locks, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.window = window
        self.locks = locks
        self.page = TidyPage()
        self.page.backRequested.connect(self.back)
        self.page.runRequested.connect(self.run)
        self.page.stopRequested.connect(lambda: self.runner.cancel())
        self.page.doneRequested.connect(window.close_tools)
        self.runner = TidyRunner(locks, self)
        self.runner.planned.connect(self._planned)
        self.runner.planProgress.connect(self.page.set_plan_progress)
        self.runner.lines.connect(self.page.add_lines)
        self.runner.progress.connect(self._progress)
        self.runner.finished.connect(self._finished)
        self.recycle = fsops.send_to_recycle_bin  # the window's Delete selected; replaced in tests (a recorder)
        self.check_recycle = fsops.can_recycle
        self.request: ToolRequest | None = None
        self.plan: TidyPlan | None = None
        self.job: int | None = None
        self.offer: RecycleWindow | None = None
        self._plan_token: int | None = None
        self._run_token: int | None = None

    @property
    def showing(self) -> bool:
        return self.window.center.currentWidget() is self.page

    def open(self, request: ToolRequest) -> None:
        """The toolbar's Tidy: the plan of the whole tree, read on a worker."""
        window = self.window
        if self.job is not None:
            window.center.setCurrentWidget(self.page)  # one Tidy at a time: the running one
            return
        assert request.rename_options is not None and request.replaygain_options is not None
        self.request, self.plan = request, None
        masks, replacement = window._name_masks()
        self.page.show_planning(request.scope.folder)
        window.center.setCurrentWidget(self.page)
        lists = request.cleanup_options.lists if request.cleanup_options is not None else cleanup.DEFAULT_LISTS
        self._plan_token = self.runner.plan(request.scope.folder, request.rename_options,
                                            request.replaygain_options, masks, replacement, lists)

    def back(self) -> None:
        """Also while Tidy runs: it goes on, and the jobs panel (or the toolbar's Tidy) shows its page again."""
        if self.page.state == "planning":
            self.runner.cancel()
            self._plan_token = None
        self.window.close_tools()

    def _planned(self, planned: TidyPlanned) -> None:
        if planned.token != self._plan_token or planned.cancelled:
            return
        if planned.error or planned.plan is None:
            self.page.show_error(planned.error or "The folders couldn't be read.")
            return
        self.plan = planned.plan
        self.page.show_plan(planned.plan)

    def run(self) -> None:
        window, plan, request = self.window, self.plan, self.request
        if plan is None or request is None or self.job is not None or not plan.has_work:
            return
        assert request.replaygain_options is not None
        if window._unsaved_edits_under(plan.root):
            return
        try:
            self._run_token = self.runner.run(plan, request.replaygain_options)
        except LockedError as e:
            window.toast.show_message(f"{e.path} is in use by {e.owner}. Try again when it has finished.")
            return
        self.job = window._start_job(jobs_module.TIDY, plan.root)
        window._pause_indexing(True)  # flac and rsgain read whole albums: the drive is Tidy's meanwhile
        self.page.show_running()

    def _progress(self, done: int, total: int, album: str) -> None:
        self.page.set_progress(done, total, album)
        if self.job is not None and album != "done":
            self.window.jobs.update(self.job, status=f"Running ({done + 1} of {total}): {Path(album).name}",
                                    progress=(done, total))

    def _finished(self, result: TidyFinished) -> None:
        if result.token != self._run_token:
            return
        window = self.window
        window._pause_indexing(False)
        window.refresh_after_tool(result.root)  # the new tracks, the names, the tags
        self.page.show_result(report_text(result))
        if self.job is not None:
            window.jobs.report(self.job, "tidy", result)
            state = (jobs_module.FAILED if result.error else jobs_module.STOPPED if result.cancelled
                     else jobs_module.DONE)
            window.jobs.finish(self.job, state, short_result(result))
            self.job = None
        self.plan = None  # a new Run Tidy… reads the tree again
        if result.offers:
            self.show_offer(result.offers, result.root)

    def show_report(self, result: TidyFinished) -> None:
        """A finished run's report, from the jobs panel."""
        self.page.show_result(report_text(result), f"<b>Tidy</b> · {result.root}")
        self.window.center.setCurrentWidget(self.page)

    # -- the offer window --

    def show_offer(self, items: list[cleanup.Item], folder: Path | None = None) -> None:
        """The Redundant files window, every box checked (SPEC §32); its report stays until it is closed."""
        window = self.window
        if self.offer is not None and self.offer.state != "deleting":
            self.offer.close()
        folder = folder or (self.request.scope.folder if self.request is not None else items[0].path.parent)
        self.offer = RecycleWindow(
            window, folder, items, locks=self.locks, owner="the Tidy tool", recycle=self.recycle,
            check_recycle=self.check_recycle,
            intro="Tidy made these files redundant (split images and their cue sheets), or found them beside the "
                  "music (pictures, videos, folders holding nothing else). Every box is checked: uncheck what to "
                  "keep, then click Delete selected to send the rest to the Recycle Bin. Closing this window keeps "
                  "them all.")
        self.offer.finished.connect(self._offer_applied)
        self.offer.revealRequested.connect(lambda path, is_dir: window._in_shell(
            lambda: window.show_in_explorer(path, is_dir), f"File Explorer couldn't show {path}.",
            str(Path(path).parent), True))
        self.offer.show()
        self.offer.raise_()

    def _offer_applied(self, report) -> None:
        window = self.window
        for folder in report.folders:
            window.refresh_after_tool(folder)
        count = len(report.recycled)
        text = f"Sent {plural(count, 'item')} to the Recycle Bin." if count else "Nothing was sent to the Recycle Bin."
        window.statusBar().showMessage(text, STATUS_MS)

    def shutdown(self) -> None:
        self.runner.close()
