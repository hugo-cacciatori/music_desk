"""The cue split's background work (SPEC §22), for the Split page: reading a sheet and its source, planning the
page's sheet again after each edit (with flac's silence check), and the job, which cuts, proves and finishes one
album with one flac process.

Nothing here reads a file on the UI thread (Ground rule 2). The job holds a lock on the album folder for the whole
run (owner "the Split tool"), taken on the caller's thread so a conflict is refused at once. Stop kills flac and
deletes this run's .partial files; once the tracks are being renamed, Stop takes effect after them. "Then run
ReplayGain" runs core.replaygain.process_album on the folder inside the same job, once the originals are in the
Recycle Bin (as Tidy chains its two steps).
"""

import logging
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from music_desk import paths
from music_desk.core import cue, cue_edit, fsops, replaygain, rsgain, split
from music_desk.core.cancel import CancelToken
from music_desk.core.cue import AlbumPlan, CueError, CueSheet, SourceInfo
from music_desk.core.cue_edit import CueDocument
from music_desk.core.flac import FlacRunner
from music_desk.core.path_locks import PathLocks
from music_desk.core.replaygain import ReplayGainOptions, RunLog
from music_desk.core.split import CutResult, Finished, SplitOptions

log = logging.getLogger(__name__)

OWNER = "the Split tool"


@dataclass(frozen=True)
class Loaded:
    token: int
    path: Path
    document: CueDocument | None
    error: str | None = None  # the sheet couldn't be read


@dataclass(frozen=True)
class Planned:
    token: int
    plan: AlbumPlan


@dataclass
class SplitFinished:
    token: int
    plan: AlbumPlan
    options: SplitOptions
    cut: CutResult | None = None
    finished: Finished | None = None
    replaygain: RunLog | None = None
    replaygain_note: str | None = None  # why ReplayGain didn't run
    cancelled: bool = False
    error: str | None = None  # the job itself failed

    @property
    def folder(self) -> Path:
        return self.plan.cue.parent

    @property
    def split(self) -> bool:
        return self.finished is not None and self.finished.error is None


class SplitRunner(QObject):
    loaded = Signal(object)  # Loaded
    planned = Signal(object)  # Planned, for the latest request only
    progress = Signal(int, int, str)  # tracks done, tracks to write, the track now written
    finished = Signal(object)  # SplitFinished

    def __init__(self, locks: PathLocks, parent: QObject | None = None, *, flac: Path | None = None,
                 recycle: Callable[[Path], None] = fsops.send_to_recycle_bin, scanner=rsgain.scan,
                 rsgain_exe: Path | None = None) -> None:
        super().__init__(parent)
        self.locks = locks
        self.flac = flac or paths.flac_path()
        self.recycle = recycle  # replaced in tests: they never use the real Recycle Bin
        self.scanner = scanner  # replaced in tests by a fake rsgain
        self.rsgain_exe = rsgain_exe or paths.rsgain_path()
        self._pool = ThreadPoolExecutor(1, thread_name_prefix="split-tool")
        self._token = 0
        self._plan_token = 0
        self._cancel = CancelToken()
        self._closed = False
        self._sources: dict[tuple[Path, float, int], SourceInfo] = {}  # read once per file version
        self._silence: dict[tuple[Path, float, int, int], bool] = {}
        self.running = False

    # -- the page --

    def load(self, path: Path, encoding: str | None = None) -> int:
        """Read a .cue for the page (`encoding` None is Auto)."""
        self._token += 1
        token = self._token

        def run() -> None:
            try:
                self.loaded.emit(Loaded(token, path, cue_edit.read_document(path, encoding)))
            except OSError as e:
                self.loaded.emit(Loaded(token, path, None, f"{path.name} can't be read: {e.strerror or e}"))

        self._submit(run)
        return token

    def plan(self, sheet: CueSheet, cue_path: Path, name_masks: Sequence[str], replacement: str,
             ticked: Iterable[int] | None) -> int:
        """Plan the page's sheet (its unsaved edits included). Only the latest request's plan is emitted."""
        self._plan_token += 1
        token = self._plan_token
        ticked = frozenset(ticked) if ticked is not None else None
        runner = FlacRunner(self.flac)

        def run() -> None:
            if token != self._plan_token:
                return  # a newer edit came meanwhile
            try:
                plan = cue.plan_sheet(sheet, cue_path, name_masks, replacement, ticked=ticked,
                                      is_silent=self._silent(runner), read_info=self._source)
            except Exception as e:  # noqa: BLE001 - shown on the page
                log.exception("planning %s failed", cue_path)
                plan = cue.AlbumPlan(cue_path, sheet=sheet, refusal=f"{type(e).__name__}: {e}")
            if token == self._plan_token:
                self.planned.emit(Planned(token, plan))

        self._submit(run)
        return token

    def _source(self, path: Path) -> SourceInfo:
        stat = path.stat()
        key = (path, stat.st_mtime, stat.st_size)
        if key not in self._sources:
            self._sources[key] = cue.read_source(path)
        return self._sources[key]

    def _silent(self, runner: FlacRunner) -> Callable[[Path, int, int], bool]:
        check = split.is_silent(runner)

        def silent(source: Path, start: int, end: int) -> bool:
            stat = source.stat()
            key = (source, stat.st_mtime, start, end)
            if key not in self._silence:
                self._silence[key] = check(source, start, end)
            return self._silence[key]

        return silent

    # -- the job --

    def run(self, plan: AlbumPlan, options: SplitOptions, replaygain_options: ReplayGainOptions | None = None) -> int:
        """Split one planned album under a lock on its folder. Raises LockedError at once if a job holds it."""
        folder = plan.cue.parent
        lock = self.locks.acquire([folder], OWNER)
        self._token += 1
        token, cancel = self._token, CancelToken()
        self._cancel = cancel
        self.running = True
        runner = FlacRunner(self.flac)

        def job() -> None:
            result = SplitFinished(token, plan, options)
            try:
                result.cut = split.cut_album(
                    plan, runner, cancel,
                    on_track=lambda done, total, track: self.progress.emit(done, total, track.name))
                if result.cut.cancelled:
                    result.cancelled = True
                elif result.cut.ok:
                    result.finished = split.finish_album(plan, result.cut, options.recycle, self.recycle)
                    if options.replaygain and result.split:
                        self._replaygain(result, replaygain_options, cancel)
                total = len(plan.written)
                self.progress.emit(total, total, "done")
            except CueError as e:
                result.error = str(e)
            except Exception as e:  # noqa: BLE001 - shown in the report
                log.exception("split job in %s failed", folder)
                result.error = f"{type(e).__name__}: {e}"
            finally:
                self.locks.release(lock)
                self.running = False
            log.info("split of %s: %s", plan.cue, "stopped" if result.cancelled else
                     f"{len(result.finished.tracks)} tracks" if result.split else result.error or
                     (result.cut.error if result.cut else "not started"))
            self.finished.emit(result)

        if not self._submit(job):
            self.locks.release(lock)
            self.running = False
        return token

    def _replaygain(self, result: SplitFinished, options: ReplayGainOptions | None, cancel: CancelToken) -> None:
        """ReplayGain counts every audio file in the folder: only once the album file has gone (SPEC §22)."""
        if len(result.finished.recycled) < 2:
            result.replaygain_note = "ReplayGain wasn't run: the album file is still in the folder."
            return
        result.replaygain = replaygain.process_album(result.folder, options or replaygain.DEFAULT_OPTIONS,
                                                     scanner=self.scanner, exe=self.rsgain_exe, cancel=cancel)

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
