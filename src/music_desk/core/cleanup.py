"""What the Redundant files window offers for the Recycle Bin (SPEC §28 "Clutter", §32): Clean up's junk (the
script's junk cleanup, which left the Rename tool), pictures and videos beside music, and clutter-only folders.
Tidy's plan calls `find_clutter` for its clutter; Clean up calls `find_items` with its options.

Plain Python, read-only: it lists, never deletes. The window recycles what the user leaves checked.
"""

import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from music_desk.core import rename
from music_desk.core.cancel import CancelToken
from music_desk.core.formats import AUDIO_EXTENSIONS
from music_desk.core.fsops import natural_key

PICTURE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"})
VIDEO_EXTENSIONS = frozenset({".mkv", ".avi", ".mov", ".wmv", ".m4v", ".webm", ".mpg", ".mpeg", ".vob"})
IGNORED_NAMES = frozenset({"thumbs.db", "desktop.ini"})  # Windows' own: they count as nothing in a folder
DEFAULT_JUNK_EXTENSIONS = frozenset({".cue", ".nfo", ".sfv", ".txt", ".lrc"})  # the script's, without .m3u
PLAYLIST_EXTENSIONS = frozenset({".m3u", ".m3u8", ".pls"})

PICTURE_BESIDE_MUSIC = "picture beside music"
VIDEO_BESIDE_MUSIC = "video beside music"


@dataclass(frozen=True)
class Lists:
    """What a picture and a video are (Clean up's lists, which Tidy reads, SPEC §32). Extensions, lowercase."""

    pictures: frozenset[str] = PICTURE_EXTENSIONS
    videos: frozenset[str] = VIDEO_EXTENSIONS


DEFAULT_LISTS = Lists()


@dataclass(frozen=True)
class Item:
    """One row of the Redundant files window: a file, or a whole folder (`lists` set: a clutter folder, re-checked
    against them before it goes)."""

    path: Path
    why: str  # "picture beside music", "empty folder", "image split into 12 tracks"…
    label: str = ""  # the File column; the name when empty ("Artwork (folder, 12 pictures)")
    lists: Lists | None = None  # a clutter folder: what it may hold
    kept: str | None = None  # listed greyed, never recycled ("kept: used by Kid A.cue")

    @property
    def is_folder(self) -> bool:
        return self.lists is not None

    @property
    def name(self) -> str:
        return self.label or self.path.name


@dataclass
class _Contents:
    pictures: int = 0
    videos: int = 0
    other: bool = False  # anything else: not clutter
    counts: Counter = field(default_factory=Counter)


def _kind(name: str, lists: Lists) -> str:
    """"picture", "video", "ignored", "audio" or "other", by name."""
    if name.casefold() in IGNORED_NAMES:
        return "ignored"
    extension = os.path.splitext(name)[1].lower()
    if extension in lists.pictures:
        return "picture"
    if extension in lists.videos:
        return "video"
    if extension in AUDIO_EXTENSIONS:
        return "audio"
    return "other"


def folder_label(path: Path, pictures: int, videos: int) -> tuple[str, str]:
    """(the File column, the Why column) of a clutter folder."""
    if not pictures and not videos:
        return f"{path.name} (empty folder)", "empty folder"
    parts = [f"{n} {word}{'s' if n != 1 else ''}" for n, word in ((pictures, "picture"), (videos, "video")) if n]
    return f"{path.name} (folder, {', '.join(parts)})", f"folder of {' and '.join(parts)}"


def find_clutter(root: str | Path, lists: Lists = DEFAULT_LISTS, cancel: CancelToken | None = None) -> list[Item]:
    """Pictures and videos in every folder holding audio under `root` (root included), and the outermost folders
    whose whole contents are pictures, videos or nothing, in Explorer's order (SPEC §28). `root` itself is never
    offered. Links and junctions are never followed, and count as something else."""
    root = Path(root)
    items: list[Item] = []
    _visit(root, lists, items, cancel, is_root=True)
    return items


