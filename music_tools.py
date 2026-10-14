"""Tidy an album folder: rename the music files from their tags, then apply ReplayGain.

Usage (from the album folder, or the artist folder with --discography):
    python music_tools.py                  rename, then ReplayGain
    python music_tools.py --rename-only    only the rename step
    python music_tools.py --gain-only      only the ReplayGain step
    python music_tools.py --discography    ReplayGain on every album under the cwd, then
                                           level them all to the same album gain
    python music_tools.py --dry-run        preview the rename step (the ReplayGain step is skipped)
Options combine, e.g. --gain-only --discography. Everything else is set in the
configuration block below.

1. Rename step (cwd and its subfolders if RECURSIVE)
   - RENAME_MASKS decide the new names. By default a file becomes
     "<track number> <title>.<ext>", or "<title>.<ext>" without a track number,
     and a file without a title keeps its name. Characters that are not allowed
     in file names are replaced in the tags, and a file is never renamed over
     another one.
   - Files with a JUNK_EXTENSIONS extension are deleted.
   - DRY_RUN previews the changes without making them.

2. ReplayGain step (runs after the rename step, so files carry their final names)
   - Scan every audio file of the cwd as one album with rsgain (ALBUM_LOUDNESS target).
   - Lower the track gain of any track that would clip.
   - Set album gain to the lowest track gain and album peak to the highest track peak.
   - Copy the album ReplayGain data into the SUBFOLDER_KEYWORDS subfolders ("lite",
     "bonus", "CD2"...), plus the track data into TRACK_DATA_SUBFOLDER_KEYWORDS ones
     ("lite") for files that also exist in the cwd.
   Discography mode runs this in every folder holding audio files (their lite/bonus/
   CD2 subfolders are handled by their album), then sets the album gain of all of
   them to the lowest album gain, so the whole discography plays at the same level.
   Albums that fail are left out.

   Each album run appends a report (track table, album values, subfolders, warnings
   and errors) to replaygain.log in the album folder. Discography mode also appends
   a summary to replaygain.log in the artist folder.
"""
import argparse
import contextlib
import math
import os
import re
import string
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from mutagen.flac import FLAC
from mutagen.id3 import COMM, ID3, TXXX
from mutagen.mp3 import MP3
from tinytag import TinyTag

# ============================= Configuration =============================

# ---- General ----
AUDIO_EXTENSIONS = {".flac", ".mp3"}          # music files handled by both steps
PAUSE_AT_END = True                           # wait for Enter before closing

# ---- Rename step ----
# Rename masks, tried in order: the first one whose tags are all present is used.
# A file that no mask fits keeps its name. The extension is added automatically.
# Fields: {track} {track_total} {disc} {disc_total} {title} {artist}
#         {albumartist} {album} {year} {genre} {composer}
# Numbers accept a format, e.g. {track:02} = track number on 2 digits.
RENAME_MASKS = [
    "{track:02} {title}",
    "{title}",
]
JUNK_EXTENSIONS = {".cue", ".m3u", ".nfo", ".sfv", ".txt", ".lrc"}  # deleted; set() to keep everything
INVALID_CHAR_REPLACEMENT = "-"                # replaces / \ ? % * : | " < > and control characters in tags
RECURSIVE = True                              # also process subfolders
DRY_RUN = False                               # True: only print what would be renamed/deleted

# ---- ReplayGain step ----
# rsgain executable, and the arguments of the album scan (the loudness target and
# the files are added after them). "-s i" = scan and write the ReplayGain tags.
RSGAIN_PATH = str(Path(__file__).resolve().parent / "rsgain-3.6-win64" / "rsgain.exe")
RSGAIN_SCAN_ARGS = ["custom", "-s", "i"]
ALBUM_LOUDNESS = "-15"                  # LUFS target
# Clipping prevention: a track whose peak after gain would exceed SAFETY_THRESHOLD
# gets its gain lowered so the peak lands SAFETY_MARGIN dB below that threshold.
SAFETY_THRESHOLD = 0.99999
SAFETY_MARGIN = 0.01
LUFS_COMMENT = f"LUFS Target : {ALBUM_LOUDNESS}"   # written in every file's comment tag
# Subfolders of an album that receive its album ReplayGain data (matched in the
# folder name, ignoring case)...
SUBFOLDER_KEYWORDS = ["lite", "bonus", "cd2", "disc 2", "cd 2"]
# ...and among them, the ones that also receive the track data of same-named files
TRACK_DATA_SUBFOLDER_KEYWORDS = ["lite"]
TRACK_DATA_EXCLUDED_KEYWORDS = ["bonus"]
LOG_FILE = "replaygain.log"             # report written in each album folder

