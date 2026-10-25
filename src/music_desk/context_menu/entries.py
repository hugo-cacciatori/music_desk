"""The MusicDesk submenu's entries for a selection (SPEC §31, §35): which entries show, in which order, with which
labels and help texts. One list for the app: its own right-click submenu and the toolbar's ReplayGain list take
their entries from here. File Explorer's handler (native/context_menu/menu.cpp) keeps the same rules in C++;
tests/test_menu_handler.py checks the two agree, selection by selection. Plain Python, no Qt.

As the handler, it judges by kind and extension only (folder or file, the extension), never by reading a file.
"""

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from music_desk.context_menu.request import (
    CLEANUP,
    EDIT_CUE,
    REMOVE_REPLAYGAIN,
    RENAME,
    REPLAYGAIN_ALBUM,
    REPLAYGAIN_ALBUMS,
    REPLAYGAIN_DISCOGRAPHY,
    REPLAYGAIN_LOOSE,
    REPLAYGAIN_TRACKS,
    SETTINGS,
    SPLIT,
    TIDY,
    TRIM,
)
from music_desk.core import cue
from music_desk.core.formats import FORMATS

OPEN = "open"  # Open in MusicDesk: a start on the folder (SPEC §29), not an entry option
# Where an entry's window says the options are: it may come from Explorer or from the app (SPEC §35).
OPTIONS_WHERE = "File > Options… in MusicDesk, or MusicDesk > Settings in the right-click menu"

REPLAYGAIN_EXTENSIONS = frozenset(e for f in FORMATS if f.replaygain for e in f.extensions)  # what rsgain scans
MUSIC_EXTENSIONS = frozenset(e for f in FORMATS for e in f.extensions)  # Rename's music files
TRIM_EXTENSIONS = frozenset(cue.SUPPORTED)  # what Trim cuts (the bundled flac)

# Explorer's status bar shows these for the handler's entries; the app shows them as hover texts.
HELP = {
    OPEN: "Opens this folder in MusicDesk",
    REPLAYGAIN_ALBUM: "Runs ReplayGain on this folder as one album, with the target set in MusicDesk",
    REPLAYGAIN_DISCOGRAPHY: "Runs ReplayGain on each album in this folder, then levels them together",
    REPLAYGAIN_ALBUMS: "Runs ReplayGain on each folder as its own album, one after another",
    REPLAYGAIN_TRACKS: "Runs ReplayGain on the selected tracks as one album; the folder's other tracks are left as "
                       "they are",
    REPLAYGAIN_LOOSE: "Runs ReplayGain on each selected track alone, as its own album, with no log",
    RENAME: "Previews renaming the music files from their tags; nothing changes until you apply",
    REMOVE_REPLAYGAIN: "Removes the selected tracks' ReplayGain tags, after a question",
    TIDY: "Plans Tidy (split, rename, ReplayGain) on every album here; nothing changes until you run it",
    CLEANUP: "Lists the redundant files here; you choose what goes to the Recycle Bin",
    SPLIT: "Shows what splitting this cue sheet writes; nothing is split until you click Split",
    EDIT_CUE: "Opens this cue sheet in MusicDesk's cue sheet editor",
    TRIM: "Opens the track's Trim page; the trimmed copy is made only when you click Trim",
    SETTINGS: "Opens MusicDesk's Options window",
}


def _extension(path: str) -> str:
    return os.path.splitext(path)[1].lower()


@dataclass(frozen=True)
class Selection:
    """What was right-clicked: empty space (`background`: `paths` holds the open folder), or the selected items."""

    paths: tuple[str, ...]
    background: bool = False
    folders: int = 0
    files: int = 0
    audio: int = 0  # files rsgain scans
    music: int = 0  # audio files of any format the app knows
    one_folder: bool = True  # every file is in the same folder (a search's results may not be)

    @classmethod
    def of(cls, paths: Iterable[str | os.PathLike[str]], background: bool = False,
           is_dir: Callable[[str], bool] = os.path.isdir) -> "Selection":
        """`is_dir` tells folders from files (the handler asks the disk; the app may ask its index)."""
        paths = tuple(os.fspath(p) for p in paths)
        folders = files = audio = music = 0
        parents = set()
        for path in paths:
            if is_dir(path):
                folders += 1
                continue
            files += 1
            extension = _extension(path)
            audio += extension in REPLAYGAIN_EXTENSIONS
            music += extension in MUSIC_EXTENSIONS
            parents.add(os.path.normcase(os.path.dirname(path)))
        return cls(paths, background, folders, files, audio, music, len(parents) <= 1)


