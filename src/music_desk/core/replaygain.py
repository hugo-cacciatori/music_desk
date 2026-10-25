"""The ReplayGain step: rsgain album scan, clipping fix, album values, LUFS comment, subfolder copies, and
discography leveling (SPEC §10B; port of music_tools' ReplayGain step, same order, values and report text).

Changes from the script (SPEC §17): explicit folders instead of the cwd, every format rsgain supports, tags
through core/tags.py, a Reporter instead of print, cancellation, and a crash is recorded without re-raising.
"""

import logging
import math
import os
import re
import time
import traceback
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from music_desk import paths
from music_desk.core import cue, rsgain
from music_desk.core.cancel import CancelToken
from music_desk.core.formats import AUDIO_EXTENSIONS, REPLAYGAIN_EXTENSIONS
from music_desk.core.tags import TaggedFile

log = logging.getLogger(__name__)

TRACK_GAIN = "replaygain_track_gain"
TRACK_PEAK = "replaygain_track_peak"
ALBUM_GAIN = "replaygain_album_gain"
ALBUM_PEAK = "replaygain_album_peak"


@dataclass(frozen=True)
class ReplayGainOptions:
    """The script's configuration block, with its defaults."""

    target: float = -15.0  # LUFS
    safety_threshold: float = 0.99999
    safety_margin: float = 0.01
    subfolder_keywords: tuple[str, ...] = ("lite", "bonus", "cd2", "disc 2", "cd 2")
    track_data_keywords: tuple[str, ...] = ("lite",)
    track_data_excluded_keywords: tuple[str, ...] = ("bonus",)
    log_file: str = "replaygain.log"
    comment: str = "LUFS Target : {target}"

    @property
    def target_text(self) -> str:
        """The target as the script wrote it: "-15", not "-15.0"."""
        return f"{self.target:g}"

    @property
    def lufs_comment(self) -> str:
        return self.comment.format(target=self.target_text)


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


DEFAULT_OPTIONS = ReplayGainOptions()

Scanner = Callable[..., rsgain.ScanResult]
Clock = Callable[[], datetime]


# ---------------------------------------------------------------------------
# Run log
# ---------------------------------------------------------------------------

def indent(text: str, prefix: str = "    ") -> str:
    return "\n".join((prefix + line).rstrip() for line in text.splitlines())


def plural(count: int, word: str, plural_word: str | None = None) -> str:
    return f"{count} {word if count == 1 else plural_word or word + 's'}"


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


# ---------------------------------------------------------------------------
# File and tag helpers
# ---------------------------------------------------------------------------

def list_audio_files(folder: Path) -> tuple[list[str], list[str]]:
    """(names rsgain can scan, other audio names) of a folder, in os.listdir order as the script."""
    supported, unsupported = [], []
    for name in os.listdir(folder):
        extension = os.path.splitext(name)[1].lower()
        if extension in REPLAYGAIN_EXTENSIONS:
            supported.append(name)
        elif extension in AUDIO_EXTENSIONS:
            unsupported.append(name)
    return supported, unsupported


def image_names(folder: Path, audio_names: Iterable[str] | None = None) -> frozenset[str]:
    """The casefolded names of the folder's unsplit images (SPEC §10 B, Tidy's rule): never measured, never given
    album values. `audio_names`: the folder's audio files, when the caller has listed them already."""
    if audio_names is None:
        supported, unsupported = list_audio_files(folder)
        audio_names = supported + unsupported
    return frozenset(name.casefold() for name in cue.unsplit_images(folder, audio_names))


def find_keyword_subfolders(folder: Path, options: ReplayGainOptions) -> list[str]:
    """The names of a folder's subfolders that match the keywords (lite, bonus, CD2...)."""
    return [
        name for name in os.listdir(folder)
        if os.path.isdir(os.path.join(folder, name))
        and any(keyword in name.lower() for keyword in options.subfolder_keywords)
    ]


def parse_replaygain(value: str) -> float:
    """Convert a ReplayGain tag value such as '-6.5 dB' or '0.98' to a float."""
    return float(value.replace(" dB", ""))


