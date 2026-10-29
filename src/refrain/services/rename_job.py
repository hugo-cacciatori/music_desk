"""The Rename tool's background work (SPEC §10 A): planning (the preview reads every file's tags) and applying,
one at a time, on a worker thread, so the UI never waits.

Applying holds a lock on the folder tree for its whole run (owner "Rename"), taken on the caller's thread
so a conflict is refused at once: tag saves and inline renames under that folder are refused meanwhile, and a
running job elsewhere in the tree refuses this one. core.rename.apply_plan re-checks every item, so a file
changed since the preview is skipped, never renamed blindly.
"""

import dataclasses
import logging
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from refrain.core import rename
from refrain.core.cancel import CancelToken
from refrain.core.path_locks import PathLocks
from refrain.core.rename import RenameOptions, RenamePlan, RenameReport
from refrain.services.job_runner import JobRunner

log = logging.getLogger(__name__)

OWNER = "Rename"


@dataclass(frozen=True)
class Planned:
    token: int
    plan: RenamePlan | None
    error: str | None = None
    cancelled: bool = False


@dataclass(frozen=True)
class Applied:
    token: int
    report: RenameReport | None
    error: str | None = None


class RenameRunner(JobRunner):
    planned = Signal(object)  # Planned
    applied = Signal(object)  # Applied

    def __init__(self, locks: PathLocks, parent: QObject | None = None) -> None:
        super().__init__(locks, "rename-tool", parent)
        self.running = False  # applying (a preview isn't a job: it changes nothing)

    def plan(self, folder: str | Path, options: RenameOptions, selection: list[Path] | None = None) -> int:
        """Start a preview. A selection renames only those files and their sidecars: no subfolders."""
        if selection:
            options = dataclasses.replace(options, recursive=False)
        return self._plan(folder, lambda cancel: rename.plan_renames(folder, options, selection=selection,
                                                                     cancel=cancel))

    def plan_paths(self, paths: list[Path], options: RenameOptions) -> int:
        """Start a preview of folders and files together (the right-click menu, SPEC §31)."""
        return self._plan(paths[0] if paths else "", lambda cancel: rename.plan_paths(paths, options, cancel))

    def _plan(self, folder: str | Path, make) -> int:
        self._cancel.cancel()  # a newer preview replaces an older one still reading tags
        self._token += 1
        token, cancel = self._token, CancelToken()
        self._cancel = cancel

        def run() -> None:
            try:
                plan = make(cancel)
            except rename.PlanCancelled:
                self.planned.emit(Planned(token, None, cancelled=True))
                return
            except Exception as e:  # noqa: BLE001 - shown in the preview
                log.exception("rename preview of %s failed", folder)
                self.planned.emit(Planned(token, None, f"{type(e).__name__}: {e}"))
                return
            self.planned.emit(Planned(token, plan))

        self._submit(run)
        return token

    def apply(self, plan: RenamePlan) -> int:
        """Run a previewed plan under a lock on its folder tree. Raises LockedError at once if a job holds it."""
        lock = self.locks.acquire(plan.trees, OWNER)
        self._token += 1
        token, cancel = self._token, CancelToken()
        self._cancel = cancel
        self.running = True

        def run() -> None:
            try:
                report = rename.apply_plan(plan, cancel=cancel)
            except Exception as e:  # noqa: BLE001 - shown in the result
                log.exception("renaming in %s failed", plan.folder)
                self.applied.emit(Applied(token, None, f"{type(e).__name__}: {e}"))
                return
            finally:
                self.locks.release(lock)
                self.running = False
            log.info("rename tool in %s: %d renamed, %d sidecars, %d skipped", plan.folder,
                     len(report.renamed), len(report.sidecars_renamed), len(report.skipped))
            self.applied.emit(Applied(token, report))

        if not self._submit(run):
            self.locks.release(lock)
            self.running = False
        return token

    def cancel(self) -> None:
        """Stop the preview or the run in progress (a run stops between two files)."""
        self._cancel.cancel()