# =========================================================================


# ###########################################################################
# Rename step
# ###########################################################################

NUMBER_FIELDS = ("track", "track_total", "disc", "disc_total")
TEXT_FIELDS = ("title", "artist", "albumartist", "album", "year", "genre", "composer")
FORBIDDEN_CHARACTERS = re.compile(r"[/\\?%*:|\"<>\x7F\x00-\x1F]")


@dataclass
class RenameReport:
    """What happened during the run, for the summary."""
    scanned: int = 0
    renamed: list = field(default_factory=list)
    fallback: list = field(default_factory=list)
    missing_tags: list = field(default_factory=list)
    name_taken: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    junk_deleted: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Configuration checks
# ---------------------------------------------------------------------------

def mask_fields(mask):
    """List the field names used by a mask."""
    return [name for _, name, _, _ in string.Formatter().parse(mask) if name is not None]


def validate_config():
    """Return the list of problems in the configuration block (empty if it is fine)."""
    problems = []
    if not RENAME_MASKS:
        problems.append("RENAME_MASKS is empty")

    sample = {name: 1 for name in NUMBER_FIELDS} | {name: "x" for name in TEXT_FIELDS}
    for mask in RENAME_MASKS:
        try:
            literal_text = "".join(text for text, _, _, _ in string.Formatter().parse(mask))
            unknown = [name for name in mask_fields(mask) if name not in sample]
        except ValueError as e:
            problems.append(f"mask {mask!r}: {e}")
            continue
        if unknown:
            problems.append(f"mask {mask!r}: unknown field(s) {', '.join(unknown)}")
            continue
        if not mask_fields(mask):
            problems.append(f"mask {mask!r}: uses no field")
        if FORBIDDEN_CHARACTERS.search(literal_text):
            problems.append(f"mask {mask!r}: contains a character not allowed in file names")
        try:
            mask.format(**sample)
        except (ValueError, TypeError, IndexError, KeyError) as e:
            problems.append(f"mask {mask!r}: invalid format ({e})")

    for name, extensions in (("AUDIO_EXTENSIONS", AUDIO_EXTENSIONS), ("JUNK_EXTENSIONS", JUNK_EXTENSIONS)):
        for extension in extensions:
            if not extension.startswith(".") or extension != extension.lower():
                problems.append(f"{name}: {extension!r} must be lowercase and start with a dot")
    overlap = {e.lower() for e in AUDIO_EXTENSIONS} & {e.lower() for e in JUNK_EXTENSIONS}
    if overlap:
        problems.append(f"{', '.join(sorted(overlap))} is in both AUDIO_EXTENSIONS and JUNK_EXTENSIONS")

    if FORBIDDEN_CHARACTERS.search(INVALID_CHAR_REPLACEMENT):
        problems.append("INVALID_CHAR_REPLACEMENT contains a character not allowed in file names")
    return problems


# ---------------------------------------------------------------------------
# Renaming
# ---------------------------------------------------------------------------

def sanitize(text):
    """Replace the characters that are not allowed in file names."""
    return FORBIDDEN_CHARACTERS.sub(INVALID_CHAR_REPLACEMENT, text)


def read_fields(tag):
    """Return the tags usable in masks; missing or blank tags are left out."""
    fields = {name: getattr(tag, name) for name in NUMBER_FIELDS if getattr(tag, name) is not None}
    for name in TEXT_FIELDS:
        value = getattr(tag, name)
        if value is not None and str(value).strip():
            fields[name] = sanitize(str(value))
    return fields


def choose_name(fields):
    """Return (new_stem, mask_index) for the first mask whose fields are all present, or None."""
    for index, mask in enumerate(RENAME_MASKS):
        if all(name in fields for name in mask_fields(mask)):
            return mask.format(**fields), index
    return None


def missing_from(mask, fields):
    return ", ".join(name for name in mask_fields(mask) if name not in fields)