def read_track_values(tagged: TaggedFile, file: str, run_log: RunLog) -> tuple[float, float] | None:
    """(track_gain, track_peak) as floats, or None if they are missing or invalid."""
    gain, peak = tagged.get_replaygain(TRACK_GAIN), tagged.get_replaygain(TRACK_PEAK)
    if gain is None or peak is None:
        run_log.warn(f"Missing ReplayGain track values in {file}, skipping")
        return None
    try:
        return parse_replaygain(gain), parse_replaygain(peak)
    except ValueError:
        run_log.warn(f"Invalid ReplayGain value in {file}: gain={gain!r}, peak={peak!r}, skipping")
        return None


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def scan_album(folder: Path, files: list[str], run_log: RunLog, scanner: Scanner, exe: Path,
               cancel: CancelToken | None) -> bool:
    """Scan all files at once in album mode, writing ReplayGain tags. True if rsgain succeeded."""
    run_log.reporter.info(f"Scanning album with {run_log.options.target_text} LUFS")
    result = scanner(files, folder, run_log.options.target_text, exe, cancel=cancel,
                     on_line=run_log.reporter.info)
    if result.error == "not found":
        run_log.scan = "FAILED (rsgain not found)"
        run_log.error(f"rsgain not found at {exe}")
        return False
    if result.error is not None:
        run_log.scan = f"FAILED ({result.error})"
        run_log.error(f"rsgain was not run: {result.error}. The album can't be scanned in one call.")
        return False
    if result.cancelled:
        run_log.scan = "CANCELLED"
        run_log.warn("Cancelled: rsgain was stopped and the tags were not post-processed")
        return False
    if result.returncode != 0:
        run_log.scan = f"FAILED (exit code {result.returncode})"
        run_log.scan_output = "\n".join(filter(None, (rsgain.strip_ansi(result.stdout),
                                                      rsgain.strip_ansi(result.stderr))))
        run_log.error(f"rsgain scan failed (exit code {result.returncode}):\n{rsgain.strip_ansi(result.stderr)}")
        return False

    run_log.scan = f"OK ({result.duration:.1f} s)"
    run_log.scan_ok = True
    return True


def adjust_gain_to_prevent_clipping(folder: Path, files: list[str], run_log: RunLog) -> None:
    """Lower track gains that would push the track peak above the safety threshold.

    Also records each track's rsgain values in the run log.
    """
    options = run_log.options
    for file in files:
        try:
            tagged = TaggedFile(folder / file)
            values = read_track_values(tagged, file, run_log)
            if values is None:
                run_log.track(file, note="skipped: no ReplayGain data")
                continue
            track_gain, track_peak = values
            run_log.track(file, track_gain, track_peak)

            if track_peak == 0:
                run_log.warn(f"Track peak is 0 in {file}, clipping check skipped")
                run_log.tracks[file]["note"] = "peak is 0"
                continue

            final_track_peak = (10 ** (track_gain / 20)) * track_peak
            if final_track_peak <= options.safety_threshold:
                continue

            max_safe_gain = round(20 * math.log10(options.safety_threshold / track_peak) - options.safety_margin, 2)
            tagged.set_replaygain(TRACK_GAIN, f"{max_safe_gain} dB")
            tagged.save()
            run_log.tracks[file].update(final=max_safe_gain, note="reduced to prevent clipping")
            run_log.reporter.info(f"Clipping detected in {file}: track gain reduced to {max_safe_gain} dB")

        except Exception as e:  # noqa: BLE001 - recorded per file, as the script does
            run_log.track(file, note="error")
            run_log.error(f"Clipping check failed for {file}: {e}")


def update_album_gain_metadata(folder: Path, files: list[str], run_log: RunLog) -> None:
    """Set the album gain to the lowest track gain and the album peak to the highest track peak."""
    track_gains, track_peaks = [], []
    for file in files:
        try:
            values = read_track_values(TaggedFile(folder / file), file, run_log)
            if values is None:
                continue
            track_gains.append(values[0])
            track_peaks.append(values[1])
        except Exception as e:  # noqa: BLE001
            run_log.error(f"Could not read metadata from {file}: {e}")

    if not track_gains:
        run_log.warn("No valid ReplayGain data extracted, album gain not written. Ensure files are correctly tagged.")
        return

    album_gain, album_peak = min(track_gains), max(track_peaks)
    run_log.album = (album_gain, album_peak)
    for file in files:
        try:
            tagged = TaggedFile(folder / file)
            tagged.set_replaygain(ALBUM_GAIN, f"{album_gain} dB")
            tagged.set_replaygain(ALBUM_PEAK, str(album_peak))
            tagged.set_comment(run_log.options.lufs_comment)
            tagged.save()
        except Exception as e:  # noqa: BLE001
            run_log.error(f"Could not write album metadata to {file}: {e}")


