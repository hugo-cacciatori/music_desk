"""Logging to %LOCALAPPDATA%\\MusicDesk\\logs\\app.log (and stderr when running from source)."""

import logging
import sys
from logging.handlers import RotatingFileHandler

from music_desk.paths import is_frozen, logs_dir

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(threadName)s %(name)s: %(message)s"


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(level)
    formatter = logging.Formatter(LOG_FORMAT)

    file_handler = RotatingFileHandler(logs_dir() / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # A --windowed build has no stderr.
    if not is_frozen() and sys.stderr is not None:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)