def process_audio_file(path, report):
    """Rename an audio file following the first mask its tags fit."""
    report.scanned += 1
    try:
        fields = read_fields(TinyTag.get(path))
    except Exception as e:
        report.errors.append(f"{path.name}: could not read the tags ({e})")
        return

    choice = choose_name(fields)
    if choice is None:
        report.missing_tags.append(f"{path.name} (missing: {missing_from(RENAME_MASKS[0], fields)})")
        return

    new_stem, mask_index = choice
    new_name = f"{new_stem}{path.suffix.lower()}"
    if path.name == new_name:
        return

    target = path.with_name(new_name)
    # On Windows a case-only rename points at the same file: that is not a clash
    if target.exists() and not os.path.samefile(path, target):
        report.name_taken.append(f"{path.name} -> {new_name}")
        return

    if not DRY_RUN:
        try:
            path.rename(target)
        except OSError as e:
            report.errors.append(f"{path.name}: could not be renamed to {new_name} ({e})")
            return

    report.renamed.append(new_name)
    if mask_index > 0:
        report.fallback.append(f"{new_name} (missing: {missing_from(RENAME_MASKS[0], fields)})")


def delete_junk_file(path, report):
    if not DRY_RUN:
        try:
            os.remove(path)
        except OSError as e:
            report.errors.append(f"{path.name}: could not be deleted ({e})")
            return
    report.junk_deleted.append(path.name)


def process_folder(directory, report):
    """Rename the audio files and delete the junk files of a folder (and its subfolders if RECURSIVE)."""
    for path in list(Path(directory).iterdir()):
        if path.is_dir():
            if RECURSIVE:
                process_folder(path, report)
        elif path.is_file():
            extension = path.suffix.lower()
            if extension in AUDIO_EXTENSIONS:
                process_audio_file(path, report)
            elif extension in JUNK_EXTENSIONS:
                delete_junk_file(path, report)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def count_files(count, verb_singular="", verb_plural=""):
    """'1 file was' / '2 files were' style wording."""
    return f"{count} file{'' if count == 1 else 's'}" + (
        f" {verb_singular if count == 1 else verb_plural}" if verb_singular else "")


def print_section(title, files):
    if files:
        print(f"\n{title}\n")
        print(files)


def print_summary(report):
    if DRY_RUN:
        print("DRY RUN: nothing was changed\n")
    was, were = ("would be", "would be") if DRY_RUN else ("was", "were")

    print(f"{len(report.renamed)}/{count_files(report.scanned, was, were)} renamed\n")
    if report.renamed:
        print(report.renamed)

    print_section(f"{count_files(len(report.fallback), was, were)} renamed with a fallback mask",
                  report.fallback)
    print_section(f"{count_files(len(report.missing_tags))} could not be renamed: missing tags",
                  report.missing_tags)
    print_section(f"{count_files(len(report.name_taken))} could not be renamed "
                  f"because another file already has that name", report.name_taken)
    print_section(f"{count_files(len(report.errors))} could not be processed", report.errors)
    print_section(f"{count_files(len(report.junk_deleted), was, were)} deleted as junk",
                  report.junk_deleted)


def rename_step():
    """Run the rename step in the cwd and print its summary."""
    report = RenameReport()
    process_folder(os.getcwd(), report)
    print_summary(report)
    return report


# ###########################################################################
# ReplayGain step
# ###########################################################################

TRACK_GAIN = "replaygain_track_gain"
TRACK_PEAK = "replaygain_track_peak"
ALBUM_GAIN = "replaygain_album_gain"
ALBUM_PEAK = "replaygain_album_peak"


# ---------------------------------------------------------------------------
# Run log
# ---------------------------------------------------------------------------

