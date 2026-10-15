"""Tag reads for the side panel, on a worker thread (Ground rule 2: no tag I/O on the UI thread). Writes go
through TagWriter.

Every request carries a token; the panel ignores results for a token it no longer waits for (the selection moved
on). Covers are decoded into QImage here too, so the UI thread never decodes a large picture.
"""

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QImage

from music_desk.core import metadata
from music_desk.core.metadata import Snapshot

log = logging.getLogger(__name__)

COVER_SIZE = 220  # pixels: the decoded cover is scaled once, off the UI thread


@dataclass(frozen=True)
class Loaded:
    token: int
    snapshot: Snapshot | None
    cover: QImage | None
    error: str | None = None


@dataclass(frozen=True)
class LoadedMany:
    token: int
    snapshots: list[Snapshot]  # in the order asked, unreadable files left out
    failed: list[tuple[str, str]]  # (path, error)


class TagIO(QObject):
    loaded = Signal(object)  # Loaded, after a read
    loadedMany = Signal(object)  # LoadedMany, after a batch read (multi-selection)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = ThreadPoolExecutor(1, thread_name_prefix="tag-io")
        self._token = 0
        self._closed = False

    def next_token(self) -> int:
        self._token += 1
        return self._token

    def load(self, path: str | Path) -> int:
        token = self.next_token()
        self._submit(lambda: self.loaded.emit(self._read(token, Path(path))))
        return token

    def load_many(self, paths: list[str | Path]) -> int:
        token = self.next_token()

        def run() -> None:
            snapshots, failed = [], []
            for path in paths:
                try:
                    snapshots.append(metadata.read_snapshot(path))
                except Exception as e:  # noqa: BLE001 - listed as unreadable
                    failed.append((str(path), f"{type(e).__name__}: {e}"))
            self.loadedMany.emit(LoadedMany(token, snapshots, failed))

        self._submit(run)
        return token

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._pool.shutdown(wait=True)

    def _submit(self, fn) -> Future | None:
        if self._closed:
            return None
        return self._pool.submit(fn)

    @staticmethod
    def _read(token: int, path: Path) -> Loaded:
        try:
            snapshot = metadata.read_snapshot(path)
        except Exception as e:  # noqa: BLE001 - an unreadable file is shown as such
            return Loaded(token, None, None, f"{type(e).__name__}: {e}")
        return Loaded(token, snapshot, decode_cover(snapshot.cover))


def decode_cover(data: bytes | None) -> QImage | None:
    if not data:
        return None
    image = QImage()
    if not image.loadFromData(data):
        return None
    return image.scaled(COVER_SIZE, COVER_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
