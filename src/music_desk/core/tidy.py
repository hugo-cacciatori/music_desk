"""Tidy (SPEC §28): what tidying a folder tree would do to each album. Nothing on disk changes here; the job runs
the plan (services/tidy_job.py).

The albums are found as ReplayGain finds them: every folder holding audio, with its keyword subfolders handled by
it. lite and bonus subfolders are left to the parent's ReplayGain; disc subfolders (cd2, disc 2…) are split and
renamed like albums, while ReplayGain still runs on their parent, which copies its album values into them.

Each folder is sorted: not split (a .cue naming the one image in it: split it), split (rename to the masks),
half-split (split, and the image and its .cue are offered for deletion), or skipped. Each ReplayGain album then
runs ReplayGain unless every track already has the four values and the comment for the current target.
"""

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path

from music_desk.core import cue as cue_mod
from music_desk.core import rename
from music_desk.core.cancel import CancelToken
from music_desk.core.cue import AlbumPlan, CueError, CueSheet, SourceInfo
from music_desk.core.formats import AUDIO_EXTENSIONS
from music_desk.core.replaygain import (
    ALBUM_GAIN,
    ALBUM_PEAK,
    TRACK_GAIN,
    TRACK_PEAK,
    ReplayGainOptions,
    find_keyword_subfolders,
    list_audio_files,
    lufs_comment_pattern,
)
from music_desk.core.tags import TaggedFile

SPLIT_BY_HAND = "open its .cue to split it by hand"


class Kind(StrEnum):
    NOT_SPLIT = "not split"
    SPLIT = "split"
    HALF_SPLIT = "half-split"  # the image, its .cue and the tracks split from it
    SKIPPED = "skipped"


class TidyCancelled(Exception):
    """plan_tidy was cancelled (it reads every file's tags and every sheet)."""


@dataclass
class FolderPlan:
    """One folder Tidy splits or renames: an album, or a disc subfolder of one."""

    folder: Path
    kind: Kind
    split: AlbumPlan | None = None  # not split: the split to run
    renames: rename.RenamePlan | None = None  # split and half-split: the renames to run (maybe none)
    offer: list[Path] = field(default_factory=list)  # the image and its .cue(s), offered for deletion after the run
    skip: str | None = None  # why nothing is done here (Kind.SKIPPED)
    image: str | None = None  # an unsplit image, split or skipped: never measured as a track
    notes: list[str] = field(default_factory=list)  # what the plan page shows besides (missing tags, a warning…)

    @property
    def rename_count(self) -> int:
        if self.renames is None:
            return 0
        return len(self.renames.with_status(rename.Status.RENAME)) + len(self.renames.with_status(rename.Status.JUNK))

    @property
    def images(self) -> set[str]:
        """The images here (offered, or waiting to be split by hand): not tracks, so ReplayGain leaves them out."""
        names = {path.name for path in self.offer if path.suffix.lower() in AUDIO_EXTENSIONS}
        return names | {self.image} if self.image else names


@dataclass
class AlbumTidy:
    """A ReplayGain album (SPEC §28): its own folder's plan, its disc subfolders' plans, and the ReplayGain check."""

    folder: Path
    folders: list[FolderPlan] = field(default_factory=list)  # its own first, then its disc subfolders
    replaygain: str | None = None  # why ReplayGain runs; None when the tags are already right
    replaygain_exclude: set[str] = field(default_factory=set)  # the album folder's images, never measured

    @property
    def own(self) -> FolderPlan:
        return self.folders[0]

    @property
    def tidy(self) -> bool:
        """Nothing to do: no split, no rename, the ReplayGain tags right, nothing skipped."""
        return self.replaygain is None and all(
            plan.kind in (Kind.SPLIT, Kind.HALF_SPLIT) and not plan.rename_count for plan in self.folders)


