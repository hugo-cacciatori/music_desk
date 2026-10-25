"""Tidy's background work (SPEC §28): planning a tree (core/tidy.py, with flac's silence check), and the job, which
goes album by album: the split of an unsplit image, the renames, then ReplayGain. The offer list (the images and
cue sheets made redundant, and the plan's clutter) is collected for the Redundant files window at the end.

Nothing here runs on the UI thread (Ground rule 2). The job holds one lock on the whole tree (owner "the Tidy
tool"), taken on the caller's thread so a conflict is refused at once. Stop ends the current step as the tools
do (a split leaves nothing half-written; rsgain is killed) and no further album starts.
"""

import logging
import threading
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from music_desk import paths
from music_desk.core import cleanup, rename, replaygain, rsgain, split, tidy
from music_desk.core.cancel import CancelToken
from music_desk.core.cue import CueError
from music_desk.core.flac import FlacRunner
from music_desk.core.path_locks import PathLocks
from music_desk.core.replaygain import ReplayGainOptions
from music_desk.core.tidy import Kind, TidyCancelled, TidyPlan

log = logging.getLogger(__name__)

OWNER = "Tidy"
LINE_INTERVAL = 0.1  # seconds between batches of log lines


@dataclass
class TidyPlanned:
    token: int
    plan: TidyPlan | None = None
    error: str | None = None
    cancelled: bool = False


@dataclass
class AlbumDone:
    """What the run did to one album, for the report."""

    folder: Path
    lines: list[str] = field(default_factory=list)
    failed: bool = False


@dataclass
class TidyFinished:
    token: int
    root: Path
    albums: list[AlbumDone] = field(default_factory=list)
    offers: list[cleanup.Item] = field(default_factory=list)  # for the Redundant files window: split images,
    # their sheets, and the plan's clutter (re-checked)
    cancelled: bool = False
    error: str | None = None  # the job itself failed

    @property
    def failed_albums(self) -> int:
        return sum(album.failed for album in self.albums)


class _Lines:
    """Log lines to the runner's signal, batched (rsgain's reader thread reports too)."""

    def __init__(self, runner: "TidyRunner") -> None:
        self._runner = runner
        self._lines: list[str] = []
        self._last = 0.0
        self._lock = threading.Lock()

    def line(self, text: str) -> None:
        with self._lock:
            self._lines.append(text)
            if time.monotonic() - self._last < LINE_INTERVAL:
                return
            lines, self._lines, self._last = self._lines, [], time.monotonic()
        self._runner.lines.emit(lines)

    def flush(self) -> None:
        with self._lock:
            lines, self._lines = self._lines, []
        if lines:
            self._runner.lines.emit(lines)

    # core.replaygain.Reporter
    def info(self, message: str) -> None:
        self.line(message)

    def warn(self, message: str) -> None:
        self.line(f"WARNING  {message}")

    def error(self, message: str) -> None:
        self.line(f"ERROR  {message}")

    def progress(self, done: int, total: int, label: str) -> None:
        pass  # Tidy reports progress per album, not per rsgain run


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