def copy_replaygain_metadata(folder: Path, files: list[str], run_log: RunLog,
                             exclude: frozenset[str] = frozenset()) -> None:
    """Copy the album's ReplayGain data to the files of its keyword subfolders.

    In track-data subfolders (e.g. 'lite', but not excluded ones like 'bonus'), the track data of the file with
    the same name in the album folder is copied too.
    """
    options = run_log.options
    subfolders = find_keyword_subfolders(folder, options)
    run_log.subfolders = []
    if not subfolders:
        return

    # Every album file now holds the same album data: the first one serves as reference.
    reference_file = files[0]
    try:
        reference = TaggedFile(folder / reference_file)
        album_data = {tag: reference.get_replaygain(tag) for tag in (ALBUM_GAIN, ALBUM_PEAK)}
        album_data = {tag: value for tag, value in album_data.items() if value is not None}
    except Exception as e:  # noqa: BLE001
        run_log.error(f"Could not read reference file {reference_file}, subfolders not updated: {e}")
        return

    for subfolder in subfolders:
        name = subfolder.lower()
        copy_track_data = (any(keyword in name for keyword in options.track_data_keywords)
                           and not any(keyword in name for keyword in options.track_data_excluded_keywords))
        images = image_names(folder / subfolder)
        folder_files = [f for f in list_audio_files(folder / subfolder)[0]
                        if f.casefold() not in exclude and f.casefold() not in images]
        updated = with_track_data = 0

        for audio_file in folder_files:
            try:
                tagged = TaggedFile(folder / subfolder / audio_file)
                for tag, value in album_data.items():
                    tagged.set_replaygain(tag, value)

                if copy_track_data and audio_file in files:
                    try:
                        album_file = TaggedFile(folder / audio_file)
                        for tag in (TRACK_GAIN, TRACK_PEAK):
                            value = album_file.get_replaygain(tag)
                            if value is not None:
                                tagged.set_replaygain(tag, value)
                        with_track_data += 1
                    except Exception as e:  # noqa: BLE001
                        run_log.error(f"Could not read track data from {audio_file} for '{subfolder}': {e}")

                tagged.save()
                updated += 1
            except Exception as e:  # noqa: BLE001
                run_log.error(f"Could not update {audio_file} in '{subfolder}': {e}")

        summary = f"{subfolder}: {updated}/{len(folder_files)} files got the album data"
        if copy_track_data:
            summary += f", {with_track_data} also got their track data"
        run_log.subfolders.append(summary)


def process_album(folder: str | Path, options: ReplayGainOptions = DEFAULT_OPTIONS,
                  scanner: Scanner = rsgain.scan, exe: Path | None = None, cancel: CancelToken | None = None,
                  reporter: Reporter | None = None, now: Clock = datetime.now,
                  exclude: Iterable[str] = ()) -> RunLog | None:
    """Run the album workflow in `folder` and append its report to the log file. `exclude`: file names that
    aren't tracks (Tidy's unsplit images, SPEC §28), neither measured nor given the album values.

    Returns the RunLog, or None if the folder has no file rsgain can scan. A crash is recorded in the log
    (result CRASHED) and not re-raised.
    """
    folder = Path(folder)
    exe = exe or paths.rsgain_path()
    reporter = reporter or NullReporter()
    skip = frozenset(name.casefold() for name in exclude)
    files, unsupported = list_audio_files(folder)
    images = image_names(folder, files + unsupported)
    waiting = [name for name in files if name.casefold() in images and name.casefold() not in skip]
    files = [name for name in files if name.casefold() not in skip and name.casefold() not in images]
    if not files:
        reporter.info(f"Only an image waiting to be split in {folder}" if waiting
                      else f"No audio file rsgain can scan in {folder}")
        return None

    run_log = RunLog(folder, files, options, exe, reporter, now)
    try:
        for name in unsupported:
            if name.casefold() not in skip:  # an excluded file isn't a track of this run at all
                run_log.warn(f"{name}: skipped, format not supported by rsgain")
        for name in waiting:
            run_log.warn(f"{name}: skipped, image waiting to be split")
        if not scan_album(folder, files, run_log, scanner, exe, cancel):
            return run_log
        adjust_gain_to_prevent_clipping(folder, files, run_log)
        update_album_gain_metadata(folder, files, run_log)
        copy_replaygain_metadata(folder, files, run_log, skip)
    except Exception:  # noqa: BLE001 - recorded as CRASHED in the album's log
        log.exception("ReplayGain crashed in %s", folder)
        run_log.crash()
    finally:
        run_log.write()
        reporter.info(f"Report written to {options.log_file}: {run_log.result()}")
    return run_log