def strip_ansi(text):
    """Remove the terminal colour codes from rsgain's output."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text or "").strip()


def indent(text, prefix="    "):
    return "\n".join((prefix + line).rstrip() for line in text.splitlines())


def plural(count, word, plural_word=None):
    return f"{count} {word if count == 1 else plural_word or word + 's'}"


def format_gain(gain):
    return "-" if gain is None else f"{gain:+.2f} dB"


def format_peak_db(peak):
    if peak is None:
        return "-"
    return f"{20 * math.log10(peak):.2f} dB" if peak > 0 else "-inf dB"


class RunLog:
    """Collects what happens during a run and appends it to LOG_FILE as one readable block."""

    def __init__(self, files):
        self.start = datetime.now()
        self.start_clock = time.monotonic()
        self.folder = os.getcwd()
        self.files = files
        self.scan = "not run"
        self.scan_output = ""
        self.scan_ok = False
        self.tracks = {}       # file -> {"gain", "peak", "final", "note"}
        self.album = None      # (album_gain, album_peak)
        self.subfolders = None # lines describing each subfolder, None until that step runs
        self.issues = []       # (time, level, message)
        self.crashed = False

    def _add(self, level, message):
        self.issues.append((datetime.now(), level, message))

    def warn(self, message):
        print(f"⚠️ {message}")
        self._add("WARNING", message)

    def error(self, message):
        print(f"❌ {message}")
        self._add("ERROR", message)

    def crash(self):
        self.crashed = True
        self._add("ERROR", "Unexpected crash:\n" + traceback.format_exc().strip())

    def track(self, file, gain=None, peak=None, note=""):
        self.tracks[file] = {"gain": gain, "peak": peak, "final": gain, "note": note}

    def count(self, level):
        return sum(1 for _, lvl, _ in self.issues if lvl == level)

    def result(self):
        if self.crashed:
            return "CRASHED"
        if not self.scan_ok:
            return "STOPPED"
        return "OK" if not self.count("ERROR") else "DONE WITH ERRORS"

    def _track_table(self):
        header = ("Track", "Loudness", "Peak", "rsgain gain", "Final gain", "Note")
        rows = []
        for file in self.files:
            t = self.tracks.get(file, {"gain": None, "peak": None, "final": None, "note": "not processed"})
            loudness = "-" if t["gain"] is None else f"{float(ALBUM_LOUDNESS) - t['gain']:.2f} LUFS"
            rows.append((file, loudness, format_peak_db(t["peak"]),
                         format_gain(t["gain"]), format_gain(t["final"]), t["note"]))
        widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]
        return [
            "  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip()
            for row in [header, *rows]
        ]

    def write(self):
        n_flac = sum(1 for f in self.files if f.lower().endswith(".flac"))
        clipping_fixes = sum(1 for t in self.tracks.values() if t["final"] != t["gain"])
        duration = time.monotonic() - self.start_clock

        lines = [
            "=" * 78,
            f"Run {self.start:%Y-%m-%d %H:%M:%S}   target {ALBUM_LOUDNESS} LUFS   rsgain: {RSGAIN_PATH}",
            f"Folder: {self.folder}",
            f"Files: {len(self.files)} ({n_flac} FLAC, {len(self.files) - n_flac} MP3)",
            f"Scan: {self.scan}",
        ]
        if self.scan_output:
            lines.append(indent(self.scan_output))

        if self.scan_ok:
            lines += ["", *self._track_table(), ""]
            if self.album:
                lines.append(f"Album: gain {self.album[0]} dB (lowest track gain), "
                             f"peak {self.album[1]} (highest track peak)")
            else:
                lines.append("Album: not computed")
            lines.append("Subfolders:")
            if self.subfolders is None:
                lines.append("  not processed")
            else:
                lines += [f"  {line}" for line in self.subfolders] or ["  none matching " + ", ".join(SUBFOLDER_KEYWORDS)]

        lines += ["", "Warnings / errors:"]
        if not self.issues:
            lines.append("  none")
        for moment, level, message in self.issues:
            first, *rest = message.splitlines()
            lines.append(f"  {moment:%H:%M:%S} {level:<7}  {first}")
            lines += [f"                    {line}" for line in rest]

        lines += ["", f"Result: {self.result()}. {plural(len(self.files), 'track')}, "
                      f"{plural(clipping_fixes, 'clipping fix', 'clipping fixes')}, {plural(self.count('WARNING'), 'warning')}, "
                      f"{plural(self.count('ERROR'), 'error')}, {duration:.1f} s", ""]

        with open(LOG_FILE, "a", encoding="utf-8") as log:
            log.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# File and tag helpers
#
# FLAC stores ReplayGain as Vorbis comments, MP3 as ID3 TXXX frames. rsgain
# writes the TXXX descriptions in uppercase, so they are matched ignoring case.
# ---------------------------------------------------------------------------

def list_audio_files(folder="."):
    """List the audio file names (AUDIO_EXTENSIONS, any case) of a folder."""
    return [f for f in os.listdir(folder) if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS]


def find_keyword_subfolders(folder="."):
    """List the names of a folder's subfolders that match SUBFOLDER_KEYWORDS (lite, bonus, CD2...)."""
    return [
        name for name in os.listdir(folder)
        if os.path.isdir(os.path.join(folder, name))
        and any(keyword in name.lower() for keyword in SUBFOLDER_KEYWORDS)
    ]