def _visit(folder: Path, lists: Lists, items: list[Item], cancel: CancelToken | None,
           is_root: bool = False) -> _Contents | None:
    """The folder's contents if it is clutter (pictures, videos, nothing), else None after listing its own items.
    A clutter folder is listed by its parent, so only the outermost one is a row."""
    if cancel is not None and cancel.cancelled:
        return None
    try:
        with os.scandir(folder) as scan:
            entries = sorted(scan, key=lambda e: natural_key(e.name))
    except OSError:
        return None  # unreadable: never offered
    contents = _Contents()
    files: list[tuple[os.DirEntry, str]] = []
    clutter: list[tuple[Path, _Contents]] = []
    for entry in entries:
        if entry.is_symlink() or entry.is_junction():
            contents.other = True
        elif entry.is_dir(follow_symlinks=False):
            sub = _visit(Path(entry.path), lists, items, cancel)
            if sub is None:
                contents.other = True
            else:
                clutter.append((Path(entry.path), sub))
                contents.pictures += sub.pictures
                contents.videos += sub.videos
        else:
            kind = _kind(entry.name, lists)
            files.append((entry, kind))
            if kind == "picture":
                contents.pictures += 1
            elif kind == "video":
                contents.videos += 1
            elif kind != "ignored":
                contents.other = True
    if not contents.other and not is_root:
        return contents  # clutter: its parent lists it whole
    for path, sub in clutter:
        label, why = folder_label(path, sub.pictures, sub.videos)
        items.append(Item(path, why, label, lists))
    if any(kind == "audio" for _entry, kind in files):
        for entry, kind in files:
            if kind in ("picture", "video"):
                why = PICTURE_BESIDE_MUSIC if kind == "picture" else VIDEO_BESIDE_MUSIC
                items.append(Item(Path(entry.path), why))
    return None


def is_clutter(folder: Path, lists: Lists) -> bool:
    """True while the folder still holds only pictures, videos or nothing (a re-check before it is recycled)."""
    if not folder.is_dir():
        return False
    return _visit(folder, lists, [], None) is not None


def recheck(item: Item) -> str | None:
    """Why an item can't go any more (gone, or a folder that now holds something else), or None."""
    if item.is_folder:
        if not item.path.is_dir():
            return "gone"
        assert item.lists is not None
        return None if is_clutter(item.path, item.lists) else "it now holds other files"
    return None if item.path.is_file() else "gone"


# ---------------------------------------------------------------------------
# Clean up (SPEC §32): its options, and what they find
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CleanupOptions:
    """The Options window's Clean up page. Today's junk cleanup is the default; the rest is off."""

    recursive: bool = True
    junk: bool = True
    junk_extensions: frozenset[str] = DEFAULT_JUNK_EXTENSIONS
    pictures: bool = False
    picture_extensions: frozenset[str] = PICTURE_EXTENSIONS
    videos: bool = False
    video_extensions: frozenset[str] = VIDEO_EXTENSIONS
    clutter_folders: bool = False
    protected_names: tuple[str, ...] = ("replaygain.log",)  # the ReplayGain log (its configured name): never junk

    @property
    def lists(self) -> Lists:
        return Lists(self.picture_extensions, self.video_extensions)


DEFAULT_OPTIONS = CleanupOptions()


def validate(options: CleanupOptions) -> list[str]:
    """The problems in the options, empty if they are fine (§10's rules for the junk list, SPEC §32)."""
    problems = rename.check_extensions("Junk file types", options.junk_extensions)
    junk = {e.lower() for e in options.junk_extensions}
    for label, protected in (("music files", AUDIO_EXTENSIONS), ("playlists", PLAYLIST_EXTENSIONS),
                             ("pictures (they have their own box)", options.picture_extensions | PICTURE_EXTENSIONS),
                             ("videos (they have their own box)", options.video_extensions | VIDEO_EXTENSIONS)):
        overlap = junk & {e.lower() for e in protected}
        if overlap:
            problems.append(f"{', '.join(sorted(overlap))} can't be junk: these are {label}")
    return problems + validate_lists(options)


def validate_lists(options: CleanupOptions) -> list[str]:
    """The problems in the picture and video lists alone: what Tidy reads too (SPEC §28)."""
    problems = []
    for name, extensions in (("Picture file types", options.picture_extensions),
                             ("Video file types", options.video_extensions)):
        problems += rename.check_extensions(name, extensions)
    for name, extensions in (("a picture", options.picture_extensions), ("a video", options.video_extensions)):
        audio = {e.lower() for e in extensions} & AUDIO_EXTENSIONS
        if audio:
            problems.append(f"{', '.join(sorted(audio))} can't be {name}: these are music files")
    return problems