@dataclass
class TidyPlan:
    root: Path
    albums: list[AlbumTidy] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)  # folders with audio no tool here can handle

    def folders(self) -> list[FolderPlan]:
        return [plan for album in self.albums for plan in album.folders]

    def summary(self) -> str:
        """"12 albums: 3 to split, 2 to rename, 5 to ReplayGain, 2 already tidy, 1 skipped"."""
        folders = self.folders()
        counts = [
            ("to split", sum(plan.kind is Kind.NOT_SPLIT for plan in folders)),
            ("to rename", sum(plan.rename_count > 0 for plan in folders)),
            ("to ReplayGain", sum(album.replaygain is not None for album in self.albums)),
            ("already tidy", sum(album.tidy for album in self.albums)),
            ("skipped", sum(plan.kind is Kind.SKIPPED for plan in folders) + len(self.skipped)),
        ]
        total = len(self.albums)
        head = f"{total} album{'' if total == 1 else 's'}"
        parts = [f"{count} {label}" for label, count in counts if count]
        return f"{head}: {', '.join(parts)}" if parts else head

    @property
    def has_work(self) -> bool:
        """Something to split, rename or ReplayGain (Run is disabled otherwise)."""
        return any(album.replaygain is not None for album in self.albums) or any(
            plan.kind is Kind.NOT_SPLIT or plan.rename_count for plan in self.folders())


# ---------------------------------------------------------------------------
# Finding the albums
# ---------------------------------------------------------------------------

def is_disc_folder(name: str, options: ReplayGainOptions) -> bool:
    """A keyword subfolder that holds a disc of its album (cd2, disc 2…): any album-data keyword but the track-data
    (lite) and excluded (bonus) ones. Tidy splits and renames these; lite and bonus folders it leaves alone."""
    lower = name.lower()
    hands_off = set(options.track_data_keywords) | set(options.track_data_excluded_keywords)
    return not any(keyword in lower for keyword in hands_off)


def _audio_names(folder: Path) -> list[str]:
    try:
        return [entry.name for entry in os.scandir(folder)
                if entry.is_file() and os.path.splitext(entry.name)[1].lower() in AUDIO_EXTENSIONS]
    except OSError:
        return []


def find_albums(root: Path, options: ReplayGainOptions) -> list[tuple[Path, list[Path]]]:
    """(album folder, its disc subfolders) under root, root included, in Explorer-like sorted order. An album is a
    folder holding any audio file; its keyword subfolders aren't albums (as `replaygain.find_album_folders`, which
    counts only the files rsgain scans: a folder of other audio is listed here and skipped later)."""
    albums = []
    for dirpath, dirnames, _ in os.walk(root):
        dirnames.sort(key=str.casefold)
        folder = Path(dirpath)
        if not _audio_names(folder):
            continue
        keyword = set(find_keyword_subfolders(folder, options))
        discs = [folder / name for name in dirnames if name in keyword and is_disc_folder(name, options)
                 and _audio_names(folder / name)]
        albums.append((folder, discs))
        dirnames[:] = [name for name in dirnames if name not in keyword]
    return albums


# ---------------------------------------------------------------------------
# Sorting one folder
# ---------------------------------------------------------------------------

def _sheets(folder: Path) -> list[tuple[Path, CueSheet | None]]:
    sheets = []
    for entry in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
        if entry.is_file() and entry.suffix.lower() == ".cue":
            try:
                sheets.append((entry, cue_mod.read_cue_sheet(entry)))
            except (OSError, CueError):
                sheets.append((entry, None))
    return sheets


def _image_of(sheet: CueSheet, audio: dict[str, str]) -> str | None:
    """The one image a single-file sheet plays, as named in the folder (`audio`: casefolded name -> name)."""
    names = {name.casefold() for name in sheet.files}
    if len(names) != 1:
        return None
    image = audio.get(next(iter(names)))
    if image is None or Path(image).suffix.lower() not in cue_mod.SUPPORTED:
        return None
    return image


