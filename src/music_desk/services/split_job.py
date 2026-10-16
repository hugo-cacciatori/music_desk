"""The cue split's background work (SPEC §22): the preview (each album planned, with flac's silence check) and
the job, which cuts, proves and finishes one album at a time with one flac process.

The job holds a lock on its folder tree for the whole run (owner "the Split tool"), taken on the caller's thread
so a conflict is refused at once. Stop kills flac and deletes that album's .partial files; once an album's tracks
are being renamed, Stop takes effect after it. "Then run ReplayGain" runs core.replaygain.process_album on each
finished album whose originals went to the Recycle Bin, inside the same job (as Tidy chains its two steps).
"""

import logging
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from music_desk import paths
from music_desk.core import cue, fsops, replaygain, rsgain, split
from music_desk.core.cancel import CancelToken
from music_desk.core.cue import AlbumPlan
from music_desk.core.flac import FlacRunner
from music_desk.core.path_locks import PathLocks
from music_desk.core.replaygain import ReplayGainOptions, RunLog
from music_desk.core.split import CutResult, Finished, SplitOptions

log = logging.getLogger(__name__)

OWNER = "the Split tool"


@dataclass(frozen=True)
class Planned:
    token: int
    plans: list[AlbumPlan]
    error: str | None = None
    cancelled: bool = False


@dataclass(frozen=True)
class Replanned:
    token: int
    plan: AlbumPlan  # the same album, read with another encoding


@dataclass
class AlbumResult:
    plan: AlbumPlan
    cut: CutResult | None = None
    finished: Finished | None = None
    replaygain: RunLog | None = None
    replaygain_note: str | None = None  # why ReplayGain didn't run on it

    @property
    def split(self) -> bool:
        return self.finished is not None and self.finished.error is None


@dataclass
class SplitFinished:
    token: int
    folder: Path
    options: SplitOptions
    albums: list[AlbumResult] = field(default_factory=list)  # the albums the job got to, in order
    refused: list[AlbumPlan] = field(default_factory=list)  # left alone, with their reasons
    cancelled: bool = False
    error: str | None = None  # the job itself failed


class SplitRunner(QObject):
    planned = Signal(object)  # Planned
    replanned = Signal(object)  # Replanned
    progress = Signal(int, int, str)  # albums done, albums to split, the album now running
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
        self._cancel = CancelToken()
        self._closed = False
        self.running = False

    # -- the preview --

    def plan(self, folder: str | Path, recursive: bool, name_masks: Sequence[str], replacement: str,
             cues: list[Path] | None = None, source: Path | None = None) -> int:
        """Plan every album in `folder` (or the given sheets). With `source`, only the sheets that cut it."""
        self._cancel.cancel()
        self._token += 1
        token, cancel = self._token, CancelToken()
        self._cancel = cancel
        runner = FlacRunner(self.flac)

        def run() -> None:
            try:
                sheets = cues if cues is not None else cue.find_albums(folder, recursive)
                plans = []
                for sheet in sheets:
                    if cancel.cancelled:
                        self.planned.emit(Planned(token, [], cancelled=True))
                        return
                    plan = cue.plan_album(sheet, name_masks, replacement, is_silent=split.is_silent(runner, cancel))
                    if source is None or (plan.source is not None and fsops.is_same_file(plan.source, source)):
                        plans.append(plan)
            except Exception as e:  # noqa: BLE001 - shown in the preview
                log.exception("split preview of %s failed", folder)
                self.planned.emit(Planned(token, [], f"{type(e).__name__}: {e}"))
                return
            self.planned.emit(Planned(token, plans))

        self._submit(run)
        return token

    def replan(self, sheet: Path, encoding: str | None, name_masks: Sequence[str], replacement: str) -> int:
        """One album again with the encoding picked in the preview."""
        self._token += 1
        token = self._token
        runner = FlacRunner(self.flac)

        def run() -> None:
            plan = cue.plan_album(sheet, name_masks, replacement, encoding, is_silent=split.is_silent(runner))
            self.replanned.emit(Replanned(token, plan))

        self._submit(run)
        return token

    # -- the job --

    def run(self, folder: str | Path, plans: list[AlbumPlan], options: SplitOptions,
            replaygain_options: ReplayGainOptions | None = None) -> int:
        """Split the planned albums under a lock on the folder tree. Raises LockedError at once if a job holds
        it."""
        folder = Path(folder)
        lock = self.locks.acquire([folder], OWNER)
        self._token += 1
        token, cancel = self._token, CancelToken()
        self._cancel = cancel
        self.running = True
        runner = FlacRunner(self.flac)
        todo = [p for p in plans if p.refusal is None]

        def job() -> None:
            result = SplitFinished(token, folder, options, refused=[p for p in plans if p.refusal is not None])
            try:
                for done, plan in enumerate(todo):
                    if cancel.cancelled:
                        result.cancelled = True
                        break
                    self.progress.emit(done, len(todo), plan.cue.parent.name)
                    album = AlbumResult(plan)
                    result.albums.append(album)
                    album.cut = split.cut_album(plan, runner, cancel)
                    if album.cut.cancelled:
                        result.cancelled = True
                        break
                    if not album.cut.ok:
                        continue
                    album.finished = split.finish_album(plan, album.cut, options.recycle, self.recycle)
                    if options.replaygain and album.split:
                        self._replaygain(album, replaygain_options, cancel)
                self.progress.emit(len(todo), len(todo), "done")
            except Exception as e:  # noqa: BLE001 - shown in the report
                log.exception("split job in %s failed", folder)
                result.error = f"{type(e).__name__}: {e}"
            finally:
                self.locks.release(lock)
                self.running = False
            log.info("split tool in %s: %d albums split, %d refused%s", folder,
                     sum(a.split for a in result.albums), len(result.refused),
                     ", stopped" if result.cancelled else "")
            self.finished.emit(result)

        if not self._submit(job):
            self.locks.release(lock)
            self.running = False
        return token

    def _replaygain(self, album: AlbumResult, options: ReplayGainOptions | None, cancel: CancelToken) -> None:
        """ReplayGain counts every audio file in the folder: only once the album file has gone (SPEC §22)."""
        if len(album.finished.recycled) < 2:
            album.replaygain_note = "ReplayGain wasn't run: the album file is still in the folder."
            return
        album.replaygain = replaygain.process_album(album.plan.cue.parent, options or replaygain.DEFAULT_OPTIONS,
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