# ---------------------------------------------------------------------------
# Loose tracks (the right-click menu only, SPEC §31)
# ---------------------------------------------------------------------------

@dataclass
class LooseResult:
    """A run on loose tracks: each one's RunLog, in order, and the paths left out with why."""

    options: ReplayGainOptions
    logs: list[RunLog] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)
    not_run: list[Path] = field(default_factory=list)  # after a cancel

    def result(self) -> str:
        results = {run_log.result() for run_log in self.logs}
        if self.not_run:
            return "CANCELLED"
        if not self.logs:
            return "NOTHING TO DO"
        return "OK" if results == {"OK"} else "DONE WITH ERRORS"

    def text(self) -> str:
        """The report the job's window shows: one block per track, then what was left out. Never written to a
        file: an album's replaygain.log keeps its last report."""
        lines = [f"ReplayGain, each track on its own (target {self.options.target_text} LUFS): "
                 f"{plural(len(self.logs), 'track')} processed", ""]
        text = "\n".join(lines) + "\n" + "".join(run_log.text() for run_log in self.logs)
        tail = [f"  {path}: {reason}" for path, reason in self.skipped]
        tail += [f"  {path}: not processed, cancelled" for path in self.not_run]
        if tail:
            text += "\nLeft out:\n" + "\n".join(tail) + "\n"
        return text + f"\nResult: {self.result()}\n"


def process_loose_tracks(tracks: Iterable[str | Path], options: ReplayGainOptions = DEFAULT_OPTIONS,
                         scanner: Scanner = rsgain.scan, exe: Path | None = None, cancel: CancelToken | None = None,
                         reporter: Reporter | None = None, now: Clock = datetime.now) -> LooseResult:
    """ReplayGain on tracks that belong to no album (SPEC §31): each one alone, in turn, is its own album. rsgain
    measures it at the target, the clipping fix lowers its track gain if needed, its album gain and peak become its
    own and the LUFS comment is written. Nothing is copied into subfolders and no log file is written.

    Files rsgain can't scan (by extension) and missing ones are skipped. A cancel stops after the current track
    (rsgain on one file is short), as the discography stops between albums.
    """
    exe = exe or paths.rsgain_path()
    reporter = reporter or NullReporter()
    result = LooseResult(options)
    todo: list[Path] = []
    for track in map(Path, tracks):
        if track.suffix.lower() not in REPLAYGAIN_EXTENSIONS:
            result.skipped.append((track, "skipped, format not supported by rsgain"))
        elif not track.is_file():
            result.skipped.append((track, "skipped, not found"))
        else:
            todo.append(track)
    for index, track in enumerate(todo):
        if cancel is not None and cancel.cancelled:
            result.not_run = todo[index:]
            break
        reporter.progress(index, len(todo), track.name)
        result.logs.append(_process_loose_track(track, options, scanner, exe, reporter, now))
    reporter.progress(len(todo), len(todo), "done")
    return result


def _process_loose_track(track: Path, options: ReplayGainOptions, scanner: Scanner, exe: Path,
                         reporter: Reporter, now: Clock) -> RunLog:
    folder, files = track.parent, [track.name]
    run_log = RunLog(folder, files, options, exe, reporter, now, loose=True)
    try:
        if scan_album(folder, files, run_log, scanner, exe, None):
            adjust_gain_to_prevent_clipping(folder, files, run_log)
            update_album_gain_metadata(folder, files, run_log)
    except Exception:  # noqa: BLE001 - recorded as CRASHED in the track's report
        log.exception("ReplayGain crashed on %s", track)
        run_log.crash()
    reporter.info(f"{track.name}: {run_log.result()}")
    return run_log


