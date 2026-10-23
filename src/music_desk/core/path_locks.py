"""Path locks (SPEC §5 jobs panel, §7, §16): a running job owns its folder tree, and a tag save owns its file.

- A lock on a folder covers everything below it. Paths compare ignoring case, as Windows does.
- Two locks can't overlap: a second job on the same tree, or a save of a file a job owns, is refused with the
  owner's name, so the user is told what to wait for.
- Listeners are called after every change (from the thread that changed it), so the UI can grey out locked
  files.

Plain Python and thread-safe: jobs, the tag writer and the UI share one instance.
"""

import logging
import os
import threading
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)


class LockedError(Exception):
    def __init__(self, path: str, owner: str) -> None:
        super().__init__(f"{path} is in use by {owner}")
        self.path = path
        self.owner = owner


def _key(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _overlaps(a: str, b: str) -> bool:
    """True if one key is the other or lies inside it."""
    if a == b:
        return True
    return a.startswith(b.rstrip(os.sep) + os.sep) or b.startswith(a.rstrip(os.sep) + os.sep)


class PathLocks:
    def __init__(self) -> None:
        self._locks: dict[int, tuple[str, list[str], list[str]]] = {}  # token -> (owner, keys, paths)
        self._next = 0
        self._mutex = threading.Lock()
        self._listeners: list[Callable[[], None]] = []

    def add_listener(self, listener: Callable[[], None]) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def owner(self, path: str | Path) -> str | None:
        """Who holds a lock covering this path (the path itself, or a folder above it), or None."""
        key = _key(path)
        with self._mutex:
            for owner, keys, _paths in self._locks.values():
                if any(key == k or key.startswith(k.rstrip(os.sep) + os.sep) for k in keys):
                    return owner
        return None

    def conflict(self, path: str | Path) -> str | None:
        """Who holds a lock overlapping this path (above it, on it or inside it): what `acquire` would refuse."""
        key = _key(path)
        with self._mutex:
            for owner, keys, _paths in self._locks.values():
                if any(_overlaps(key, other) for other in keys):
                    return owner
        return None

    def acquire(self, paths: Iterable[str | Path], owner: str) -> int:
        """Lock these paths (files or folder trees) for `owner`. Raises LockedError on any overlap."""
        paths = [str(p) for p in paths]
        keys = [_key(p) for p in paths]
        with self._mutex:
            for other_owner, other_keys, _other_paths in self._locks.values():
                for key, path in zip(keys, paths, strict=True):
                    if any(_overlaps(key, other_key) for other_key in other_keys):
                        raise LockedError(path, other_owner)
            self._next += 1
            token = self._next
            self._locks[token] = (owner, keys, paths)
        self._notify()
        return token

    def release(self, token: int) -> None:
        with self._mutex:
            removed = self._locks.pop(token, None)
        if removed is not None:
            self._notify()

    @contextmanager
    def lock(self, paths: Iterable[str | Path], owner: str):
        token = self.acquire(paths, owner)
        try:
            yield token
        finally:
            self.release(token)

    def held(self) -> list[tuple[str, list[str]]]:
        with self._mutex:
            return [(owner, list(paths)) for owner, _keys, paths in self._locks.values()]

    def _notify(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:  # noqa: BLE001 - a broken listener must never break taking or releasing a lock
                log.exception("path lock listener failed")
