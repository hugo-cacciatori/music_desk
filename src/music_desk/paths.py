"""Where the app finds its bundled resources and keeps its own files.

The app's own files (index, settings, logs, temporary playlists) live only in %LOCALAPPDATA%\\MusicDesk.
"""

import os
import sys
from pathlib import Path

APP_DIR_NAME = "MusicDesk"


def is_frozen() -> bool:
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def resources_root() -> Path:
    """The bundled `resources/` folder: inside the PyInstaller bundle when frozen, else the project's."""
    if is_frozen():
        return Path(sys._MEIPASS) / "resources"
    return Path(__file__).resolve().parents[2] / "resources"


def resource_path(*parts: str) -> Path:
    return resources_root().joinpath(*parts)


def rsgain_path() -> Path:
    """The bundled rsgain. Never configurable (Ground rule 5)."""
    return resource_path("rsgain", "rsgain.exe")


def app_data_dir() -> Path:
    """%LOCALAPPDATA%\\MusicDesk, created on demand."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    path = Path(base) / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = app_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path