# ---------------------------------------------------------------------------
# Discography mode
# ---------------------------------------------------------------------------

def find_album_folders(root: Path, options: ReplayGainOptions) -> list[Path]:
    """The folders under root (root included) that directly hold audio files rsgain can scan.

    The lite/bonus/CD2 subfolders of an album are not albums of their own: the album's copy step handles them.
    A folder whose only such files are unsplit images isn't an album (SPEC §10 B).
    """
    albums = []
    for dirpath, dirnames, _ in os.walk(root):
        dirnames.sort()
        supported, unsupported = list_audio_files(Path(dirpath))
        images = image_names(Path(dirpath), supported + unsupported) if supported else frozenset()
        if any(name.casefold() not in images for name in supported):
            albums.append(Path(dirpath))
            keyword_subfolders = find_keyword_subfolders(Path(dirpath), options)
            dirnames[:] = [name for name in dirnames if name not in keyword_subfolders]
    return albums


def level_album(folder: Path, target_gain: float, options: ReplayGainOptions) -> tuple[int, int, list[str]]:
    """Set the album gain of an album folder and its keyword subfolders to target_gain.

    Only files whose album gain differs are written; files without an album gain are skipped.
    Returns (changed, skipped, errors).
    """
    file_paths = []
    for each in (folder, *(folder / sub for sub in find_keyword_subfolders(folder, options))):
        images = image_names(each)
        file_paths += [each / f for f in list_audio_files(each)[0] if f.casefold() not in images]

    changed, skipped, errors = 0, 0, []
    for path in file_paths:
        try:
            tagged = TaggedFile(path)
            current = tagged.get_replaygain(ALBUM_GAIN)
            if current is None:
                skipped += 1
                continue
            if parse_replaygain(current) == target_gain:
                continue
            tagged.set_replaygain(ALBUM_GAIN, f"{target_gain} dB")
            tagged.save()
            changed += 1
        except Exception as e:  # noqa: BLE001
            errors.append(f"Could not level {os.path.relpath(path, folder)}: {e}")
    return changed, skipped, errors


@dataclass
class AlbumOutcome:
    name: str
    folder: Path
    result: str = "CRASHED"
    gain: float | None = None
    changed: int | None = None  # None: not leveled
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class DiscographyResult:
    albums: list[AlbumOutcome]
    target: float | None
    cancelled: bool = False


def process_discography(root: str | Path, options: ReplayGainOptions = DEFAULT_OPTIONS,
                        scanner: Scanner = rsgain.scan, exe: Path | None = None, cancel: CancelToken | None = None,
                        reporter: Reporter | None = None, now: Clock = datetime.now,
                        on_album: Callable[[RunLog], None] | None = None) -> DiscographyResult | None:
    """Process every album under root, one at a time, then give them all the lowest album gain.

    After a cancel, the remaining albums are not processed and nothing is leveled. `on_album` gets each album's
    RunLog as it is written (the app's report shows them).
    """
    root = Path(root)
    reporter = reporter or NullReporter()
    start, start_clock = now(), time.monotonic()
    album_folders = find_album_folders(root, options)
    if not album_folders:
        reporter.info(f"No folder with audio files rsgain can scan under {root}")
        return None

    # 1. Album workflow in every album folder
    albums: list[AlbumOutcome] = []
    cancelled = False
    for index, folder in enumerate(album_folders):
        name = os.path.relpath(folder, root) if folder != root else f"{root.name} (this folder)"
        album = AlbumOutcome(name, folder)
        albums.append(album)
        if cancelled or (cancel is not None and cancel.cancelled):
            cancelled = True
            album.result = "CANCELLED"
            continue
        reporter.progress(index, len(album_folders), name)
        run_log = process_album(folder, options, scanner, exe, cancel, reporter, now)
        if run_log is not None and on_album is not None:
            on_album(run_log)
        if run_log is not None:
            album.result = run_log.result()
            if run_log.album and album.result in ("OK", "DONE WITH ERRORS"):
                album.gain = run_log.album[0]
            if run_log.scan == "CANCELLED":
                cancelled = True

    # 2. Level the albums that were processed successfully (not after a cancel)
    leveled = [album for album in albums if album.gain is not None] if not cancelled else []
    target = min((album.gain for album in leveled if album.gain is not None), default=None)
    for album in leveled:
        assert target is not None  # the lowest of the leveled albums' gains
        album.changed, album.skipped, album.errors = level_album(album.folder, target, options)
        if album.gain == target:
            line = f"album gain already at the discography target ({target} dB)"
        else:
            line = f"album gain {album.gain} dB -> {target} dB"
        line += f", {plural(album.changed, 'file')} changed"
        with open(album.folder / options.log_file, "a", encoding="utf-8") as log_file:
            log_file.write(f"Discography leveling {now():%Y-%m-%d %H:%M:%S}: {line}\n")
            log_file.writelines(f"  ERROR  {error}\n" for error in album.errors)

    # 3. Summary in the artist folder
    write_discography_summary(root, options, start, time.monotonic() - start_clock, albums, target, cancelled)
    reporter.progress(len(album_folders), len(album_folders), "done")
    return DiscographyResult(albums, target, cancelled)


