"""File-system operations the app may perform in the library (CLAUDE.md Ground rule 1).

The app never deletes: junk files go to the Recycle Bin, and only after the user confirmed.
"""

import os
from pathlib import Path

import natsort
from send2trash import send2trash


def send_to_recycle_bin(path: Path) -> None:
    send2trash(str(path))


def is_same_file(a: Path, b: Path) -> bool:
    """True if both paths name the same file (on Windows, a case-only rename points at the same file)."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


natural_key = natsort.os_sort_keygen()
"""Sort key giving File Explorer's order ("Track 2" before "Track 10")."""
