"""rsgain subprocess wrapper: the startup self-check and the album scan (SPEC §13).

rsgain runs with cwd set to the album folder and relative file names (the app never changes its own cwd), at
below-normal priority and without a console window. Its output is read as it comes, and a cancel kills it.
"""

import logging
import re
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from music_desk.core.cancel import CancelToken

log = logging.getLogger(__name__)

VERSION_TIMEOUT_S = 10
# CreateProcess limit is 32,767 characters; stay a little under it.
MAX_COMMAND_LINE = 32_000
SCAN_ARGS = ("custom", "-s", "i")  # the script's RSGAIN_SCAN_ARGS: scan and write the tags
POLL_INTERVAL_S = 0.1


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


@dataclass(frozen=True)
class ScanResult:
    returncode: int | None
    stdout: str
    stderr: str
    duration: float
    cancelled: bool = False
    error: str | None = None  # "not found", "command line too long": rsgain didn't run

    @property
    def ok(self) -> bool:
        return self.error is None and not self.cancelled and self.returncode == 0


def scan_command(exe: Path, files: Sequence[str], target: str, args: Sequence[str] = SCAN_ARGS) -> list[str]:
    return [str(exe), *args, "-l", target, *files]


def scan(files: Sequence[str], folder: Path, target: str, exe: Path, args: Sequence[str] = SCAN_ARGS,
         cancel: CancelToken | None = None, on_line: Callable[[str], None] | None = None) -> ScanResult:
    """Scan `files` (names relative to `folder`) as one album and write their ReplayGain tags."""
    command = scan_command(exe, files, target, args)
    if len(subprocess.list2cmdline(command)) > MAX_COMMAND_LINE:
        return ScanResult(None, "", "", 0.0, error="command line too long")

    started = time.monotonic()
    try:
        process = subprocess.Popen(
            command, cwd=folder, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS,
        )
    except FileNotFoundError:
        return ScanResult(None, "", "", 0.0, error="not found")

    chunks: dict[str, list[str]] = {"stdout": [], "stderr": []}

    def read(name: str, stream) -> None:
        for line in stream:
            chunks[name].append(line)
            if on_line is not None and name == "stdout":
                text = strip_ansi(line)
                if text:
                    on_line(text)

    readers = [threading.Thread(target=read, args=(name, getattr(process, name)), daemon=True)
               for name in ("stdout", "stderr")]
    for reader in readers:
        reader.start()

    cancelled = False
    while process.poll() is None:
        if cancel is not None and cancel.cancelled:
            process.kill()
            cancelled = True
            break
        time.sleep(POLL_INTERVAL_S)
    process.wait()
    for reader in readers:
        reader.join()
    log.info("rsgain %s in %s: exit %s%s", " ".join(args), folder, process.returncode,
             " (cancelled)" if cancelled else "")
    return ScanResult(process.returncode, "".join(chunks["stdout"]), "".join(chunks["stderr"]),
                      time.monotonic() - started, cancelled=cancelled)