def discography_summary_text(root: Path, options: ReplayGainOptions, start: datetime, duration: float,
                             albums: list[AlbumOutcome], target: float | None, cancelled: bool = False) -> str:
    header = ("Album", "Result", "Album gain", "Leveled to", "Files changed")
    rows = []
    for album in albums:
        is_leveled = album.changed is not None
        result = album.result
        if result == "OK" and album.gain is None:
            result = "OK (no album gain)"
        rows.append((album.name, result, format_gain(album.gain),
                     format_gain(target) if is_leveled else "not leveled",
                     str(album.changed) if is_leveled else "-"))
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]
    table = ["  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
             for row in [header, *rows]]

    n_leveled = sum(1 for album in albums if album.changed is not None)
    issues = [f"  {album.name}: {album.result}, not leveled. See {os.path.join(album.name, options.log_file)}"
              for album in albums if album.changed is None]
    issues += [f"  {album.name}: {error}" for album in albums for error in album.errors]
    issues += [f"  {album.name}: {album.skipped} file(s) without album gain, not leveled"
               for album in albums if album.skipped]
    if cancelled:
        gain_line = "Discography album gain: none, the run was cancelled before leveling"
    elif target is not None:
        gain_line = f"Discography album gain: {target} dB (lowest album gain)"
    else:
        gain_line = "Discography album gain: none, no album was processed successfully"
    lines = [
        "=" * 78,
        f"Discography run {start:%Y-%m-%d %H:%M:%S}   target {options.target_text} LUFS",
        f"Folder: {root}",
        f"Albums: {len(albums)} ({n_leveled} leveled, {len(albums) - n_leveled} not leveled)",
        "",
        *table,
        "",
        gain_line,
        "",
        "Errors / notes:",
        *(issues or ["  none"]),
        "",
        f"Result: {'OK' if n_leveled == len(albums) and not issues else 'CHECK ALBUMS'}. "
        f"{plural(len(albums), 'album')}, {n_leveled} leveled, {duration:.1f} s",
        "",
    ]
    return "\n".join(lines) + "\n"


def write_discography_summary(root: Path, options: ReplayGainOptions, start: datetime, duration: float,
                              albums: list[AlbumOutcome], target: float | None, cancelled: bool = False) -> None:
    """The summary replaces the artist folder's log, unless that folder was also one of this run's albums: then
    it follows that album's report, written minutes earlier in the same run."""
    mode = "a" if any(Path(album.folder) == Path(root) for album in albums) else "w"
    with open(root / options.log_file, mode, encoding="utf-8") as log_file:
        log_file.write(discography_summary_text(root, options, start, duration, albums, target, cancelled))


# ---------------------------------------------------------------------------
# Options: checks and saved values (the ReplayGain options card)
# ---------------------------------------------------------------------------

TARGET_RANGE = (-30.0, -5.0)  # rsgain's -l accepts -30 <= n <= -5


