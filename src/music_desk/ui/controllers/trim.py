"""The trim's controller (SPEC §23, §16, §35): the Trim window's page, its plan, Preview in player and the run.

One window, one track: the Metadata tab's Trim… or the right-click "Trim this track…" opens it (its two times are
its only state). Each edit is planned again on the runner's
worker after a short pause; the waveform is read once per track on its own worker. A trim is just a trim: it writes
one copy into a subfolder, and nothing runs after it.
"""

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

from music_desk.core import playlists, trim
from music_desk.core.cue import SourceInfo
from music_desk.core.path_locks import LockedError
from music_desk.services.trim_job import Loaded, PeaksRead, Planned, TrimFinished, TrimRunner
from music_desk.ui.trim_page import TrimPage

log = logging.getLogger(__name__)

PLAN_DELAY_MS = 80  # after an edit (a handle dragged, a key typed), the plan follows once it pauses
NO_CUE_PLAYER = ("No app is set to open .cue sheets. To choose one, open Windows Settings > Apps > Default apps "
                 "and search for .cue.")


def format_line(info: SourceInfo) -> str:
    """'FLAC 16-bit/44.1 kHz · 9:47.213'."""
    return f"{info.format} {info.bits}-bit/{info.rate / 1000:g} kHz · {trim.format_time(trim.length_ms(info))}"


def report_text(result: TrimFinished) -> str:
    plan = result.plan
    if result.cancelled:
        return "Stopped: nothing left half-done. The copy being written was deleted; the original is unchanged."
    if result.error or not result.trimmed:
        return f"The trim failed: {result.error or 'not started'}\nNothing was written; the original is unchanged."
    length = trim.format_time(trim.to_ms(plan.length, plan.info.rate))
    lines = [f"Done: {plan.shown_path}, {length}, identical to its part of the original. The original is unchanged.",
             "", "The proof: the copy has its planned number of samples, and its audio has the same MD5 as its part "
                 "of the original."]
    if result.finished is not None:
        lines += [f"Warning: {w}" for w in result.finished.warnings]
    return "\n".join(lines)


class TrimController(QObject):
    def __init__(self, window, locks, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.window = window
        self.page = TrimPage()
        self.runner = TrimRunner(locks, self)
        self.path: Path | None = None
        self.info: SourceInfo | None = None
        self.tags: dict = {}
        self.plan: trim.TrimPlan | None = None
        self.running = False  # a trim is being written
        self._load_token: int | None = None
        self._plan_token: int | None = None
        self._run_token: int | None = None
        self._planning = False
        self._plan_timer = QTimer(self, singleShot=True, interval=PLAN_DELAY_MS)
        self._plan_timer.timeout.connect(self._request_plan)

        page = self.page
        page.backRequested.connect(self.back)
        page.doneRequested.connect(self.done)
        page.trimRequested.connect(self.trim)
        page.stopRequested.connect(self.runner.cancel)
        page.previewRequested.connect(self.preview)
        page.valuesEdited.connect(self._edited)
        window.tools.cards.changed.connect(self._settings_changed)
        self.runner.loaded.connect(self._loaded)
        self.runner.peaksRead.connect(self._peaks_read)
        self.runner.planned.connect(self._planned)
        self.runner.finished.connect(self._finished)

    # -- opening and leaving --

    def open(self, path: str | Path) -> None:
        """The page on this track, from scratch (not while a trim is being written)."""
        if self.running:
            return
        path = Path(path)
        self.path, self.info, self.tags, self.plan = path, None, {}, None
        self._plan_timer.stop()
        self.page.show_loading(path)
        self._load_token = self.runner.load(path)

    def back(self) -> None:
        """Close, before the trim: the window closes, nothing written."""
        self.window.close_tools()

    def done(self) -> None:
        self.plan = None
        self.window.close_tools()

    def _loaded(self, loaded: Loaded) -> None:
        if loaded.token != self._load_token:
            return
        if loaded.info is None:
            self.page.show_error(loaded.error or "")
            return
        self.info, self.tags = loaded.info, loaded.tags
        subfolder = self.window.settings.trim_subfolder or trim.DEFAULT_SUBFOLDER
        self.page.show_track(loaded.path, format_line(loaded.info), trim.length_ms(loaded.info), subfolder)
        self._edited()

    def _settings_changed(self) -> None:
        """The subfolder is File > Options…, Trim (SPEC §12): a change there applies to the open track at once."""
        subfolder = self.window.settings.trim_subfolder or trim.DEFAULT_SUBFOLDER
        if self.page.state == "editing" and subfolder != self.page.subfolder_name:
            self.page.subfolder_name = subfolder
            self._edited()

    def _peaks_read(self, read: PeaksRead) -> None:
        if read.token == self._load_token:
            self.page.waveform.set_peaks(read.peaks, read.error or "")

    # -- the plan --

    def _edited(self) -> None:
        if self.info is None or self.page.state != "editing":
            return
        self._planning = True
        self._plan_timer.start()
        self._refresh_buttons()

    def _request_plan(self) -> None:
        if self.path is None or self.info is None:
            return
        start, end, subfolder = self.page.values()
        self._plan_token = self.runner.plan(self.path, self.info, start, end, subfolder)

    def _planned(self, planned: Planned) -> None:
        if planned.token != self._plan_token or self.page.state != "editing":
            return
        self._planning = self._plan_timer.isActive()
        self.plan = planned.plan
        self.page.show_plan(self.plan)
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        plan = None if self._planning else self.plan
        reasons = list(dict.fromkeys(why for _where, why in plan.problems)) if plan is not None else []
        ready = plan is not None and plan.ok and not reasons and not self.running
        self.page.set_buttons(trim_enabled=ready, preview=plan is not None and plan.times_ok,
                              note=" ".join(reasons))

    # -- Preview in player --

    def preview(self) -> None:
        """The current edit as a temporary cue sheet, opened in the default player on the shell thread (SPEC §23).
        Nothing in the library is written."""
        plan = self.plan
        if plan is None or not plan.times_ok or self._planning:
            return
        sheet = trim.preview_sheet(plan, self.tags.get("title", ""), self.tags.get("artist", ""),
                                   self.tags.get("album", ""))
        window = self.window

        def run() -> None:
            window.launch(playlists.write_preview(sheet, window.playlist_dir))

        window._in_shell(run, NO_CUE_PLAYER, str(plan.source), False)
        window.statusBar().showMessage(f"Previewing {plan.source.name} in the player…", 8000)

    # -- the run --

    def trim(self) -> None:
        plan = self.plan
        if plan is None or not plan.ok or self._planning or self.running:
            return
        if not self.window._library_open() or self.window._unsaved_edits_under(plan.source.parent):
            return
        try:
            token = self.runner.run(plan)
        except LockedError as e:
            self.window.toast.show_message(str(e))
            return
        self._run_token = token
        self.running = True
        self.page.show_running(plan.source.name)
        self._refresh_buttons()

    def _finished(self, result: TrimFinished) -> None:
        if result.token != self._run_token:
            return
        if self.window._library_open():
            self.window.refresh_after_tool(result.plan.source.parent)  # the subfolder and the copy
        self.page.show_result(report_text(result))
        self.running = False
        self.plan = None
        if result.trimmed:
            self.window.statusBar().showMessage(f"Trimmed {result.plan.source.name}.", 8000)

    def close(self) -> None:
        self.runner.close()  # a running flac is killed; this run's .partial is deleted