@dataclass(frozen=True)
class MenuEntry:
    entry: str  # the app's entry (`--<entry>`), or OPEN
    label: str
    help: str


def target_text(target: float) -> str:
    """The ReplayGain target as the labels show it: "−15 LUFS", "−18.5 LUFS" (a real minus sign)."""
    return f"{'−' if target < 0 else ''}{abs(target):g} LUFS"


def entry_names(selection: Selection) -> list[str]:
    """The submenu's entries, in order (SPEC §31): Open in MusicDesk on one folder or empty space; the ReplayGain
    entries on folders only or on files with tracks rsgain scans (no folder); Rename on any selection with a folder
    or a music file (empty space too); Remove ReplayGain tags when every item is a track rsgain scans; Tidy and
    Clean up on folders only (empty space too); Split and Edit on one .cue; Trim on one FLAC, WAV or AIFF file;
    Settings always."""
    s = selection
    names = []
    if s.background or (s.folders == 1 and s.files == 0):
        names += [OPEN, REPLAYGAIN_ALBUM, REPLAYGAIN_DISCOGRAPHY]
    elif s.folders > 1 and s.files == 0:
        names.append(REPLAYGAIN_ALBUMS)
    elif s.folders == 0 and s.audio == 1:
        names.append(REPLAYGAIN_LOOSE)  # "this track"
    elif s.folders == 0 and s.audio > 1:
        if s.one_folder:  # an album is one folder's tracks
            names.append(REPLAYGAIN_TRACKS)
        names.append(REPLAYGAIN_LOOSE)
    if s.background or s.folders > 0 or s.music > 0:
        names.append(RENAME)
    if not s.background and s.folders == 0 and s.files > 0 and s.audio == s.files:
        names.append(REMOVE_REPLAYGAIN)
    if s.background or (s.folders > 0 and s.files == 0):
        names += [TIDY, CLEANUP]
    if not s.background and s.folders == 0 and s.files == 1:
        extension = _extension(s.paths[0])
        if extension == ".cue":
            names += [SPLIT, EDIT_CUE]
        if extension in TRIM_EXTENSIONS:
            names.append(TRIM)
    names.append(SETTINGS)
    return names


def label(entry: str, selection: Selection, target: float) -> str:
    """An entry's label (Hugo, 2026-10-24): with what it acts on, and ReplayGain's with the target."""
    s = selection
    fixed = {OPEN: "Open in MusicDesk", SETTINGS: "Settings", RENAME: "Rename from tags…",
             REMOVE_REPLAYGAIN: "Remove ReplayGain tags…", SPLIT: "Split this cue sheet…",
             EDIT_CUE: "Edit this cue sheet…", TRIM: "Trim this track…"}
    if entry in fixed:
        return fixed[entry]
    if entry in (TIDY, CLEANUP):
        what = "this folder" if s.background or s.folders == 1 else f"{s.folders} folders"
        return f"{'Tidy' if entry == TIDY else 'Clean up'} {what}…"
    suffix = f" ({target_text(target)})"
    if entry == REPLAYGAIN_ALBUM:
        return "ReplayGain as an album" + suffix
    if entry == REPLAYGAIN_DISCOGRAPHY:
        return "ReplayGain as a discography" + suffix
    if entry == REPLAYGAIN_ALBUMS:
        return f"ReplayGain: {s.folders} folders, each as an album" + suffix
    if entry == REPLAYGAIN_TRACKS:
        return f"ReplayGain: these {s.audio} tracks as an album" + suffix
    if s.audio == 1:
        return "ReplayGain this track" + suffix
    return f"ReplayGain: each of these {s.audio} tracks on its own" + suffix


def menu_entries(selection: Selection, target: float) -> list[MenuEntry]:
    """The submenu for this selection, with the ReplayGain options' target."""
    return [MenuEntry(name, label(name, selection, target), HELP[name]) for name in entry_names(selection)]
