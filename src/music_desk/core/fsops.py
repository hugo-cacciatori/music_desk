"""File-system operations the app may perform in the library (CLAUDE.md Ground rule 1).

The app never deletes: junk files go to the Recycle Bin, and only after the user confirmed.
"""

import os
import subprocess
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


# -- handing things to Windows (the app itself never opens or plays files) --

def open_with_default_app(path: str | Path, launcher=os.startfile) -> None:
    """Open a file or folder the way a double-click in Explorer does. Raises OSError, e.g. when no app is
    associated with the file type."""
    launcher(str(path))


def explorer_command(path: str | Path, is_dir: bool) -> str:
    """The Explorer command line: a folder opens itself; a file opens its folder with the file selected."""
    path = os.path.normpath(str(path))
    return f'explorer "{path}"' if is_dir else f'explorer /select,"{path}"'


def open_in_explorer(path: str | Path, is_dir: bool, run=None) -> None:
    """Show a file or folder in File Explorer. `run` gets the command line (a string: Explorer parses
    /select,"…" itself, so it can't go through list quoting)."""
    command = explorer_command(path, is_dir)
    (run or (lambda cmd: subprocess.Popen(cmd)))(command)