def load_audio(path):
    """Open a file for tag editing: FLAC, or MP3 with ID3 tags."""
    return FLAC(path) if path.lower().endswith(".flac") else MP3(path, ID3=ID3)


def find_txxx(audio, name):
    """Return the TXXX frames of an MP3 whose description is name, ignoring case."""
    if audio.tags is None:
        return []
    return [frame for frame in audio.tags.getall("TXXX") if frame.desc.lower() == name.lower()]


def remove_txxx(audio, name):
    """Remove every TXXX frame whose description is name, ignoring case."""
    for frame in find_txxx(audio, name):
        audio.tags.delall(frame.HashKey)


def get_tag(audio, name):
    """Return the first value of a tag as a string, None if it is missing."""
    if isinstance(audio, FLAC):
        values = audio.get(name)
        return values[0] if values else None
    frames = find_txxx(audio, name)
    return frames[0].text[0] if frames else None


def set_tag(audio, name, value):
    """Write a tag, replacing any existing one with the same name (any case)."""
    if isinstance(audio, FLAC):
        audio[name] = value
        return
    if audio.tags is None:
        audio.add_tags()
    remove_txxx(audio, name)
    audio.tags.add(TXXX(encoding=3, desc=name.upper(), text=[value]))


def set_comment(audio, text):
    """Write the comment tag (a COMM frame for MP3)."""
    if isinstance(audio, FLAC):
        audio["comment"] = text
        return
    if audio.tags is None:
        audio.add_tags()
    remove_txxx(audio, "comment")  # written by older versions of this script
    audio.tags.add(COMM(encoding=3, lang="eng", desc="", text=[text]))


def parse_replaygain(value):
    """Convert a ReplayGain tag value such as '-6.5 dB' or '0.98' to a float."""
    return float(value.replace(" dB", ""))


def read_track_values(audio, file, run_log):
    """Return (track_gain, track_peak) as floats, or None if they are missing or invalid."""
    gain, peak = get_tag(audio, TRACK_GAIN), get_tag(audio, TRACK_PEAK)
    if gain is None or peak is None:
        run_log.warn(f"Missing ReplayGain track values in {file}, skipping")
        return None
    try:
        return parse_replaygain(gain), parse_replaygain(peak)
    except ValueError:
        run_log.warn(f"Invalid ReplayGain value in {file}: gain={gain!r}, peak={peak!r}, skipping")
        return None


# ---------------------------------------------------------------------------
# rsgain
# ---------------------------------------------------------------------------

def print_output(result):
    if result.stdout:
        print("Output:", result.stdout)


def scan_album(files, run_log):
    """Scan all files at once in album mode, writing ReplayGain tags.

    Returns True if rsgain succeeded.
    """
    command = [RSGAIN_PATH, *RSGAIN_SCAN_ARGS, "-l", ALBUM_LOUDNESS, *files]
    print(f"🔊 Scanning album with {ALBUM_LOUDNESS} LUFS")
    started = time.monotonic()
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError:
        run_log.scan = "FAILED (rsgain not found)"
        run_log.error(f"rsgain not found at {RSGAIN_PATH}")
        return False

    if result.returncode != 0:
        run_log.scan = f"FAILED (exit code {result.returncode})"
        run_log.scan_output = "\n".join(filter(None, (strip_ansi(result.stdout), strip_ansi(result.stderr))))
        run_log.error(f"rsgain scan failed (exit code {result.returncode}):\n{strip_ansi(result.stderr)}")
        return False

    run_log.scan = f"OK ({time.monotonic() - started:.1f} s)"
    run_log.scan_ok = True
    print("✅ Processed album successfully")
    print_output(result)
    return True


def remove_replaygain_tags(files):
    """Remove the ReplayGain tags of each file (not used by the workflow below)."""
    for file in files:
        print(f"🗑 Removing ReplayGain tags for: {file}")
        try:
            result = subprocess.run([RSGAIN_PATH, "custom", "-s", "d", file],
                                    check=True, capture_output=True, text=True, encoding="utf-8")
            print(f"✅ Processed file successfully: {file}")
            print_output(result)
        except subprocess.CalledProcessError as e:
            print(f"❌ Error processing file: {file}\nError: {e}\nstdout: {e.stdout}\nstderr: {e.stderr}")


# ---------------------------------------------------------------------------
# Tag post-processing
# ---------------------------------------------------------------------------

