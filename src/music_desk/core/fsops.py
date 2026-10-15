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


def _shared_opener(path: str, flags: int) -> int:
    """os.open with FILE_SHARE_DELETE (Windows): the file can still be deleted or renamed while it is read."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                       wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    generic_read, share_all, open_existing, normal = 0x80000000, 0x7, 3, 0x80
    handle = create(path, generic_read, share_all, None, open_existing, normal, None)
    if handle == wintypes.HANDLE(-1).value:
        error = ctypes.get_last_error()
        raise OSError(error, ctypes.FormatError(error).strip(), path)
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except OSError:
        kernel32.CloseHandle(handle)
        raise


def open_shared(path: str | Path):
    """Open a file for reading without blocking others: Python's open() doesn't share delete access on Windows,
    so a file being read couldn't be deleted or renamed in Explorer at that moment. The file object keeps its
    name (mutagen recognizes some formats by it)."""
    if os.name != "nt":
        return open(path, "rb")  # noqa: SIM115 - the caller closes it
    return open(str(path), "rb", opener=_shared_opener)  # noqa: SIM115 - the caller closes it
