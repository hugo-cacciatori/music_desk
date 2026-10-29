"""Where the app finds its bundled resources and keeps its own files.

The app's own files (index, settings, logs, temporary playlists) live only in %LOCALAPPDATA%\\Refrain; up to 0.11
they were in %LOCALAPPDATA%\\MusicDesk, which the installed app moves on start (SPEC §36 task 6).
"""

import os
import sys
from pathlib import Path

APP_DIR_NAME = "Refrain"
OLD_APP_DIR_NAME = "MusicDesk"  # the app's name up to 0.11


def is_frozen() -> bool:
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def resources_root() -> Path:
    """The bundled `resources/` folder: inside the PyInstaller bundle when frozen, else the project's."""
    if is_frozen():
        return Path(sys._MEIPASS) / "resources"  # pyright: ignore[reportAttributeAccessIssue] - set by PyInstaller
    return Path(__file__).resolve().parents[2] / "resources"


def resource_path(*parts: str) -> Path:
    return resources_root().joinpath(*parts)


def icon_path() -> Path:
    """The app icon (drawn by packaging/make_icon.py), for the windows; the exe embeds the same file."""
    return resource_path("icons", "refrain.ico")


def rsgain_path() -> Path:
    """The bundled rsgain. Never configurable (Ground rule 5)."""
    return resource_path("rsgain", "rsgain.exe")


def flac_path() -> Path:
    """The bundled flac, for the cue split (SPEC §22). Never configurable (Ground rule 5)."""
    return resource_path("flac", "flac.exe")


def local_app_data() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local"))


def app_data_dir() -> Path:
    """%LOCALAPPDATA%\\Refrain, created on demand."""
    path = local_app_data() / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = app_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def playlists_dir() -> Path:
    """Temporary playlists for "Play folder" and "Play selection" (SPEC §14)."""
    path = app_data_dir() / "playlists"
    path.mkdir(parents=True, exist_ok=True)
    return path


def library_db_path() -> Path:
    """The index and lyrics cache (SPEC §2)."""
    return app_data_dir() / "library.db"


def move_old_app_data() -> tuple[list[str], list[str]]:
    """Moves MusicDesk's files into Refrain's folder (SPEC §36 task 6): every item of %LOCALAPPDATA%\\MusicDesk that
    Refrain's folder doesn't have yet (the right-click menu may have made its menu\\ first), by a rename on the same
    drive; MusicDesk's folder goes once only empty folders are left in it. Returns the names moved, and those left
    with the reason (a file in use while MusicDesk runs): the next start moves what was left."""
    old = local_app_data() / OLD_APP_DIR_NAME
    if not old.is_dir():
        return [], []
    new = app_data_dir()
    moved, left = [], []
    for item in sorted(old.iterdir()):
        if (new / item.name).exists():
            continue
        try:
            item.rename(new / item.name)
            moved.append(item.name)
        except OSError as error:
            left.append(f"{item.name} ({error.strerror or error})")
    for folder in sorted((p for p in old.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        _remove_if_empty(folder)
    _remove_if_empty(old)
    return moved, left


def _remove_if_empty(folder: Path) -> None:
    try:
        folder.rmdir()  # only an empty folder: never a file
    except OSError:
        pass