def adjust_gain_to_prevent_clipping(files, run_log):
    """Lowers track gains that would push the track peak above SAFETY_THRESHOLD.

    Also records each track's rsgain values in the run log.
    """
    for file in files:
        try:
            audio = load_audio(file)
            values = read_track_values(audio, file, run_log)
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
            if final_track_peak <= SAFETY_THRESHOLD:
                continue

            max_safe_gain = round(20 * math.log10(SAFETY_THRESHOLD / track_peak) - SAFETY_MARGIN, 2)
            set_tag(audio, TRACK_GAIN, f"{max_safe_gain} dB")
            audio.save()
            run_log.tracks[file].update(final=max_safe_gain, note="reduced to prevent clipping")
            print(f"⚠️ Clipping detected in {file}! Reduced track gain to {max_safe_gain} dB.")

        except Exception as e:
            run_log.track(file, note="error")
            run_log.error(f"Clipping check failed for {file}: {e}")


def update_album_gain_metadata(files, run_log):
    """Assigns album gain to match the lowest track gain and album peak to the highest track peak."""
    track_gains, track_peaks = [], []

    for file in files:
        try:
            values = read_track_values(load_audio(file), file, run_log)
            if values is None:
                continue
            track_gain, track_peak = values
            track_gains.append(track_gain)
            track_peaks.append(track_peak)
            print(f"🔍 Read metadata for {file}: Track Gain={track_gain} dB, Track Peak={track_peak}")

        except Exception as e:
            run_log.error(f"Could not read metadata from {file}: {e}")

    if not track_gains:
        run_log.warn("No valid ReplayGain data extracted, album gain not written. Ensure files are correctly tagged.")
        return

    album_gain = min(track_gains)
    album_peak = max(track_peaks)
    run_log.album = (album_gain, album_peak)
    print(f"🎵 Computed Album Gain: {album_gain} dB, Album Peak: {album_peak}")

    for file in files:
        try:
            audio = load_audio(file)
            set_tag(audio, ALBUM_GAIN, f"{album_gain} dB")
            set_tag(audio, ALBUM_PEAK, str(album_peak))
            set_comment(audio, LUFS_COMMENT)
            audio.save()
            print(f"✍️ Wrote album gain {album_gain} dB, album peak {album_peak} → {file}")

        except Exception as e:
            run_log.error(f"Could not write album metadata to {file}: {e}")


def copy_replaygain_metadata(run_log):
    """Copies the cwd's album ReplayGain data to the files of matching subfolders.

    In TRACK_DATA_SUBFOLDER_KEYWORDS folders (e.g. 'lite', but not TRACK_DATA_EXCLUDED_KEYWORDS
    ones like 'bonus'), the track ReplayGain data of the file with the same name in
    the cwd is copied too.
    """
    subfolders = find_keyword_subfolders()
    run_log.subfolders = []
    if not subfolders:
        print(f"No folders matching {', '.join(SUBFOLDER_KEYWORDS)} found in the current working directory.")
        return

    cwd_files = list_audio_files()

    # Every cwd file now holds the same album data: the first one serves as reference
    reference_file = cwd_files[0]
    try:
        reference = load_audio(reference_file)
        album_data = {tag: get_tag(reference, tag) for tag in (ALBUM_GAIN, ALBUM_PEAK)}
        album_data = {tag: value for tag, value in album_data.items() if value is not None}
    except Exception as e:
        run_log.error(f"Could not read reference file {reference_file}, subfolders not updated: {e}")
        return

    for folder in subfolders:
        name = folder.lower()
        copy_track_data = (any(keyword in name for keyword in TRACK_DATA_SUBFOLDER_KEYWORDS)
                           and not any(keyword in name for keyword in TRACK_DATA_EXCLUDED_KEYWORDS))
        folder_files = list_audio_files(folder)
        updated = with_track_data = 0

        for audio_file in folder_files:
            try:
                audio = load_audio(os.path.join(folder, audio_file))
                for tag, value in album_data.items():
                    set_tag(audio, tag, value)

                if copy_track_data and audio_file in cwd_files:
                    try:
                        cwd_audio = load_audio(audio_file)
                        for tag in (TRACK_GAIN, TRACK_PEAK):
                            value = get_tag(cwd_audio, tag)
                            if value is not None:
                                set_tag(audio, tag, value)
                        with_track_data += 1
                        print(f"🎵 Copied track ReplayGain data from CWD -> {audio_file} in '{folder}'")
                    except Exception as e:
                        run_log.error(f"Could not read track data from {audio_file} for '{folder}': {e}")

                audio.save()
                updated += 1
                print(f"✅ ReplayGain metadata copied for {audio_file} in '{folder}'")

            except Exception as e:
                run_log.error(f"Could not update {audio_file} in '{folder}': {e}")

        summary = f"{folder}: {updated}/{len(folder_files)} files got the album data"
        if copy_track_data:
            summary += f", {with_track_data} also got their track data"
        run_log.subfolders.append(summary)


