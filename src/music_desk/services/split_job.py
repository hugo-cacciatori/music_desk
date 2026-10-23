"""The cue split's background work (SPEC §22), for the Split page: reading a sheet (and listing the audio files
beside it), planning the page's sheet again after each edit (with flac's silence check), and the job, which cuts,
proves and finishes one album with one flac process. A split is just a split: nothing else runs after it, and
nothing is deleted but this run's own .partial files.

Nothing here reads a file on the UI thread (Ground rule 2). The job holds a lock on the image's folder and the
destination for the whole run (owner "the Split tool"), taken on the caller's thread so a conflict is refused at
once. Stop kills flac and deletes this run's .partial files; once the tracks are being renamed, Stop takes effect
after them.
"""

import logging
import os
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from music_desk import paths
from music_desk.core import cue, cue_edit, fsops, split
from music_desk.core.cancel import CancelToken
from music_desk.core.cue import AlbumPlan, CueError, CueSheet, SourceInfo
from music_desk.core.cue_edit import CueDocument
from music_desk.core.flac import FlacRunner
from music_desk.core.path_locks import PathLocks
from music_desk.core.split import CutResult, Finished
from music_desk.core.tags import TaggedFile

log = logging.getLogger(__name__)

OWNER = "the Split tool"


@dataclass(frozen=True)
class Loaded:
    token: int
    path: Path
    document: CueDocument | None
    error: str | None = None  # the sheet couldn't be read
    audio: tuple[str, ...] = ()  # the FLAC, WAV and AIFF files beside it, in Explorer's order (the source picker)


@dataclass(frozen=True)
class Planned:
    token: int
    plan: AlbumPlan
    audio: tuple[str, ...] = ()  # the audio files beside the sheet now (the source picker follows the folder)


@dataclass
class SplitFinished:
    token: int
    plan: AlbumPlan
    cut: CutResult | None = None
    finished: Finished | None = None
    cancelled: bool = False
    error: str | None = None  # the job itself failed

    @property
    def folder(self) -> Path:
        """Where the tracks were written."""
        return self.plan.folder

    @property
    def split(self) -> bool:
        return self.finished is not None and self.finished.error is None


def audio_files(folder: Path) -> tuple[str, ...]:
    """The FLAC, WAV and AIFF files in a folder, in Explorer's order: what a sheet there can point to."""
    try:
        names = [n for n in os.listdir(folder)
                 if os.path.splitext(n)[1].lower() in cue.SUPPORTED and (folder / n).is_file()]
    except OSError:
        return ()
    return tuple(sorted(names, key=fsops.natural_key))


class SplitRunner(QObject):
    loaded = Signal(object)  # Loaded
    planned = Signal(object)  # Planned, for the latest request only
    progress = Signal(int, int, str)  # tracks done, tracks to write, the track now written
    finished = Signal(object)  # SplitFinished

    def __init__(self, locks: PathLocks, parent: QObject | None = None, *, flac: Path | None = None) -> None:
        super().__init__(parent)
        self.locks = locks
        self.flac = flac or paths.flac_path()
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
                document = cue_edit.read_document(path, encoding)
                self.loaded.emit(Loaded(token, path, document, audio=audio_files(path.parent)))
            except OSError as e:
                self.loaded.emit(Loaded(token, path, None, f"{path.name} can't be read: {e.strerror or e}"))

        self._submit(run)
        return token

    def load_new(self, audio: Path) -> int:
        """New cue sheet… for an audio file: a new sheet filled from its tags (album artist, else artist; album),
        emitted as `loaded` for the `<stem>.cue` it would be saved as. Nothing is written."""
        self._token += 1
        token = self._token
        cue_path = audio.with_suffix(".cue")

        def run() -> None:
            performer = title = ""
            try:
                fields = TaggedFile(audio, shared=True).fields()
                performer, title = fields.albumartist or fields.artist or "", fields.album or ""
            except Exception as e:  # noqa: BLE001 - without tags, the sheet just has no PERFORMER or TITLE
                log.info("new cue sheet: no tags read from %s: %s", audio, e)
            document = cue_edit.new_document(audio.name, performer, title)
            self.loaded.emit(Loaded(token, cue_path, document, audio=audio_files(audio.parent)))

        self._submit(run)
        return token

    def plan(self, sheet: CueSheet, cue_path: Path, name_masks: Sequence[str], replacement: str,
             ticked: Iterable[int] | None, destination: Path | None = None) -> int:
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
                                      is_silent=self._silent(runner), read_info=self._source,
                                      destination=destination)
            except Exception as e:  # noqa: BLE001 - shown on the page
                log.exception("planning %s failed", cue_path)
                plan = cue.AlbumPlan(cue_path, sheet=sheet, refusal=f"{type(e).__name__}: {e}")
            if token == self._plan_token:
                self.planned.emit(Planned(token, plan, audio_files(cue_path.parent)))

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

    def run(self, plan: AlbumPlan) -> int:
        """Split one planned album under a lock on the image's folder and the destination. Raises LockedError at
        once if a job holds either."""
        lock = self.locks.acquire(sorted({plan.cue.parent, plan.folder}), OWNER)
        self._token += 1
        token, cancel = self._token, CancelToken()
        self._cancel = cancel
        self.running = True
        runner = FlacRunner(self.flac)

        def job() -> None:
            result = SplitFinished(token, plan)
            try:
                result.cut = split.cut_album(
                    plan, runner, cancel,
                    on_track=lambda done, total, track: self.progress.emit(done, total, track.name))
                if result.cut.cancelled:
                    result.cancelled = True
                elif result.cut.ok:
                    result.finished = split.finish_album(plan, result.cut)
                total = len(plan.written)
                self.progress.emit(total, total, "done")
            except CueError as e:
                result.error = str(e)
            except Exception as e:  # noqa: BLE001 - shown in the report
                log.exception("split of %s failed", plan.cue)
                result.error = f"{type(e).__name__}: {e}"
            finally:
                self.locks.release(lock)
                self.running = False
            log.info("split of %s into %s: %s", plan.cue, plan.folder, "stopped" if result.cancelled else
                     f"{len(result.finished.tracks)} tracks" if result.split and result.finished else result.error or
                     (result.cut.error if result.cut else "not started"))
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
            self._pool.shutdown(wait=True)

    def _submit(self, fn) -> bool:
        if self._closed:
            return False
        try:
            self._pool.submit(fn)
        except RuntimeError:
            return False
        return True
