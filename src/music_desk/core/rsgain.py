"""rsgain subprocess wrapper. M0 only has the startup self-check; the scan runner comes at M1."""

import logging
import re
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

VERSION_TIMEOUT_S = 10


def strip_ansi(text: str | None) -> str:
    """Remove the terminal colour codes from rsgain's output."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text or "").strip()


def rsgain_version(exe: Path) -> str | None:
    """First line of `rsgain --version`, or None if rsgain can't run (the ReplayGain tools are then disabled)."""
    try:
        result = subprocess.run(
            [str(exe), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=VERSION_TIMEOUT_S,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("rsgain self-check failed for %s: %s", exe, e)
        return None
    if result.returncode != 0:
        log.warning("rsgain self-check: %s exited with code %s: %s", exe, result.returncode,
                    strip_ansi(result.stderr))
        return None
    # The first line is "rsgain 3.6 - using:", followed by the library versions.
    lines = strip_ansi(result.stdout).splitlines()
    return lines[0].removesuffix("- using:").strip() if lines else None
