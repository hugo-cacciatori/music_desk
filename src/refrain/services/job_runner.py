"""What the tools' runners share (SPEC §16): one job thread each with its cancel token, and log lines batched on
their way to the UI."""

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, SignalInstance

from refrain.core.cancel import CancelToken
from refrain.core.path_locks import PathLocks

LINE_INTERVAL = 0.1  # seconds between batches of log lines


class JobRunner(QObject):
    """A tool's runner: its work runs on its own thread, one call at a time; `close` cancels and waits."""

    def __init__(self, locks: PathLocks, thread_name: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.locks = locks
        self._pool = ThreadPoolExecutor(1, thread_name_prefix=thread_name)
        self._token = 0
        self._cancel = CancelToken()
        self._closed = False

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


class BatchedLines:
    """core.replaygain.Reporter → a runner's `lines` signal, batched (rsgain's reader thread and the job thread both
    report). `on_progress`, when given, gets each progress report after the lines before it."""

    def __init__(self, lines: SignalInstance, on_progress: Callable[[int, int, str], None] | None = None) -> None:
        self._signal = lines
        self._on_progress = on_progress
        self._lines: list[str] = []
        self._last = 0.0
        self._lock = threading.Lock()

    def line(self, text: str) -> None:
        with self._lock:
            self._lines.append(text)
            if time.monotonic() - self._last < LINE_INTERVAL:
                return
            lines, self._lines, self._last = self._lines, [], time.monotonic()
        self._signal.emit(lines)

    def flush(self) -> None:
        with self._lock:
            lines, self._lines = self._lines, []
        if lines:
            self._signal.emit(lines)

    def info(self, message: str) -> None:
        self.line(message)

    def warn(self, message: str) -> None:
        self.line(f"WARNING  {message}")

    def error(self, message: str) -> None:
        self.line(f"ERROR  {message}")

    def progress(self, done: int, total: int, label: str) -> None:
        if self._on_progress is not None:
            self.flush()
            self._on_progress(done, total, label)
