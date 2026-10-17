"""Tag reads for the side panel, on a worker thread (Ground rule 2: no tag I/O on the UI thread). Writes go
through TagWriter.

Every request carries a token; the panel ignores results for a token it no longer waits for (the selection moved
on). Covers are decoded into QImage here too, so the UI thread never decodes a large picture.
"""

import logging
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Qt, Signal
from PySide6.QtGui import QImage, QImageIOHandler, QImageReader

from music_desk.core import masks, metadata, preview
from music_desk.core.fsops import open_shared
from music_desk.core.metadata import Snapshot
from music_desk.core.tags import TaggedFile

log = logging.getLogger(__name__)

COVER_SIZE = 220  # pixels: the decoded cover is scaled once, off the UI thread
PREVIEW_WIDTH = 1200  # pixels: previewed images are decoded at most this wide; the page scales them to fit


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
    cover: QImage | None = None  # the embedded front cover, when every file has the same one


@dataclass(frozen=True)
class ImageRead:
    token: int
    path: str
    data: bytes | None
    image: QImage | None
    error: str | None = None


@dataclass(frozen=True)
class PreviewRead:
    """A file preview (SPEC §20): its text, or its image scaled down, or why it can't be shown."""

    token: int
    path: str
    kind: str  # "text" or "image"
    size: int = 0
    mtime: float = 0.0
    text: preview.TextPreview | None = None
    image: QImage | None = None
    pixels: tuple[int, int] | None = None  # the image's own size, as shown (EXIF orientation applied)
    error: str | None = None


class TagIO(QObject):
    loaded = Signal(object)  # Loaded, after a read
    previewRead = Signal(object)  # PreviewRead
    loadedMany = Signal(object)  # LoadedMany, after a batch read (multi-selection)
    imageRead = Signal(object)  # ImageRead: a picture chosen as the new cover
    folderCover = Signal(int, object, str)  # token, QImage or None, where it comes from
    renameFields = Signal(int, object, str)  # token, the rename fields (or None), the file's extension

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = ThreadPoolExecutor(1, thread_name_prefix="tag-io")
        self._token = 0
        self._closed = False
        # The user's file name masks and replacement (SPEC §21), for the rename hint; the window sets it.
        self.name_masks: Callable[[], tuple[Sequence[str], str]] = lambda: (masks.DEFAULT_MASKS,
                                                                            masks.DEFAULT_REPLACEMENT)

    def next_token(self) -> int:
        self._token += 1
        return self._token

    def load(self, path: str | Path) -> int:
        token = self.next_token()
        name_masks, replacement = self.name_masks()  # read on the UI thread, where the setting changes
        self._submit(lambda: self.loaded.emit(self._read(token, Path(path), name_masks, replacement)))
        return token

    def load_many(self, paths: Sequence[str | Path]) -> int:
        token = self.next_token()

        def run() -> None:
            snapshots, failed = [], []
            for path in paths:
                try:
                    snapshots.append(metadata.read_snapshot(path))
                except Exception as e:  # noqa: BLE001 - listed as unreadable
                    failed.append((str(path), f"{type(e).__name__}: {e}"))
            covers = {metadata.embedded_cover(s) for s in snapshots}
            common = covers.pop() if len(covers) == 1 else None
            self.loadedMany.emit(LoadedMany(token, snapshots, failed, decode_cover(common)))

        self._submit(run)
        return token

    def load_folder_cover(self, folder: str | Path, first_track: str | Path | None) -> int:
        token = self.next_token()

        def run() -> None:
            try:
                found = metadata.folder_picture(folder, first_track)
            except Exception:  # noqa: BLE001 - no cover is shown then
                found = None
            image = decode_cover(found[0]) if found else None
            self.folderCover.emit(token, image, found[1] if found and image is not None else "")

        self._submit(run)
        return token

    def read_rename_fields(self, path: str | Path) -> int:
        """The fields rename masks use, of one file (the File names card's live example)."""
        token = self.next_token()

        def run() -> None:
            try:
                fields = TaggedFile(path, shared=True).rename_fields()
            except Exception:  # noqa: BLE001 - no example then
                fields = None
            self.renameFields.emit(token, fields, Path(path).suffix)

        self._submit(run)
        return token

    def read_image(self, path: str | Path) -> int:
        token = self.next_token()

        def run() -> None:
            try:
                data = metadata.read_image(path)
                image = decode_cover(data)
                if image is None:
                    raise ValueError(f"{Path(path).name} couldn't be decoded as a picture")
            except Exception as e:  # noqa: BLE001 - shown to the user
                self.imageRead.emit(ImageRead(token, str(path), None, None, str(e)))
                return
            self.imageRead.emit(ImageRead(token, str(path), data, image))

        self._submit(run)
        return token

    def read_preview(self, path: str | Path, max_width: int = PREVIEW_WIDTH) -> int:
        """A previewed file, read and decoded off the UI thread: text up to its cap, or an image decoded at most
        `max_width` pixels wide (never upscaled)."""
        token = self.next_token()
        self._submit(lambda: self.previewRead.emit(read_preview(token, Path(path), max_width)))
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
    def _read(token: int, path: Path, name_masks: Sequence[str], replacement: str) -> Loaded:
        try:
            snapshot = metadata.read_snapshot(path, name_masks, replacement)
        except Exception as e:  # noqa: BLE001 - an unreadable file is shown as such
            return Loaded(token, None, None, f"{type(e).__name__}: {e}")
        return Loaded(token, snapshot, decode_cover(snapshot.cover))


