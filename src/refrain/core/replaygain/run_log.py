"""The run log: what happens during an album run, as the report written to the album's log file, and the
formatting it shares with the other reports."""

import math
import os
import time
import traceback
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Protocol

from refrain.core.replaygain.options import ReplayGainOptions
from refrain.core.text import plural

Clock = Callable[[], datetime]


class Reporter(Protocol):
    def info(self, message: str) -> None: ...
    def warn(self, message: str) -> None: ...
    def error(self, message: str) -> None: ...
    def progress(self, done: int, total: int, label: str) -> None: ...


class NullReporter:
    def info(self, message: str) -> None:
        pass

    def warn(self, message: str) -> None:
        pass

    def error(self, message: str) -> None:
        pass

    def progress(self, done: int, total: int, label: str) -> None:
        pass


def indent(text: str, prefix: str = "    ") -> str:
    return "\n".join((prefix + line).rstrip() for line in text.splitlines())


def format_gain(gain: float | None) -> str:
    return "-" if gain is None else f"{gain:+.2f} dB"


def format_peak_db(peak: float | None) -> str:
    if peak is None:
        return "-"
    return f"{20 * math.log10(peak):.2f} dB" if peak > 0 else "-inf dB"


def files_summary(files: list[str]) -> str:
    """The script's "N (x FLAC, y MP3)", with other formats appended only when present."""
    counts: dict[str, int] = {"FLAC": 0, "MP3": 0}
    for name in files:
        label = os.path.splitext(name)[1].lstrip(".").upper()
        counts[label] = counts.get(label, 0) + 1
    others = sorted(label for label in counts if label not in ("FLAC", "MP3"))
    parts = [f"{counts[label]} {label}" for label in ("FLAC", "MP3", *others)]
    return f"{len(files)} ({', '.join(parts)})"


class RunLog:
    """Collects what happens during an album run and appends it to the album's log file as one block."""

    def __init__(self, folder: Path, files: list[str], options: ReplayGainOptions, exe: Path,
                 reporter: Reporter | None = None, now: Clock = datetime.now, loose: bool = False) -> None:
        self.loose = loose  # a track on its own (SPEC §31): its own album, no subfolders, no log file
        self.now = now
        self.start = now()
        self.start_clock = time.monotonic()
        self.folder = folder
        self.files = files
        self.options = options
        self.exe = exe
        self.reporter = reporter or NullReporter()
        self.scan = "not run"
        self.scan_output = ""
        self.scan_ok = False
        self.tracks: dict[str, dict] = {}  # file -> {"gain", "peak", "final", "note"}
        self.album: tuple[float, float] | None = None
        self.subfolders: list[str] | None = None  # None until that step runs
        self.issues: list[tuple[datetime, str, str]] = []
        self.crashed = False

    def _add(self, level: str, message: str) -> None:
        self.issues.append((self.now(), level, message))

    def warn(self, message: str) -> None:
        self.reporter.warn(message)
        self._add("WARNING", message)

    def error(self, message: str) -> None:
        self.reporter.error(message)
        self._add("ERROR", message)

    def crash(self) -> None:
        self.crashed = True
        self._add("ERROR", "Unexpected crash:\n" + traceback.format_exc().strip())

    def track(self, file: str, gain: float | None = None, peak: float | None = None, note: str = "") -> None:
        self.tracks[file] = {"gain": gain, "peak": peak, "final": gain, "note": note}

    def count(self, level: str) -> int:
        return sum(1 for _, lvl, _ in self.issues if lvl == level)

    def result(self) -> str:
        if self.crashed:
            return "CRASHED"
        if not self.scan_ok:
            return "STOPPED"
        return "OK" if not self.count("ERROR") else "DONE WITH ERRORS"

    def _track_table(self) -> list[str]:
        header = ("Track", "Loudness", "Peak", "rsgain gain", "Final gain", "Note")
        rows = []
        for file in self.files:
            t = self.tracks.get(file, {"gain": None, "peak": None, "final": None, "note": "not processed"})
            loudness = "-" if t["gain"] is None else f"{self.options.target - t['gain']:.2f} LUFS"
            rows.append((file, loudness, format_peak_db(t["peak"]),
                         format_gain(t["gain"]), format_gain(t["final"]), t["note"]))
        widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]
        return [
            "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
            for row in [header, *rows]
        ]

    def text(self) -> str:
        clipping_fixes = sum(1 for t in self.tracks.values() if t["final"] != t["gain"])
        duration = time.monotonic() - self.start_clock

        lines = [
            "=" * 78,
            f"Run {self.start:%Y-%m-%d %H:%M:%S}   target {self.options.target_text} LUFS   rsgain: {self.exe}",
            f"Folder: {self.folder}",
            f"Files: {files_summary(self.files)}",
            f"Scan: {self.scan}",
        ]
        if self.scan_output:
            lines.append(indent(self.scan_output))

        if self.scan_ok:
            lines += ["", *self._track_table(), ""]
            if self.loose:
                lines.append(f"Album: the track's own values, gain {self.album[0]} dB, peak {self.album[1]}"
                             if self.album else "Album: not computed")
            elif self.album:
                lines.append(f"Album: gain {self.album[0]} dB (lowest track gain), "
                             f"peak {self.album[1]} (highest track peak)")
            else:
                lines.append("Album: not computed")
            if not self.loose:
                lines.append("Subfolders:")
                if self.subfolders is None:
                    lines.append("  not processed")
                else:
                    lines += [f"  {line}" for line in self.subfolders] or [
                        "  none matching " + ", ".join(self.options.subfolder_keywords)]

        lines += ["", "Warnings / errors:"]
        if not self.issues:
            lines.append("  none")
        for moment, level, message in self.issues:
            first, *rest = message.splitlines()
            lines.append(f"  {moment:%H:%M:%S} {level:<7}  {first}")
            lines += [f"                    {line}" for line in rest]

        lines += ["", f"Result: {self.result()}. {plural(len(self.files), 'track')}, "
                      f"{plural(clipping_fixes, 'clipping fix', 'clipping fixes')}, "
                      f"{plural(self.count('WARNING'), 'warning')}, "
                      f"{plural(self.count('ERROR'), 'error')}, {duration:.1f} s", ""]
        return "\n".join(lines) + "\n"

    def write(self) -> None:
        """Write the report as the album's log file, replacing the previous run's (Hugo, 2026-10-16; the script
        appended). Within one discography run, the leveling line is added after it (`process_discography`)."""
        with open(self.folder / self.options.log_file, "w", encoding="utf-8") as log_file:
            log_file.write(self.text())