class TidyRunner(QObject):
    planned = Signal(object)  # TidyPlanned
    planProgress = Signal(int, int, str)  # albums read, albums found, the folder being read
    lines = Signal(object)  # list[str]: log lines, batched
    progress = Signal(int, int, str)  # albums done, albums in all, the album now running
    finished = Signal(object)  # TidyFinished

    def __init__(self, locks: PathLocks, parent: QObject | None = None, *, flac: Path | None = None,
                 scanner=rsgain.scan, exe: Path | None = None) -> None:
        super().__init__(parent)
        self.locks = locks
        self.flac = flac or paths.flac_path()
        self.scanner = scanner  # replaced in tests by a fake that tags as rsgain would
        self.exe = exe
        self._pool = ThreadPoolExecutor(1, thread_name_prefix="tidy-tool")
        self._token = 0
        self._cancel = CancelToken()
        self._closed = False
        self.running = False

    # -- the plan --

    def plan(self, root: Path | list[Path], rename_options: rename.RenameOptions,
             replaygain_options: ReplayGainOptions, masks: Iterable[str], replacement: str,
             clutter_lists: cleanup.Lists = cleanup.DEFAULT_LISTS) -> int:
        """Plan Tidy on a folder, or on several as one plan (the right-click menu, SPEC §31), on the worker."""
        self._token += 1
        token, cancel = self._token, CancelToken()
        self._cancel = cancel
        masks = list(masks)

        def run() -> None:
            result = TidyPlanned(token)
            try:
                silent = split.is_silent(FlacRunner(self.flac), cancel)
                roots = root if isinstance(root, list) else [root]
                result.plan = tidy.plan_tidy_paths(roots, rename_options, replaygain_options, masks, replacement,
                                                   clutter_lists=clutter_lists, is_silent=silent, cancel=cancel,
                                                   progress=lambda done, total, folder: self.planProgress.emit(
                                                       done, total, folder))
            except TidyCancelled:
                result.cancelled = True
            except Exception as e:  # noqa: BLE001 - shown on the page
                log.exception("planning Tidy in %s failed", root)
                result.error = f"{type(e).__name__}: {e}"
            self.planned.emit(result)

        self._submit(run)
        return token

    # -- the job --

    def run(self, plan: TidyPlan, replaygain_options: ReplayGainOptions) -> int:
        """Run the plan under a lock on its whole trees. Raises LockedError at once if a job holds any of them."""
        lock = self.locks.acquire(plan.trees, OWNER)
        self._token += 1
        token, cancel = self._token, CancelToken()
        self._cancel = cancel
        self.running = True

        def job() -> None:
            out = _Lines(self)
            result = TidyFinished(token, plan.root)
            work = [album for album in plan.albums if not album.tidy]
            try:
                for n, album in enumerate(work):
                    if cancel.cancelled:
                        result.cancelled = True
                        break
                    self.progress.emit(n, len(work), str(album.folder))
                    out.line(f"== {album.folder}")
                    done = self._album(album, replaygain_options, cancel, out, result)
                    result.albums.append(done)
                    if cancel.cancelled:
                        result.cancelled = True
                        break
                self.progress.emit(len(work), len(work), "done")
                # The clutter goes on the offer list whatever happened to its album (SPEC §28), checked again.
                result.offers += [item for item in plan.clutter if cleanup.recheck(item) is None]
            except Exception as e:  # noqa: BLE001 - shown in the report
                log.exception("Tidy in %s failed", plan.root)
                result.error = f"{type(e).__name__}: {e}"
            finally:
                out.flush()
                self.locks.release(lock)
                self.running = False
            log.info("Tidy in %s: %d albums, %d failed%s", plan.root, len(result.albums), result.failed_albums,
                     ", stopped" if result.cancelled else "")
            self.finished.emit(result)

        if not self._submit(job):
            self.locks.release(lock)
            self.running = False
        return token

    def _album(self, album: tidy.AlbumTidy, options: ReplayGainOptions, cancel: CancelToken, out: _Lines,
               result: TidyFinished) -> AlbumDone:
        done = AlbumDone(album.folder)
        offers: list[cleanup.Item] = []
        for folder in album.folders:
            where = "" if folder.folder == album.folder else f"{folder.folder.name}: "
            if folder.kind is Kind.SKIPPED:
                done.lines.append(f"{where}skipped: {folder.skip}")
                continue
            if folder.kind is Kind.NOT_SPLIT:
                ok = self._split(folder, cancel, out, done, where)
                if not ok:
                    done.failed = True
                    return done  # no ReplayGain, nothing offered
                offers += tidy.offer_items(folder)
            elif folder.renames is not None and folder.rename_count:
                report = rename.apply_plan(folder.renames, cancel=cancel)
                text = f"{where}renamed {plural(len(report.renamed), 'file')}"
                done.lines.append(text)
                out.line(text)
                for problem in report.errors + report.name_taken + report.skipped:
                    done.lines.append(f"{where}not renamed: {problem}")
                    out.line(f"WARNING  {problem}")
                if report.cancelled:
                    return done
            if folder.kind is Kind.HALF_SPLIT:
                offers += tidy.offer_items(folder)
        if album.replaygain is not None and not cancel.cancelled:
            exclude = set().union(*(plan.images for plan in album.folders))
            out.line(f"ReplayGain ({album.replaygain})")
            run_log = replaygain.process_album(album.folder, options, scanner=self.scanner, exe=self.exe,
                                               cancel=cancel, reporter=out, exclude=exclude)
            if run_log is None:
                done.lines.append("ReplayGain: no file it can measure")
            else:
                outcome = run_log.result()
                done.lines.append(f"ReplayGain: {outcome} (see {options.log_file})")
                if outcome not in ("OK", "DONE WITH ERRORS"):
                    done.failed = True
        if album.clutter and not done.lines:
            done.lines.append("nothing to change: its files to offer are listed at the end")
        result.offers += offers
        return done

    def _split(self, folder: tidy.FolderPlan, cancel: CancelToken, out: _Lines, done: AlbumDone,
               where: str) -> bool:
        plan = folder.split
        assert plan is not None
        out.line(f"{where}splitting {plan.cue.name} into {plural(len(plan.written), 'track')}")
        try:
            cut = split.cut_album(plan, FlacRunner(self.flac), cancel,
                                  on_track=lambda n, total, track: out.line(f"  {track.name}"))
            if cut.cancelled:
                done.lines.append(f"{where}split stopped: nothing left half-written")
                return False
            if not cut.ok:
                done.lines.append(f"{where}split failed: {cut.error}")
                return False
            finished = split.finish_album(plan, cut)
        except CueError as e:
            done.lines.append(f"{where}split failed: {e}")
            return False
        if finished.error:
            done.lines.append(f"{where}split failed: {finished.error}")
            return False
        done.lines.append(f"{where}split into {plural(len(finished.tracks), 'track')}, each identical to its part "
                          "of the original")
        done.lines += [f"{where}{warning}" for warning in finished.warnings]
        if plan.sheet is not None and plan.sheet.fell_back:
            done.lines.append(f"{where}{plan.cue.name} read as Windows-1252 (no BOM, not UTF-8): if a title looks "
                              "wrong, fix its tags")
        return True

    def cancel(self) -> None:
        self._cancel.cancel()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._cancel.cancel()
            self._pool.shutdown(wait=True)

    def _submit(self, fn) -> bool:
        if self._closed:
            return False
        try:
            self._pool.submit(fn)
        except RuntimeError:
            return False
        return True


def report_text(result: TidyFinished) -> str:
    """The run's report: a headline, then each album with what was done."""
    if result.error:
        head = f"Failed: {result.error}"
    elif result.cancelled:
        head = f"Stopped after {plural(len(result.albums), 'album')}."
    else:
        head = f"Done: {plural(len(result.albums), 'album')} tidied"
        head += f", {result.failed_albums} with a problem." if result.failed_albums else "."
    lines = [head, ""]
    for album in result.albums:
        lines.append(f"{album.folder}{'  (problem)' if album.failed else ''}")
        lines += [f"    {line}" for line in album.lines]
    if result.offers:
        lines += ["", f"{plural(len(result.offers), 'item')} to offer for the Recycle Bin: you choose what to keep "
                  "in the Redundant files window that opened."]
    return "\n".join(lines)


def short_result(result: TidyFinished) -> str:
    if result.error:
        return "failed"
    if result.cancelled:
        return f"stopped after {plural(len(result.albums), 'album')}"
    text = f"{plural(len(result.albums), 'album')} tidied"
    return text + (f", {result.failed_albums} with a problem" if result.failed_albums else "")