# ---------------------------------------------------------------------------
# Execution workflow
# ---------------------------------------------------------------------------

def process_album():
    """Run the album workflow in the cwd. Returns its RunLog, or None if there is no audio file."""
    files = list_audio_files()
    if not files:
        print("No .mp3/.flac files found in the current working directory.")
        return None

    run_log = RunLog(files)
    try:
        if not scan_album(files, run_log):
            print("⛔ Stopping: the ReplayGain scan failed, so the tags were not post-processed.")
            return run_log
        adjust_gain_to_prevent_clipping(files, run_log)
        update_album_gain_metadata(files, run_log)
        copy_replaygain_metadata(run_log)
    except Exception:
        run_log.crash()
        raise
    finally:
        run_log.write()
        print(f"📝 Report written to {LOG_FILE}: {run_log.result()}")
    return run_log


# ---------------------------------------------------------------------------
# Discography mode
# ---------------------------------------------------------------------------

def find_album_folders(root="."):
    """List the folders under root (root included) that directly hold audio files.

    The lite/bonus/CD2 subfolders of an album are not albums of their own: the
    album's copy step handles them.
    """
    albums = []
    for dirpath, dirnames, _ in os.walk(root):
        dirnames.sort()
        if list_audio_files(dirpath):
            albums.append(dirpath)
            keyword_subfolders = find_keyword_subfolders(dirpath)
            dirnames[:] = [name for name in dirnames if name not in keyword_subfolders]
    return albums


def level_album(folder, target_gain):
    """Set the album gain of an album folder and its keyword subfolders to target_gain.

    Only files whose album gain differs are written; files without an album gain are skipped.
    Returns (changed, skipped, errors).
    """
    paths = [os.path.join(folder, f) for f in list_audio_files(folder)]
    for subfolder in find_keyword_subfolders(folder):
        paths += [os.path.join(folder, subfolder, f) for f in list_audio_files(os.path.join(folder, subfolder))]

    changed, skipped, errors = 0, 0, []
    for path in paths:
        try:
            audio = load_audio(path)
            current = get_tag(audio, ALBUM_GAIN)
            if current is None:
                skipped += 1
                continue
            if parse_replaygain(current) == target_gain:
                continue
            set_tag(audio, ALBUM_GAIN, f"{target_gain} dB")
            audio.save()
            changed += 1
        except Exception as e:
            errors.append(f"Could not level {os.path.relpath(path, folder)}: {e}")
    return changed, skipped, errors


def process_discography():
    """Process every album under the cwd, then give them all the lowest album gain."""
    start, start_clock = datetime.now(), time.monotonic()
    root = os.getcwd()
    album_folders = find_album_folders()
    if not album_folders:
        print("No folder with .mp3/.flac files found under the current working directory.")
        return

    # 1. Album workflow in every album folder
    albums = []  # {"name", "folder", "result", "gain", "changed", "skipped", "errors"}
    for folder in album_folders:
        name = os.path.relpath(folder) if folder != "." else f"{os.path.basename(root)} (this folder)"
        print(f"\n💿 Album: {name}")
        album = {"name": name, "folder": folder, "result": "CRASHED", "gain": None,
                 "changed": None, "skipped": 0, "errors": []}
        try:
            with contextlib.chdir(folder):
                run_log = process_album()
            album["result"] = run_log.result()
            if run_log.album and album["result"] in ("OK", "DONE WITH ERRORS"):
                album["gain"] = run_log.album[0]
        except Exception as e:
            print(f"❌ Album {name} crashed: {e}")
        albums.append(album)

    # 2. Level the albums that were processed successfully
    leveled = [album for album in albums if album["gain"] is not None]
    target = min((album["gain"] for album in leveled), default=None)
    if target is not None:
        print(f"\n🎚 Leveling {plural(len(leveled), 'album')} to album gain {target} dB")
    for album in leveled:
        album["changed"], album["skipped"], album["errors"] = level_album(album["folder"], target)
        for error in album["errors"]:
            print(f"❌ {album['name']}: {error}")
        if album["gain"] == target:
            line = f"album gain already at the discography target ({target} dB)"
        else:
            line = f"album gain {album['gain']} dB -> {target} dB"
        line += f", {plural(album['changed'], 'file')} changed"
        with open(os.path.join(album["folder"], LOG_FILE), "a", encoding="utf-8") as log:
            log.write(f"Discography leveling {datetime.now():%Y-%m-%d %H:%M:%S}: {line}\n")
            log.writelines(f"  ERROR  {error}\n" for error in album["errors"])

    # 3. Summary in the artist folder
    write_discography_summary(start, time.monotonic() - start_clock, albums, target)
    print(f"\n📝 Discography summary written to {LOG_FILE}")


