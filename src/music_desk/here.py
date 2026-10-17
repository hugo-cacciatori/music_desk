"""MusicDeskHere.exe (SPEC §29): what the `musicdesk` keyword in File Explorer's address bar runs.

Explorer starts it with the folder it shows as the working directory; it starts MusicDesk.exe on that folder and
exits. MusicDesk itself never guesses a folder from its own working directory. Plain Python, no Qt: it starts fast.
"""

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from music_desk.foreground import allow_foreground as default_allow_foreground

APP_EXE = "MusicDesk.exe"


def main(cwd: str | None = None, executable: str | None = None, launch: Callable[..., object] = subprocess.Popen,
         allow_foreground: Callable[[], None] = default_allow_foreground) -> int:
    folder = cwd or os.getcwd()
    app = Path(executable or sys.executable).with_name(APP_EXE)
    allow_foreground()  # MusicDesk may hand the folder to a running copy, which then takes the focus
    launch([str(app), folder], cwd=str(app.parent), close_fds=True)
    return 0