def _split_problem(plan: AlbumPlan) -> str | None:
    """Why a sheet needs a human (SPEC §28): anything the Split page would warn about or refuse."""
    if plan.refusal:
        return plan.refusal
    if plan.warnings:
        return plan.warnings[0]
    if any(track.number == 0 for track in plan.tracks):
        return "it has a hidden track before track 1"
    if plan.problems:
        return plan.problems[0][1]
    if plan.notes:
        return f"track {plan.notes[0][0]}: {plan.notes[0][1]}"
    return None


def plan_folder(folder: Path, rename_options: rename.RenameOptions, name_masks: Iterable[str], replacement: str, *,
                is_silent: Callable[[Path, int, int], bool] | None = None,
                read_info: Callable[[Path], SourceInfo] = cue_mod.read_source,
                cancel: CancelToken | None = None) -> FolderPlan:
    """What Tidy does in one folder (its subfolders aside)."""
    audio = {name.casefold(): name for name in _audio_names(folder)}
    sheets = _sheets(folder)
    images: dict[str, list[Path]] = {}  # image name -> the sheets playing it
    for path, sheet in sheets:
        image = _image_of(sheet, audio) if sheet is not None else None
        if image is not None:
            images.setdefault(image, []).append(path)

    if len(audio) == 1 and images:
        [(image, cues)] = images.items()
        if len(cues) > 1:
            return FolderPlan(folder, Kind.SKIPPED, skip=f"{len(cues)} sheets name {image}: {SPLIT_BY_HAND}",
                              image=image)
        sheet = next(sheet for path, sheet in sheets if path == cues[0])
        assert sheet is not None
        try:
            plan = cue_mod.plan_sheet(sheet, cues[0], list(name_masks), replacement, is_silent=is_silent,
                                      read_info=read_info)
        except Exception as e:  # noqa: BLE001 - an album that can't be planned is skipped, not the whole tree
            return FolderPlan(folder, Kind.SKIPPED, skip=f"{cues[0].name} can't be planned ({type(e).__name__}: "
                                                         f"{e}): {SPLIT_BY_HAND}", image=image)
        problem = _split_problem(plan)
        if problem:
            return FolderPlan(folder, Kind.SKIPPED, split=plan, skip=f"{problem.rstrip('.')}: {SPLIT_BY_HAND}",
                              image=image)
        return FolderPlan(folder, Kind.NOT_SPLIT, split=plan, offer=[folder / image, cues[0]], image=image)

    options = replace(rename_options, masks=tuple(name_masks), replacement=replacement, recursive=False)
    try:
        renames = rename.plan_renames(folder, options, cancel=cancel)
    except rename.PlanCancelled as e:
        raise TidyCancelled from e
    kind, offer = Kind.SPLIT, []
    if images:  # the image beside the tracks split from it
        kind = Kind.HALF_SPLIT
        for image, cues in images.items():
            offer += [folder / image, *cues]
    plan = FolderPlan(folder, kind, renames=renames, offer=offer)
    for status in (rename.Status.MISSING_TAGS, rename.Status.NAME_TAKEN, rename.Status.UNREADABLE):
        items = renames.with_status(status)
        if items:
            plan.notes.append(f"{len(items)} file{'' if len(items) == 1 else 's'} not renamed ({status})")
    plan.notes += renames.warnings
    return plan


# ---------------------------------------------------------------------------
# The ReplayGain check
# ---------------------------------------------------------------------------

def _comment_target(comment: str | None, options: ReplayGainOptions) -> float | None | bool:
    """The target in the ReplayGain step's comment line: a number, False with no such line, None when the
    template has no {target} (then the line's presence is all that counts)."""
    pattern = lufs_comment_pattern(options.comment)
    for line in (comment or "").splitlines():
        if pattern.fullmatch(line):
            if "{target}" not in options.comment:
                return None
            before = options.comment.split("{target}")[0].strip()
            rest = line.strip()[len(before):].strip()
            number = rest.split()[0] if rest.split() else ""
            try:
                return float(number.replace(",", "."))
            except ValueError:
                return None
    return False