def write_discography_summary(start, duration, albums, target):
    header = ("Album", "Result", "Album gain", "Leveled to", "Files changed")
    rows = []
    for album in albums:
        is_leveled = album["changed"] is not None
        result = album["result"]
        if result == "OK" and album["gain"] is None:
            result = "OK (no album gain)"
        rows.append((album["name"], result, format_gain(album["gain"]),
                     format_gain(target) if is_leveled else "not leveled",
                     str(album["changed"]) if is_leveled else "-"))
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]
    table = ["  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in [header, *rows]]

    n_leveled = sum(1 for album in albums if album["changed"] is not None)
    issues = [f"  {album['name']}: {album['result']}, not leveled. See {os.path.join(album['name'], LOG_FILE)}"
              for album in albums if album["changed"] is None]
    issues += [f"  {album['name']}: {error}" for album in albums for error in album["errors"]]
    issues += [f"  {album['name']}: {album['skipped']} file(s) without album gain, not leveled"
               for album in albums if album["skipped"]]
    lines = [
        "=" * 78,
        f"Discography run {start:%Y-%m-%d %H:%M:%S}   target {ALBUM_LOUDNESS} LUFS",
        f"Folder: {os.getcwd()}",
        f"Albums: {len(albums)} ({n_leveled} leveled, {len(albums) - n_leveled} not leveled)",
        "",
        *table,
        "",
        f"Discography album gain: {target} dB (lowest album gain)" if target is not None
        else "Discography album gain: none, no album was processed successfully",
        "",
        "Errors / notes:",
        *(issues or ["  none"]),
        "",
        f"Result: {'OK' if n_leveled == len(albums) and not issues else 'CHECK ALBUMS'}. "
        f"{plural(len(albums), 'album')}, {n_leveled} leveled, {duration:.1f} s",
        "",
    ]
    with open(LOG_FILE, "a", encoding="utf-8") as log:
        log.write("\n".join(lines) + "\n")


def gain_step(discography):
    """Run the ReplayGain step on the album in the cwd, or on every album under it."""
    if discography:
        process_discography()
    else:
        process_album()


# ###########################################################################
# Main
# ###########################################################################

def main():
    global DRY_RUN
    parser = argparse.ArgumentParser(
        description="Rename the music files of the current folder from their tags, then apply ReplayGain.")
    steps = parser.add_mutually_exclusive_group()
    steps.add_argument("--rename-only", action="store_true", help="only rename the files (and delete junk)")
    steps.add_argument("--gain-only", action="store_true", help="only apply ReplayGain")
    parser.add_argument("--discography", action="store_true",
                        help="ReplayGain on every album under the current folder, then level their album gains")
    parser.add_argument("--dry-run", action="store_true",
                        help="preview the rename step without changing anything (skips ReplayGain)")
    args = parser.parse_args()
    DRY_RUN = DRY_RUN or args.dry_run
    do_rename, do_gain = not args.gain_only, not args.rename_only

    problems = validate_config() if do_rename else []
    if problems:
        print("The configuration at the top of the script has problems, nothing was changed:")
        for problem in problems:
            print(f"  - {problem}")
    else:
        if do_rename:
            print("=== Rename ===")
            rename_step()
        if do_gain:
            print("\n=== ReplayGain ===")
            if DRY_RUN:
                print("Skipped: dry run (ReplayGain would tag files under names that are about to change).")
            else:
                gain_step(args.discography)

    if PAUSE_AT_END:
        input("\nPress enter to exit")
    if problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
