"""Logs event-loop stalls (SPEC §18, Ground rule 2): a timer that should fire every few milliseconds measures how
late it is. Turned on when the app runs from source, so a UI-thread block shows up in the log during development.
"""

import logging
import time

from PySide6.QtCore import QObject, QTimer

log = logging.getLogger(__name__)

STALL_MS = 50  # Ground rule 2's target
INTERVAL_MS = 20


class StallWatch(QObject):
    def __init__(self, parent: QObject | None = None, threshold_ms: int = STALL_MS,
                 interval_ms: int = INTERVAL_MS) -> None:
        super().__init__(parent)
        self.threshold_ms = threshold_ms
        self.interval_ms = interval_ms
        self.worst_ms = 0.0
        self._last = time.perf_counter()
        self._timer = QTimer(self, interval=interval_ms)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self) -> None:
        now = time.perf_counter()
        late = (now - self._last) * 1000 - self.interval_ms
        self._last = now
        if late > self.threshold_ms:
            self.worst_ms = max(self.worst_ms, late)
            log.warning("the event loop stalled for %.0f ms (the target is under %d ms)", late, self.threshold_ms)