def replaygain_needed(album: Path, discs: Iterable[Path], options: ReplayGainOptions,
                      exclude: Iterable[str] = ()) -> str | None:
    """Why the album needs a ReplayGain run, or None when every track rsgain scans (the images in `exclude`
    aside) has the four values and the comment for the current target, and its disc subfolders' tracks have the
    album values the run copies there."""
    skip = {name.casefold() for name in exclude}
    files = [name for name in list_audio_files(album)[0] if name.casefold() not in skip]
    if not files:
        return None
    targets = set()
    for name in files:
        try:
            tagged = TaggedFile(album / name, shared=True)
            values = [tagged.get_replaygain(key) for key in (TRACK_GAIN, TRACK_PEAK, ALBUM_GAIN, ALBUM_PEAK)]
            comment = tagged.fields().comment
        except Exception:  # noqa: BLE001 - a file that can't be read: the run reports it
            return f"{name} can't be read"
        if any(value is None for value in values):
            return "missing tags"
        target = _comment_target(comment, options)
        if target is False:
            return "no target in the comment"
        targets.add(target)
    wanted = options.target
    others = {t for t in targets if t is not None and abs(t - wanted) > 1e-6}
    if others:
        now = ", ".join(f"{t:g}" for t in sorted(others))
        return f"target {now} LUFS, now {options.target_text}"
    for disc in discs:
        for name in list_audio_files(disc)[0]:
            try:
                tagged = TaggedFile(disc / name, shared=True)
                if tagged.get_replaygain(ALBUM_GAIN) is None or tagged.get_replaygain(ALBUM_PEAK) is None:
                    return f"missing tags in {disc.name}"
            except Exception:  # noqa: BLE001
                return f"{disc.name}\\{name} can't be read"
    return None


# ---------------------------------------------------------------------------
# The whole tree
# ---------------------------------------------------------------------------

def plan_tidy(root: str | Path, rename_options: rename.RenameOptions, replaygain_options: ReplayGainOptions,
              name_masks: Iterable[str], replacement: str, *,
              is_silent: Callable[[Path, int, int], bool] | None = None,
              read_info: Callable[[Path], SourceInfo] = cue_mod.read_source,
              cancel: CancelToken | None = None,
              progress: Callable[[int, int, str], None] | None = None) -> TidyPlan:
    """The dry run of Tidy over `root` and every subfolder (SPEC §28). Raises TidyCancelled on a cancel."""
    root = Path(root)
    masks = list(name_masks)
    tidy = TidyPlan(root)
    found = find_albums(root, replaygain_options)
    for n, (folder, discs) in enumerate(found):
        if cancel is not None and cancel.cancelled:
            raise TidyCancelled
        if progress is not None:
            progress(n, len(found), str(folder))
        album = AlbumTidy(folder)
        for each in (folder, *discs):
            album.folders.append(plan_folder(each, rename_options, masks, replacement, is_silent=is_silent,
                                             read_info=read_info, cancel=cancel))
        own = album.own
        album.replaygain_exclude = own.images
        scanned = list_audio_files(folder)[0]
        tracks = [name for name in scanned if name not in own.images]
        if own.kind is Kind.SKIPPED and not tracks:
            pass  # an image waiting to be split by hand: nothing to measure yet
        elif not scanned:
            # Only formats rsgain can't read: renamed all the same, never measured.
            own.notes.append("no file here is a format ReplayGain reads")
        elif any(plan.kind is Kind.NOT_SPLIT for plan in album.folders):
            album.replaygain = "after the split"
        else:
            album.replaygain = replaygain_needed(folder, [plan.folder for plan in album.folders[1:]],
                                                 replaygain_options, own.images)
        tidy.albums.append(album)
    if progress is not None:
        progress(len(found), len(found), "")
    return tidy
