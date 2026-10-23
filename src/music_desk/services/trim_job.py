"""The trim's background work (SPEC §23), for the Trim page: reading the track (its stream values and the tags the
preview names), its waveform, planning the page's values again after each edit, and the job, which cuts, proves
and finishes one copy with one flac process. A trim is just a trim: nothing else runs after it, and nothing is
deleted but this run's own .partial (and the subfolder, if this run created it and it is still empty).

Nothing here reads a file on the UI thread (Ground rule 2). The waveform has its own worker, so a long read never
holds up a plan or the job, and a newer track stops it. The job holds a lock on the track's folder (which holds the
subfolder) for the whole run, owner "the Trim tool", taken on the caller's thread so a conflict is refused at once.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from music_desk import paths
from music_desk.core import trim, waveform
from music_desk.core.cancel import CancelToken
from music_desk.core.cue import CueError, SourceInfo
from music_desk.core.flac import Cancelled, FlacError, FlacRunner
from music_desk.core.path_locks import PathLocks
from music_desk.core.tags import TaggedFile
from music_desk.core.trim import Finished, TrimPlan, TrimResult
from music_desk.core.waveform import Peaks

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Loaded:
    token: int
    path: Path
    info: SourceInfo | None
    error: str | None = None  # why the track can't be trimmed
    tags: dict = field(default_factory=dict)  # title, artist, album: what Preview in player names


@dataclass(frozen=True)
class PeaksRead:
    token: int  # the load's
    peaks: Peaks | None
    error: str | None = None


@dataclass(frozen=True)
class Planned:
    token: int
    plan: TrimPlan


@dataclass
class TrimFinished:
    token: int
    plan: TrimPlan
    cut: TrimResult | None = None
    finished: Finished | None = None
    cancelled: bool = False
    error: str | None = None  # the trim failed (flac, the proof, the space) or the job itself did

    @property
    def trimmed(self) -> bool:
        return self.finished is not None and self.finished.error is None and self.finished.path is not None


class TrimRunner(QObject):
    loaded = Signal(object)  # Loaded
    peaksRead = Signal(object)  # PeaksRead, for the latest load only
    planned = Signal(object)  # Planned, for the latest request only
    finished = Signal(object)  # TrimFinished

    def __init__(self, locks: PathLocks, parent: QObject | None = None, *, flac: Path | None = None) -> None:
        super().__init__(parent)
        self.locks = locks
        self.flac = flac or paths.flac_path()
        self._pool = ThreadPoolExecutor(1, thread_name_prefix="trim-tool")
        self._waveform_pool = ThreadPoolExecutor(1, thread_name_prefix="trim-waveform")
        self._token = 0
        self._plan_token = 0
        self._cancel = CancelToken()
        self._waveform_cancel = CancelToken()
        self._closed = False
        self.running = False

    # -- the page --

    def load(self, path: Path) -> int:
        """Read the track, then its waveform (on its own worker). A newer load stops the older waveform."""
        self._token += 1
        token = self._token
        self._waveform_cancel.cancel()
        cancel = self._waveform_cancel = CancelToken()

        def run() -> None:
            try:
                info = trim.read_source(path)
            except CueError as e:
                self.loaded.emit(Loaded(token, path, None, str(e)))
                return
            except OSError as e:
                self.loaded.emit(Loaded(token, path, None, f"{path.name} can't be read: {e.strerror or e}"))
                return
            except Exception as e:  # noqa: BLE001 - mutagen's errors for a damaged file: shown on the page
                self.loaded.emit(Loaded(token, path, None, f"{path.name} can't be read ({type(e).__name__}: {e})."))
                return
            tags = {}
            try:
                fields = TaggedFile(path, shared=True).fields()
                tags = {"title": fields.title or "", "artist": fields.artist or "", "album": fields.album or ""}
            except Exception:  # noqa: BLE001 - without tags, the preview names the parts by the file name
                pass
            self.loaded.emit(Loaded(token, path, info, tags=tags))
            self._submit(lambda: self._read_peaks(token, path, info, cancel), self._waveform_pool)

        self._submit(run)
        return token

    def _read_peaks(self, token: int, path: Path, info: SourceInfo, cancel: CancelToken) -> None:
        if cancel.cancelled:
            return
        try:
            peaks = waveform.read_peaks(FlacRunner(self.flac), path, info, cancel)
            result = PeaksRead(token, peaks)
        except Cancelled:
            return
        except FlacError as e:
            result = PeaksRead(token, None, f"The audio can't be read: {e}")
        except Exception as e:  # noqa: BLE001 - shown in the waveform's place; the trim only needs the length
            log.exception("waveform of %s failed", path)
            result = PeaksRead(token, None, f"The audio can't be read ({type(e).__name__}: {e}).")
        if not cancel.cancelled:
            self.peaksRead.emit(result)

    def plan(self, path: Path, info: SourceInfo, start: str, end: str, subfolder: str) -> int:
        """Check the page's values (reading the subfolder's listing). Only the latest request's plan is emitted."""
        self._plan_token += 1
        token = self._plan_token

        def run() -> None:
            if token != self._plan_token:
                return  # a newer edit came meanwhile
            plan = trim.plan_trim(path, info, start, end, subfolder)
            if token == self._plan_token:
                self.planned.emit(Planned(token, plan))

        self._submit(run)
        return token

    # -- the job --

    def run(self, plan: TrimPlan) -> int:
        """Trim under a lock on the track's folder. Raises LockedError at once if a job holds it."""
        lock = self.locks.acquire([plan.source.parent], trim.OWNER)
        self._token += 1
        token, cancel = self._token, CancelToken()
        self._cancel = cancel
        self.running = True
        runner = FlacRunner(self.flac)

        def job() -> None:
            result = TrimFinished(token, plan)
            try:
                result.cut = trim.cut_trim(plan, runner, cancel)
                if result.cut.cancelled:
                    result.cancelled = True
                elif result.cut.ok:
                    result.finished = trim.finish_trim(plan, result.cut)
                    result.error = result.finished.error
                else:
                    result.error = result.cut.error
            except Exception as e:  # noqa: BLE001 - shown in the report
                log.exception("trim of %s failed", plan.source)
                result.error = f"{type(e).__name__}: {e}"
            finally:
                self.locks.release(lock)
                self.running = False
            log.info("trim of %s to %s: %s", plan.source, plan.shown_path,
                     "stopped" if result.cancelled else result.error or "done")
            self.finished.emit(result)

        if not self._submit(job):
            self.locks.release(lock)
            self.running = False
        return token

    def cancel(self) -> None:
        self._cancel.cancel()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._cancel.cancel()
            self._waveform_cancel.cancel()
            self._waveform_pool.shutdown(wait=True)
            self._pool.shutdown(wait=True)

    def _submit(self, fn, pool: ThreadPoolExecutor | None = None) -> bool:
        if self._closed:
            return False
        try:
            (pool or self._pool).submit(fn)
        except RuntimeError:
            return False
        return True