def find_items(root: str | Path, options: CleanupOptions, cancel: CancelToken | None = None) -> list[Item]:
    """What Clean up offers in `root` (and its subfolders with `recursive`): junk by extension, then the pictures,
    videos and clutter folders its boxes ask for. Protected files are listed as kept, with their reason."""
    root = Path(root)
    items: list[Item] = []
    if options.junk:
        _junk(root, options, items, cancel)
    if options.pictures or options.videos or options.clutter_folders:
        for item in find_clutter(root, options.lists, cancel):
            if not options.recursive and item.path.parent != root:
                continue
            if (item.is_folder and options.clutter_folders or item.why == PICTURE_BESIDE_MUSIC and options.pictures
                    or item.why == VIDEO_BESIDE_MUSIC and options.videos):
                items.append(item)
    return items


def _junk(folder: Path, options: CleanupOptions, items: list[Item], cancel: CancelToken | None) -> None:
    """The script's junk rules, with the app's keeps (SPEC §10): the ReplayGain log, a single-file album's sheet."""
    if cancel is not None and cancel.cancelled:
        return
    try:
        with os.scandir(folder) as scan:
            entries = [entry for entry in sorted(scan, key=lambda e: natural_key(e.name))
                       if not entry.is_symlink() and not entry.is_junction()]
    except OSError:
        return
    junk = ({e.lower() for e in options.junk_extensions} - AUDIO_EXTENSIONS - PLAYLIST_EXTENSIONS
            - options.picture_extensions - options.video_extensions)
    protected = {name.lower() for name in options.protected_names}
    paths = [Path(entry.path) for entry in entries]
    has_cue = any(path.suffix.lower() == ".cue" for path in paths)
    facts = rename.folder_facts(paths, rename.RenameOptions()) if has_cue else None
    for entry, path in zip(entries, paths, strict=True):
        if entry.is_dir(follow_symlinks=False):
            if options.recursive:
                _junk(path, options, items, cancel)
            continue
        extension = path.suffix.lower()
        if extension not in junk:
            continue
        lower = path.name.lower()
        if lower in protected:
            items.append(Item(path, f"junk: {extension}", kept="kept: the ReplayGain log"))
        elif facts is not None and lower in facts.cue_audio:
            items.append(Item(path, f"junk: {extension}", kept=f"kept: needed to play {facts.cue_audio[lower]}"))
        else:
            items.append(Item(path, f"junk: {extension}"))


def options_to_json(options: CleanupOptions) -> dict:
    """The page's options, as saved in settings.json (the protected log name is the ReplayGain options')."""
    return {
        "recursive": options.recursive,
        "junk": options.junk,
        "junk_extensions": sorted(options.junk_extensions),
        "pictures": options.pictures,
        "picture_extensions": sorted(options.picture_extensions),
        "videos": options.videos,
        "video_extensions": sorted(options.video_extensions),
        "clutter_folders": options.clutter_folders,
    }


def options_from_json(data) -> CleanupOptions:
    """Saved options; a missing or mistyped value falls back to its default (validation shows bad values)."""
    data = data if isinstance(data, dict) else {}
    default = DEFAULT_OPTIONS

    def flag(key: str, fallback: bool) -> bool:
        value = data.get(key)
        return value if isinstance(value, bool) else fallback

    def extensions(key: str, fallback: frozenset[str]) -> frozenset[str]:
        value = data.get(key)
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            return frozenset(value)
        return fallback

    return CleanupOptions(
        recursive=flag("recursive", default.recursive),
        junk=flag("junk", default.junk),
        junk_extensions=extensions("junk_extensions", default.junk_extensions),
        pictures=flag("pictures", default.pictures),
        picture_extensions=extensions("picture_extensions", default.picture_extensions),
        videos=flag("videos", default.videos),
        video_extensions=extensions("video_extensions", default.video_extensions),
        clutter_folders=flag("clutter_folders", default.clutter_folders),
    )
