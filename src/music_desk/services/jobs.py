"""The jobs of this session (SPEC §10 "Jobs panel"): the Rename, ReplayGain and Tidy runs, running or finished.

Each tool runs one job at a time, on its own worker thread, under a lock on its folder tree; this list is what the
jobs panel shows and reopens. A job keeps its report, so a finished run can be looked at again until the list is
cleared (it isn't saved: the reports themselves are in each folder's replaygain.log, or shown again by a rescan).
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal

RUNNING, DONE, STOPPED, FAILED = "running", "done", "stopped", "failed"
RENAME, REPLAYGAIN, TIDY = "Rename", "ReplayGain", "Tidy"


@dataclass
class Job:
    id: int
    kind: str  # RENAME, REPLAYGAIN or TIDY
    folder: Path  # the folder tree it locks while it runs
    state: str = RUNNING
    status: str = "Starting…"  # one line: the album running, or how it ended
    progress: tuple[int, int] | None = None  # (done, total) albums
    started: datetime = field(default_factory=datetime.now)
    finished: datetime | None = None
    reports: dict = field(default_factory=dict)  # "rename": RenameReport, "replaygain": Finished

    @property
    def running(self) -> bool:
        return self.state == RUNNING


class JobList(QObject):
    changed = Signal(int)  # a job was added or changed (its id)
    removed = Signal(int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.jobs: list[Job] = []
        self._next_id = 1

    def add(self, kind: str, folder: Path) -> Job:
        job = Job(self._next_id, kind, Path(folder))
        self._next_id += 1
        self.jobs.append(job)
        self.changed.emit(job.id)
        return job

    def get(self, job_id: int) -> Job | None:
        return next((job for job in self.jobs if job.id == job_id), None)

    def update(self, job_id: int, *, status: str | None = None, progress: tuple[int, int] | None = None) -> None:
        job = self.get(job_id)
        if job is None or not job.running:
            return
        if status is not None:
            job.status = status
        if progress is not None:
            job.progress = progress
        self.changed.emit(job_id)

    def report(self, job_id: int, name: str, report) -> None:
        job = self.get(job_id)
        if job is not None:
            job.reports[name] = report
            self.changed.emit(job_id)

    def finish(self, job_id: int, state: str, status: str) -> None:
        job = self.get(job_id)
        if job is None:
            return
        job.state, job.status, job.finished = state, status, datetime.now()
        self.changed.emit(job_id)

    @property
    def running(self) -> list[Job]:
        return [job for job in self.jobs if job.running]

    def clear_finished(self) -> None:
        for job in [job for job in self.jobs if not job.running]:
            self.jobs.remove(job)
            self.removed.emit(job.id)
