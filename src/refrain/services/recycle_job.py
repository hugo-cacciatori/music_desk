"""Recycling the Redundant files window's checked items (SPEC §32): one worker for Clean up and Tidy.

The run holds a lock on the items (its owner: "Clean up" or "Tidy"), taken on the caller's thread so a
conflict is refused at once. Each item is checked again first (gone, or a folder that now holds something else, is
skipped). Where Windows can't recycle, nothing is deleted for good: the item is kept. Stop takes effect between two
items.
"""

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from refrain.core import cleanup, fsops
from refrain.core.cancel import CancelToken
from refrain.core.cleanup import Item
from refrain.core.path_locks import PathLocks
from refrain.core.text import plural

log = logging.getLogger(__name__)


@dataclass
class RecycleReport:
    recycled: list[Item] = field(default_factory=list)
    kept: list[tuple[Item, str]] = field(default_factory=list)  # no Recycle Bin there: never deleted for good
    skipped: list[tuple[Item, str]] = field(default_factory=list)  # changed meanwhile, or not reached after Stop
    failed: list[tuple[Item, str]] = field(default_factory=list)
    cancelled: bool = False

    @property
    def folders(self) -> set[Path]:
        """The folders whose contents changed, for the index."""
        return {item.path.parent for item in self.recycled}


class RecycleRunner(QObject):
    progress = Signal(int, int, str)  # items done, items in all, the item now going
    finished = Signal(object)  # RecycleReport

    def __init__(self, locks: PathLocks, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.locks = locks
        self._pool = ThreadPoolExecutor(1, thread_name_prefix="recycle")
        self._cancel = CancelToken()
        self.running = False

    def run(self, items: list[Item], owner: str, recycle: Callable[[Path], None] = fsops.send_to_recycle_bin) -> None:
        """Recycle the items under one lock. Raises LockedError at once if a job holds any of them."""
        lock = self.locks.acquire([item.path for item in items], owner)
        cancel = self._cancel = CancelToken()
        self.running = True

        def job() -> None:
            report = RecycleReport()
            try:
                for n, item in enumerate(items):
                    if cancel.cancelled:
                        report.cancelled = True
                        report.skipped += [(rest, "not reached: stopped") for rest in items[n:]]
                        break
                    self.progress.emit(n, len(items), item.name)
                    why = cleanup.recheck(item)
                    if why is not None:
                        report.skipped.append((item, why))
                        continue
                    try:
                        recycle(item.path)
                        report.recycled.append(item)
                    except fsops.CannotRecycle as e:
                        report.kept.append((item, f"no Recycle Bin: {e}"))
                    except OSError as e:
                        report.failed.append((item, str(e)))
                self.progress.emit(len(items), len(items), "")
            except Exception as e:  # noqa: BLE001 - the window reports it
                log.exception("recycling failed")
                report.failed.append((Item(Path(), "", "the run itself"), f"{type(e).__name__}: {e}"))
            finally:
                self.locks.release(lock)
                self.running = False
            self.finished.emit(report)

        try:
            self._pool.submit(job)
        except RuntimeError:
            self.locks.release(lock)
            self.running = False

    def cancel(self) -> None:
        self._cancel.cancel()

    def close(self) -> None:
        self._cancel.cancel()
        self._pool.shutdown(wait=True)


def report_text(report: RecycleReport) -> str:
    """The window's report: what went to the Recycle Bin, what was kept or skipped, and why."""

    head = f"Sent {plural(len(report.recycled), 'item')} to the Recycle Bin"
    head += " (stopped)." if report.cancelled else "."
    lines = [head]
    for title, rows in (("Sent to the Recycle Bin", [(item, item.why) for item in report.recycled]),
                        ("Kept", report.kept), ("Skipped", report.skipped), ("Failed", report.failed)):
        if rows:
            lines += ["", f"{title}:"]
            lines += [f"    {item.path}  ({why})" for item, why in rows]
    return "\n".join(lines)
