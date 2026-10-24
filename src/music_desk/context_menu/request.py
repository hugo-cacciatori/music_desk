"""What a start asks of MusicDesk (SPEC §29, §31): a folder to open, or a right-click entry and the clicked paths.

The handler starts `MusicDesk.exe --<entry> --list <file>`: the paths, one per line, are in a file it wrote in
%LOCALAPPDATA%\\MusicDesk\\menu\\, so a large selection isn't cut by the command line's length limit. The file is
read and deleted here. Paths may also be plain arguments (a run from source, tests). Plain Python, no Qt.
"""

import logging
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from music_desk.core.formats import is_audio
from music_desk.core.fsops import natural_key

log = logging.getLogger(__name__)

SETTINGS = "settings"
# ReplayGain (SPEC §31): a folder (or the open one) as an album or as a discography; several folders, each as an
# album; selected tracks as an album; tracks each on its own (one track: "this track").
REPLAYGAIN_ALBUM = "replaygain-album"
REPLAYGAIN_DISCOGRAPHY = "replaygain-discography"
REPLAYGAIN_ALBUMS = "replaygain-albums"
REPLAYGAIN_TRACKS = "replaygain-tracks"
REPLAYGAIN_LOOSE = "replaygain-loose"
REPLAYGAIN_ENTRIES = (REPLAYGAIN_ALBUM, REPLAYGAIN_DISCOGRAPHY, REPLAYGAIN_ALBUMS, REPLAYGAIN_TRACKS, REPLAYGAIN_LOOSE)
RENAME = "rename"  # any mix of folders and files: one preview (SPEC §31)
REMOVE_REPLAYGAIN = "remove-replaygain"  # selected tracks only: the question, then a report window
TIDY = "tidy"  # folders (or the open one): one plan
CLEANUP = "cleanup"  # folders (or the open one): one Redundant files window
SPLIT = "split"  # one .cue: its Split page
# The entries built so far; the option is "--" + the entry.
ENTRIES = (SETTINGS, *REPLAYGAIN_ENTRIES, RENAME, REMOVE_REPLAYGAIN, TIDY, CLEANUP, SPLIT)
LIST_OPTION = "--list"


@dataclass(frozen=True)
class StartRequest:
    entry: str | None  # a right-click entry, or None: a normal start
    paths: tuple[str, ...] = ()

    @property
    def folder(self) -> str | None:
        """A normal start's folder (SPEC §29): its first path, if any."""
        return self.paths[0] if self.entry is None and self.paths else None


def parse_arguments(arguments: Sequence[str], menu_dir: Path | None = None) -> StartRequest:
    """The request in a start's arguments. Unknown options are ignored, as before; `menu_dir` is where list files
    may be deleted from (None: none is deleted)."""
    entry = None
    paths: list[str] = []
    items = iter(arguments)
    for argument in items:
        if argument == LIST_OPTION:
            list_file = next(items, None)
            if list_file:
                paths.extend(read_list(Path(list_file), menu_dir))
        elif argument.startswith("--") and argument[2:] in ENTRIES:
            entry = argument[2:]
        elif argument and not argument.startswith("-"):
            paths.append(argument)
    return StartRequest(entry, tuple(paths))


def read_list(path: Path, menu_dir: Path | None) -> list[str]:
    """The paths in a list file, one per line. The file is deleted after reading when it is in `menu_dir` (the
    handler's own file); any other file is only read."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        log.warning("couldn't read the list of clicked items %s: %s", path, e)
        return []
    if menu_dir is not None and os.path.normcase(path.resolve().parent) == os.path.normcase(menu_dir.resolve()):
        try:
            path.unlink()
        except OSError as e:
            log.warning("couldn't delete %s: %s", path, e)
    return [line.strip() for line in lines if line.strip()]


def first_track(paths: Iterable[str | Path]) -> Path | None:
    """The track the File names example shows (SPEC §31): the first audio file of the clicked files in natural
    order; else the first track of the first folder (its own files first, then its subfolders, in natural order).
    Reads folders: call it off the UI thread."""
    paths = [Path(p) for p in paths]
    files = sorted((p for p in paths if is_audio(p) and p.is_file()), key=natural_key)
    if files:
        return files[0]
    for folder in sorted((p for p in paths if p.is_dir()), key=natural_key):
        found = _first_track_in(folder)
        if found is not None:
            return found
    return None


def _first_track_in(folder: Path) -> Path | None:
    for root, dirs, names in os.walk(folder):
        dirs.sort(key=natural_key)
        tracks = sorted((name for name in names if is_audio(name)), key=natural_key)
        if tracks:
            return Path(root) / tracks[0]
    return None