def read_preview(token: int, path: Path, max_width: int = PREVIEW_WIDTH) -> PreviewRead:
    kind = preview.kind(path) or "text"
    try:
        stat = path.stat()
    except OSError as e:
        return PreviewRead(token, str(path), kind, error=f"Can't read {path.name}: {e.strerror or e}")
    base = PreviewRead(token, str(path), kind, size=stat.st_size, mtime=stat.st_mtime)
    if kind == "text":
        try:
            return replace(base, text=preview.read_text(path))
        except OSError as e:
            return replace(base, error=f"Can't read {path.name}: {e.strerror or e}")
    if stat.st_size > preview.IMAGE_LIMIT:
        return replace(base, error=f"Too large to preview ({stat.st_size / 1024 / 1024:.0f} MB). Open it "
                                   "instead.")
    try:
        with open_shared(path) as f:
            data = f.read()
    except OSError as e:
        return replace(base, error=f"Can't read {path.name}: {e.strerror or e}")
    image, pixels, error = decode_preview_image(data, max_width)
    return replace(base, image=image, pixels=pixels, error=error)


def decode_preview_image(data: bytes, max_width: int) -> tuple[QImage | None, tuple[int, int] | None, str | None]:
    """(image, its own size, error): decoded from memory at most `max_width` wide, with EXIF orientation."""
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    reader.setAutoTransform(True)
    size = reader.size()
    if not size.isValid():
        return None, None, f"Can't show this image: {reader.errorString()}"
    if reader.transformation() & QImageIOHandler.Transformation.TransformationRotate90:
        size = size.transposed()  # a portrait photo stored sideways
    if size.width() > max_width:
        scaled = size.scaled(max_width, 1_000_000, Qt.AspectRatioMode.KeepAspectRatio)  # width-bound
        if reader.transformation() & QImageIOHandler.Transformation.TransformationRotate90:
            scaled = scaled.transposed()  # the reader scales before it rotates
        reader.setScaledSize(scaled)
    image = reader.read()
    if image.isNull():
        return None, None, f"Can't show this image: {reader.errorString()}"
    return image, (size.width(), size.height()), None


def decode_cover(data: bytes | None) -> QImage | None:
    if not data:
        return None
    image = QImage()
    if not image.loadFromData(data):
        return None
    return image.scaled(COVER_SIZE, COVER_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