def validate(options: ReplayGainOptions) -> list[str]:
    """The problems in the options (empty if they are fine). The script had no checks for its ReplayGain block;
    these keep a run from failing halfway (rsgain refusing the target, a log name Windows refuses...)."""
    from music_desk.core.masks import FORBIDDEN_CHARACTERS  # the file-name rule, shared with the Rename tool

    problems = []
    low, high = TARGET_RANGE
    if not (isinstance(options.target, int | float) and low <= options.target <= high):
        problems.append(f"The target loudness must be between {low:g} and {high:g} LUFS (rsgain's range)")
    if not 0 < options.safety_threshold <= 1:
        problems.append("The safety threshold must be above 0 and at most 1 (a peak of 1.0 is full scale)")
    if not 0 <= options.safety_margin <= 6:
        problems.append("The safety margin must be between 0 and 6 dB")
    for label, keywords in (("Album-data subfolder keywords", options.subfolder_keywords),
                            ("Track-data keywords", options.track_data_keywords),
                            ("Excluded keywords", options.track_data_excluded_keywords)):
        for keyword in keywords:
            if not keyword.strip():
                problems.append(f"{label}: a keyword is empty")
            elif keyword != keyword.lower():
                problems.append(f"{label}: {keyword!r} must be lowercase (folder names are compared in lowercase)")
    name = options.log_file
    if not name.strip() or name in (".", "..") or FORBIDDEN_CHARACTERS.search(name) or name.endswith((" ", ".")):
        problems.append("The log file name isn't a valid file name")
    try:
        options.comment.format(target=options.target_text)
    except (ValueError, KeyError, IndexError) as e:
        problems.append(f"The comment text is invalid ({e}); it may only use {{target}}")
    return problems


def options_to_json(options: ReplayGainOptions) -> dict:
    return {
        "target": options.target,
        "safety_threshold": options.safety_threshold,
        "safety_margin": options.safety_margin,
        "subfolder_keywords": list(options.subfolder_keywords),
        "track_data_keywords": list(options.track_data_keywords),
        "track_data_excluded_keywords": list(options.track_data_excluded_keywords),
        "log_file": options.log_file,
        "comment": options.comment,
    }


def options_from_json(data) -> ReplayGainOptions:
    """Saved options; a missing or mistyped value falls back to its default (see rename.options_from_json)."""
    data = data if isinstance(data, dict) else {}
    default = DEFAULT_OPTIONS

    def number(key):
        value = data.get(key)
        return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else getattr(default, key)

    def strings(key):
        value = data.get(key)
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            return tuple(value)
        return getattr(default, key)

    def text(key):
        value = data.get(key)
        return value if isinstance(value, str) else getattr(default, key)

    return ReplayGainOptions(
        target=number("target"), safety_threshold=number("safety_threshold"), safety_margin=number("safety_margin"),
        subfolder_keywords=strings("subfolder_keywords"), track_data_keywords=strings("track_data_keywords"),
        track_data_excluded_keywords=strings("track_data_excluded_keywords"), log_file=text("log_file"),
        comment=text("comment"))


# ---------------------------------------------------------------------------
# Removing ReplayGain (SPEC §10 B, Hugo 2026-10-16)
# ---------------------------------------------------------------------------

_NUMBER = r"[-+]?\d+(?:[.,]\d+)?"


def lufs_comment_pattern(template: str) -> re.Pattern:
    """A comment line the ReplayGain step writes, for any target: the template ("LUFS Target : {target}") with
    {target} as a number. A template without {target} matches only itself."""
    body = _NUMBER.join(re.escape(part) for part in template.strip().split("{target}"))
    return re.compile(r"\s*" + body + r"\s*")


def remove_replaygain(path: str | Path, options: ReplayGainOptions = DEFAULT_OPTIONS) -> bool:
    """Remove a file's ReplayGain tags: every REPLAYGAIN_* value, and the comment lines the ReplayGain step wrote
    (the card's comment template, any target). Other comment lines stay. Saves only when something changed;
    returns whether it did. Raises the file's errors (unreadable, no tags, locked...) to the caller."""
    tagged = TaggedFile(path)
    if not tagged.info.tags:
        return False  # raw AAC: nothing to remove, nothing can be written
    changed = tagged.remove_replaygain() > 0
    comment = tagged.fields().comment
    if comment:
        pattern = lufs_comment_pattern(options.comment)
        lines = comment.splitlines()
        kept = [line for line in lines if not pattern.fullmatch(line)]
        if len(kept) != len(lines):
            tagged.set_fields({"comment": "\n".join(kept).strip() or None})
            changed = True
    if changed:
        tagged.save()
    return changed
