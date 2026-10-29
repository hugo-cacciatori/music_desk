"""Handing files to Windows: opening with the default app, showing in File Explorer, playing through a temporary
playlist. It can stall (a slow drive, a busy shell), so it runs off the UI thread, one call at a time, and a failure
comes back as `failed` for the window to show.

One per window: the main window, a right-click task's window (SPEC §35).
"""

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from refrain.core import fsops

log = logging.getLogger(__name__)


class Shell(QObject):
    failed = Signal(str, str, bool)  # message, a path to show in File Explorer instead (or ""), whether it is a folder

    def __init__(self, name: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # Replaced in tests, which never launch anything.
        self.launch: Callable[[str | Path], None] = fsops.open_with_default_app
        self.show_in_explorer: Callable[[str | Path, bool], None] = fsops.open_in_explorer
        self.playlist_dir: Path | None = None  # None: %LOCALAPPDATA%\Refrain\playlists
        self._executor = ThreadPoolExecutor(1, thread_name_prefix=name)

    def submit(self, fn: Callable[[], object]) -> None:
        """Run on the shell thread, for work that reports its own outcome."""
        self._executor.submit(fn)

    def run(self, fn: Callable[[], object], message: str, fallback: str = "", fallback_is_dir: bool = False) -> None:
        """Run on the shell thread; an OSError is logged and `message` is emitted, with a path to show instead."""
        def run() -> None:
            try:
                fn()
            except OSError as e:
                log.warning("%s (%s)", message, e)
                self.failed.emit(message, fallback, fallback_is_dir)
        self._executor.submit(run)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)
