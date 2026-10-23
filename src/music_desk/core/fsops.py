"""File-system operations the app may perform in the library (AGENTS.md Ground rule 1).

The app never deletes: junk files go to the Recycle Bin, and only after the user confirmed.
"""

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import natsort
from send2trash import send2trash


class CannotRecycle(OSError):
    """Windows has no Recycle Bin for this item, so sending it there would delete it for good."""


def send_to_recycle_bin(path: Path, check: Callable[[Path], str | None] | None = None,
                        send: Callable[[str], None] = send2trash) -> None:
    """Recycle one file or folder, or raise CannotRecycle when Windows can't (SPEC §19). send2trash itself can't
    refuse: where there is no Recycle Bin, it deletes permanently without an error (measured on a network path)."""
    reason = (check or can_recycle)(path)
    if reason is not None:
        raise CannotRecycle(reason)
    send(str(path))


_DRIVE_KINDS = {0: "a drive of an unknown kind", 1: "not a drive", 2: "a removable drive", 4: "a network location",
                5: "a CD or DVD drive", 6: "a RAM disk"}
_DRIVE_FIXED = 3


def can_recycle(path: Path) -> str | None:
    """None when Windows would send `path` to the Recycle Bin, else why not, in a few words.

    Measured (SPEC §19): a network path is REMOTE for GetDriveTypeW and fails SHQueryRecycleBinW. Inferred from
    the Recycle Bin's own settings (per volume, under Explorer\\BitBucket\\Volume): NukeOnDelete=1 turns the bin
    off, and a file bigger than MaxCapacity (MB) is deleted at once."""
    if os.name != "nt":
        return None
    import ctypes
    import winreg
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    buffer = ctypes.create_unicode_buffer(1024)
    if not kernel32.GetVolumePathNameW(wintypes.LPCWSTR(str(path)), buffer, len(buffer)):
        return f"Windows can't tell which drive {path} is on"
    root = buffer.value
    kind = kernel32.GetDriveTypeW(wintypes.LPCWSTR(root))
    if kind != _DRIVE_FIXED:
        return f"{root} is {_DRIVE_KINDS.get(kind, 'a drive of an unknown kind')}, which has no Recycle Bin"

    class SHQUERYRBINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("i64Size", ctypes.c_longlong), ("i64NumItems", ctypes.c_longlong)]

    info = SHQUERYRBINFO(cbSize=ctypes.sizeof(SHQUERYRBINFO))
    if ctypes.windll.shell32.SHQueryRecycleBinW(wintypes.LPCWSTR(root), ctypes.byref(info)) != 0:
        return f"{root} has no Recycle Bin"

    if not kernel32.GetVolumeNameForVolumeMountPointW(wintypes.LPCWSTR(root), buffer, len(buffer)):
        return None  # no volume name to look up its settings: Windows' defaults apply (the bin is on)
    guid = buffer.value.rstrip("\\").rsplit("Volume", 1)[-1]  # \\?\Volume{…}\ -> {…}
    key_path = rf"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume\{guid}"
    settings = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            for name in ("NukeOnDelete", "MaxCapacity"):
                try:
                    settings[name] = winreg.QueryValueEx(key, name)[0]
                except OSError:
                    pass  # not set: Windows' default
    except OSError:
        return None  # never configured: Windows' defaults apply
    if settings.get("NukeOnDelete") == 1:
        return f"the Recycle Bin is turned off on {root}"
    limit = settings.get("MaxCapacity")
    if limit is not None and path.is_file() and path.stat().st_size > limit * 1024 * 1024:
        return f"{path.name} is bigger than the Recycle Bin of {root} allows ({limit:,} MB)"
    return None


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
