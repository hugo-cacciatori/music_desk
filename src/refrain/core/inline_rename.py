"""Inline rename of one file or folder (SPEC §11): validation, the rename itself with the audio file's .lrc
sidecar, and Undo. A rename is one of the app's few allowed changes to the library (Ground rule 1).

Plain Python; the app runs these on a worker thread. A rename holds a path lock while it runs, so it can't
collide with a running job or a tag save.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from refrain.core.formats import AUDIO_EXTENSIONS
from refrain.core.fsops import is_same_file
from refrain.core.masks import FORBIDDEN_CHARACTERS
from refrain.core.path_locks import LockedError, PathLocks

RESERVED_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)),
                            *(f"LPT{n}" for n in range(1, 10))})
MAX_PATH = 259  # characters: the classic Windows limit, which older players and tools still have
RENAME_OWNER = "a rename"


class RenameError(Exception):
    """A plain message for the user; nothing was renamed."""


@dataclass
class RenameResult:
    steps: list[tuple[Path, Path]]  # (old, new), in the order they were done
    warnings: list[str] = field(default_factory=list)

    @property
    def sidecar(self) -> tuple[Path, Path] | None:
        return self.steps[1] if len(self.steps) > 1 else None


def validate_name(path: Path, new_name: str) -> str | None:
    """Why `new_name` can't be used for `path` (None if it can). Checks the disk for a clash."""
    if not new_name.strip():
        return "A name can't be empty."
    if FORBIDDEN_CHARACTERS.search(new_name):
        return 'A name can\'t contain any of / \\ ? % * : | " < > or control characters.'
    if new_name[-1] in " .":
        return "A name can't end with a space or a dot."
    if new_name.split(".")[0].strip().upper() in RESERVED_NAMES:
        return f"{new_name} is a name Windows reserves."
    target = path.with_name(new_name)
    if len(str(target)) > MAX_PATH:
        return f"The full path would be longer than {MAX_PATH} characters."
    if target.exists() and not is_same_file(path, target):
        return f"{new_name} already exists here."
    return None


def sidecar_for(path: Path) -> Path | None:
    """The same-stem .lrc that follows an audio file's rename: only when no other audio file shares the stem
    (SPEC §10)."""
    if path.suffix.lower() not in AUDIO_EXTENSIONS:
        return None
    try:
        names = os.listdir(path.parent)
    except OSError:
        return None
    stem = path.stem.casefold()
    owners = [n for n in names if Path(n).stem.casefold() == stem and Path(n).suffix.lower() in AUDIO_EXTENSIONS]
    lrcs = [n for n in names if Path(n).stem.casefold() == stem and Path(n).suffix.lower() == ".lrc"]
    if len(owners) == 1 and len(lrcs) == 1:
        return path.parent / lrcs[0]
    return None


def rename_entry(path: str | Path, new_name: str, locks: PathLocks | None = None) -> RenameResult:
    """Rename a file or folder (a case-only rename too), and an audio file's .lrc sidecar with it.
    Raises RenameError with the reason when nothing was renamed."""
    path = Path(path)
    if not path.exists():
        raise RenameError(f"{path.name} isn't there any more.")
    problem = validate_name(path, new_name)
    if problem:
        raise RenameError(problem)
    target = path.with_name(new_name)
    if target.name == path.name:
        return RenameResult([])
    try:
        token = locks.acquire([path], RENAME_OWNER) if locks else None
    except LockedError as e:
        raise RenameError(f"{path.name} can't be renamed while {e.owner} is running.") from None
    try:
        sidecar = sidecar_for(path) if not path.is_dir() else None
        try:
            path.rename(target)
        except OSError as e:
            raise RenameError(f"{path.name} couldn't be renamed: {e.strerror or e}") from None
        result = RenameResult([(path, target)])
        if sidecar is not None and Path(new_name).stem != path.stem:
            lrc_target = sidecar.with_name(Path(new_name).stem + sidecar.suffix)
            if lrc_target.exists() and not is_same_file(sidecar, lrc_target):
                result.warnings.append(f"{sidecar.name} wasn't renamed: {lrc_target.name} already exists.")
            else:
                try:
                    sidecar.rename(lrc_target)
                    result.steps.append((sidecar, lrc_target))
                except OSError as e:
                    result.warnings.append(f"{sidecar.name} couldn't be renamed: {e.strerror or e}")
        return result
    finally:
        if locks is not None and token is not None:
            locks.release(token)


def undo(result: RenameResult, locks: PathLocks | None = None) -> RenameResult:
    """Put the names back, last rename first (the sidecar before its audio file, so the audio file's rename
    doesn't move the sidecar a second time). Raises RenameError if the main item can't go back."""
    done: list[tuple[Path, Path]] = []
    warnings = []
    for old, new in reversed(result.steps):
        try:
            back = rename_entry(new, old.name, locks)
        except RenameError as e:
            if (old, new) == result.steps[0]:
                raise RenameError(f"Undo failed: {e}") from None
            warnings.append(f"Undo of {new.name}: {e}")
            continue
        done.extend(back.steps)
    return RenameResult(done, warnings)
