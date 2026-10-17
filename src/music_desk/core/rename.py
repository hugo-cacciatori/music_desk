"""Rename music files from their tags, with an opt-in junk cleanup (SPEC §10, port of music_tools' rename step).

The script renamed while it walked. Here `plan_renames` is the dry run: it walks in the same order and simulates
the renames on a virtual copy of each folder, so "name taken" comes out exactly as the script would report it.
`apply_plan` then executes that plan, re-checking every item. Junk files go to the Recycle Bin, never deleted.
"""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from music_desk.core import fsops
from music_desk.core.cancel import CancelToken
from music_desk.core.formats import AUDIO_EXTENSIONS
from music_desk.core.masks import (  # noqa: F401 - re-exported: the parity tests import them from here
    DEFAULT_MASKS,
    DEFAULT_REPLACEMENT,
    FORBIDDEN_CHARACTERS,
    choose_name,
    mask_fields,
    mask_values,
    missing_from,
    sanitize,
    validate_masks,
)
from music_desk.core.tags import TaggedFile

PLAYLIST_EXTENSIONS = frozenset({".m3u", ".m3u8", ".pls"})
IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png"})
DEFAULT_JUNK_EXTENSIONS = frozenset({".cue", ".nfo", ".sfv", ".txt", ".lrc"})  # the script's, without .m3u


@dataclass(frozen=True)
class RenameOptions:
    """The script's configuration block. Defaults are the script's, except the file types and the junk list."""

    masks: tuple[str, ...] = DEFAULT_MASKS  # filled from the shared setting when a run starts (SPEC §21)
    replacement: str = DEFAULT_REPLACEMENT
    recursive: bool = True
    cleanup: bool = False
    junk_extensions: frozenset[str] = DEFAULT_JUNK_EXTENSIONS
    audio_extensions: frozenset[str] = AUDIO_EXTENSIONS
    protected_names: tuple[str, ...] = ("replaygain.log",)  # the ReplayGain log is never junk


# ---------------------------------------------------------------------------
# Configuration checks (the masks themselves live in core/masks.py, shared app-wide: SPEC §21)
# ---------------------------------------------------------------------------

def validate(options: RenameOptions) -> list[str]:
    """The problems in the options (empty if they are fine): the script's validate_config, plus the cleanup
    protections of SPEC §10."""
    return validate_masks(options.masks, options.replacement) + validate_file_types(options)


def validate_file_types(options: RenameOptions) -> list[str]:
    """The problems in everything but the masks: the file types and the cleanup protections."""
    problems = []
    for name, extensions in (("Music file types", options.audio_extensions),
                             ("Junk file types", options.junk_extensions)):
        for extension in extensions:
            if not extension.startswith(".") or extension != extension.lower():
                problems.append(f"{name}: {extension!r} must be lowercase and start with a dot")

    junk = {e.lower() for e in options.junk_extensions}
    for label, protected in (("music files", AUDIO_EXTENSIONS | set(options.audio_extensions)),
                             ("playlists", PLAYLIST_EXTENSIONS), ("cover images", IMAGE_EXTENSIONS)):
        overlap = junk & protected
        if overlap:
            problems.append(f"{', '.join(sorted(overlap))} can't be junk: these are {label}")
    return problems


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

class Status(StrEnum):
    RENAME = "rename"
    UNCHANGED = "unchanged"
    MISSING_TAGS = "missing tags"
    NAME_TAKEN = "name taken"
    UNREADABLE = "unreadable"
    KEPT = "kept"
    JUNK = "junk"


@dataclass
class PlanItem:
    source: Path
    status: Status
    target: Path | None = None
    detail: str = ""  # missing fields, reason kept, or error
    fallback_missing: str | None = None  # set when a fallback mask was used
    sidecar: "PlanItem | None" = None  # the .lrc that follows an audio rename
    is_audio: bool = False  # counted in "N files scanned"
    size: int = 0
    mtime_ns: int = 0

    @property
    def new_path(self) -> Path:
        """The target, which every RENAME and NAME_TAKEN item has."""
        assert self.target is not None
        return self.target


@dataclass
class RenamePlan:
    folder: Path
    options: RenameOptions
    items: list[PlanItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def with_status(self, status: Status) -> list[PlanItem]:
        return [item for item in self.items if item.status is status]


def snapshot(item: PlanItem) -> PlanItem:
    try:
        stat = item.source.stat()
        item.size, item.mtime_ns = stat.st_size, stat.st_mtime_ns
    except OSError:
        pass
    return item


class _FolderState:
    """The names in a folder as they will be after the planned operations so far (ignoring case, as Windows)."""

    def __init__(self, names: Iterable[str]) -> None:
        self.names = {name.lower() for name in names}

    def taken(self, old: str, new: str) -> bool:
        # A case-only rename points at the same file: not a clash (the script's samefile check).
        return new.lower() in self.names and new.lower() != old.lower()

    def rename(self, old: str, new: str) -> None:
        self.names.discard(old.lower())
        self.names.add(new.lower())

    def remove(self, name: str) -> None:
        self.names.discard(name.lower())


CUE_FILE = re.compile(r'^\s*FILE\s+(?:"([^"]*)"|(\S+))', re.IGNORECASE)
CUE_TRACK = re.compile(r"^\s*TRACK\s+\d+", re.IGNORECASE)


def read_cue(path: Path) -> tuple[list[str], int]:
    """(FILE names, TRACK count) of a cue sheet."""
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")  # never fails; enough to read FILE and TRACK lines
    files, tracks = [], 0
    for line in text.splitlines():
        match = CUE_FILE.match(line)
        if match:
            files.append(match.group(1) if match.group(1) is not None else match.group(2))
        elif CUE_TRACK.match(line):
            tracks += 1
    return files, tracks


@dataclass
class _FolderFacts:
    """What the plan needs to know about a folder before walking it."""

    single_file_cues: dict[str, str]  # lowercased audio name -> cue name, for single-file albums
    cue_audio: dict[str, str]  # lowercased cue name -> the audio file name it plays (single-file albums)
    cue_files: dict[str, list[str]]  # cue name -> the file names it lists
    sidecars: dict[str, Path]  # lowercased audio name -> its .lrc (only when exactly one audio file has the stem)


def folder_facts(entries: list[Path], options: RenameOptions) -> _FolderFacts:
    files = [p for p in entries if p.is_file()]
    by_lower = {p.name.lower(): p for p in files}
    audio = [p for p in files if p.suffix.lower() in options.audio_extensions]

    single_file_cues, cue_audio, cue_files = {}, {}, {}
    for cue in (p for p in files if p.suffix.lower() == ".cue"):
        try:
            names, tracks = read_cue(cue)
        except OSError:
            continue
        cue_files[cue.name] = names
        distinct = {name.lower() for name in names}
        if len(distinct) == 1 and tracks >= 2:
            target = by_lower.get(distinct.pop())
            if target is not None and target.suffix.lower() in options.audio_extensions:
                single_file_cues[target.name.lower()] = cue.name
                cue_audio[cue.name.lower()] = target.name

    # An .lrc is junk like the other default extensions when cleanup is ticked: its lyrics aren't protected since
    # no function relies on the lyrics tag (M12, SPEC §26). A sidecar that stays still follows its audio file.
    sidecars = {}
    for lrc in (p for p in files if p.suffix.lower() == ".lrc"):
        owners = [a for a in audio if a.stem.lower() == lrc.stem.lower()]
        if len(owners) == 1:
            sidecars[owners[0].name.lower()] = lrc
    return _FolderFacts(single_file_cues, cue_audio, cue_files, sidecars)


DEFAULT_OPTIONS = RenameOptions()


class PlanCancelled(Exception):
    """plan_renames was cancelled (it reads every file's tags, which takes a while on a big tree)."""


def plan_renames(folder: str | Path, options: RenameOptions = DEFAULT_OPTIONS,
                 selection: Iterable[str | Path] | None = None, cancel: CancelToken | None = None) -> RenamePlan:
    """The dry run: what renaming `folder` would do. Nothing on disk changes.

    With `selection`, only those audio files (and their sidecars) are planned, and there is no junk cleanup.
    A cancel raises PlanCancelled.
    """
    plan = RenamePlan(Path(folder), options)
    selected = {Path(p).resolve() for p in selection} if selection is not None else None
    _plan_folder(Path(folder), options, selected, plan, cancel)
    return plan


def _plan_folder(folder: Path, options: RenameOptions, selected: set[Path] | None, plan: RenamePlan,
                 cancel: CancelToken | None = None) -> None:
    if cancel is not None and cancel.cancelled:
        raise PlanCancelled
    entries = list(folder.iterdir())
    facts = folder_facts(entries, options)
    state = _FolderState(p.name for p in entries)
    handled_sidecars: set[str] = set()
    cleanup = options.cleanup and selected is None
    junk_extensions = {e.lower() for e in options.junk_extensions} - AUDIO_EXTENSIONS - PLAYLIST_EXTENSIONS \
        - IMAGE_EXTENSIONS - set(options.audio_extensions)
    protected = {name.lower() for name in options.protected_names}
    renamed_names: set[str] = set()

    def is_junk(path: Path) -> bool:
        lower = path.name.lower()
        if not cleanup or path.suffix.lower() not in junk_extensions or lower in protected:
            return False
        return lower not in facts.cue_audio

    # A sidecar follows its audio file only if it stays; a junk .lrc goes to the Recycle Bin instead.
    staying_sidecars = {audio: lrc for audio, lrc in facts.sidecars.items() if not is_junk(lrc)}

    for path in entries:
        if cancel is not None and cancel.cancelled:
            raise PlanCancelled
        if path.is_dir():
            if options.recursive:
                _plan_folder(path, options, selected, plan, cancel)
            continue
        if not path.is_file():
            continue
        extension = path.suffix.lower()
        lower = path.name.lower()

        if extension in options.audio_extensions:
            if selected is not None and path.resolve() not in selected:
                continue
            item = _plan_audio(path, options, facts, staying_sidecars, state, handled_sidecars, plan)
            item.is_audio = True
            plan.items.append(item)
            if item.status is Status.RENAME:
                renamed_names.add(lower)
        elif lower in handled_sidecars:
            continue  # moved along with its audio file
        elif is_junk(path):
            plan.items.append(snapshot(PlanItem(path, Status.JUNK)))
            state.remove(path.name)
        elif cleanup and extension in junk_extensions and lower not in protected:
            # A junk extension, kept case by case (SPEC §10): a single-file album's cue sheet.
            plan.items.append(snapshot(PlanItem(path, Status.KEPT, detail=f"needed to play {facts.cue_audio[lower]}")))

    if not cleanup:
        for cue, names in facts.cue_files.items():
            if cue in facts.single_file_cues.values():
                continue
            if any(name.lower() in renamed_names for name in names):
                plan.warnings.append(f"{cue} lists files that will be renamed: it won't match them anymore")


def _plan_audio(path: Path, options: RenameOptions, facts: _FolderFacts, sidecars: dict[str, Path],
                state: _FolderState, handled_sidecars: set[str], plan: RenamePlan) -> PlanItem:
    cue = facts.single_file_cues.get(path.name.lower())
    if cue is not None:
        return snapshot(PlanItem(path, Status.KEPT, detail=f"used by {cue}"))
    try:
        fields = mask_values(TaggedFile(path).rename_fields(), options.replacement)
    except Exception as e:  # noqa: BLE001 - any unreadable file is reported, as the script did
        return snapshot(PlanItem(path, Status.UNREADABLE, detail=f"could not read the tags ({e})"))

    choice = choose_name(fields, options.masks)
    if choice is None:
        return snapshot(PlanItem(path, Status.MISSING_TAGS, detail=missing_from(options.masks[0], fields)))

    new_stem, mask_index = choice
    new_name = f"{new_stem}{path.suffix.lower()}"
    target = path.with_name(new_name)
    if path.name == new_name:
        return snapshot(PlanItem(path, Status.UNCHANGED))
    if state.taken(path.name, new_name):
        return snapshot(PlanItem(path, Status.NAME_TAKEN, target=target))

    item = snapshot(PlanItem(path, Status.RENAME, target=target))
    if mask_index > 0:
        item.fallback_missing = missing_from(options.masks[0], fields)
    state.rename(path.name, new_name)

    lrc = sidecars.get(path.name.lower())
    if lrc is not None:
        handled_sidecars.add(lrc.name.lower())
        lrc_target = lrc.with_name(f"{new_stem}{lrc.suffix}")
        if lrc.name == lrc_target.name:
            pass
        elif state.taken(lrc.name, lrc_target.name):
            item.sidecar = snapshot(PlanItem(lrc, Status.NAME_TAKEN, target=lrc_target))
            plan.warnings.append(f"{lrc.name} stays: {lrc_target.name} already exists")
        else:
            item.sidecar = snapshot(PlanItem(lrc, Status.RENAME, target=lrc_target))
            state.rename(lrc.name, lrc_target.name)
    return item


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

@dataclass
class RenameReport:
    """What happened, with the script's sections (same entry texts) plus the app's additions."""

    scanned: int = 0
    renamed: list[str] = field(default_factory=list)
    fallback: list[str] = field(default_factory=list)
    missing_tags: list[str] = field(default_factory=list)
    name_taken: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    junk: list[str] = field(default_factory=list)  # sent to the Recycle Bin
    kept: list[str] = field(default_factory=list)
    sidecars_renamed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # changed since the preview
    warnings: list[str] = field(default_factory=list)
    cancelled: bool = False
    # What happened on disk, for the app's index: (old path, new path) per rename, sidecars included, in order;
    # and the files sent to the Recycle Bin.
    steps: list[tuple[Path, Path]] = field(default_factory=list)
    recycled: list[Path] = field(default_factory=list)


def unchanged_since_plan(item: PlanItem) -> bool:
    try:
        stat = item.source.stat()
    except OSError:
        return False
    return (stat.st_size, stat.st_mtime_ns) == (item.size, item.mtime_ns)


def target_free(item: PlanItem) -> bool:
    return not item.new_path.exists() or fsops.is_same_file(item.source, item.new_path)


def apply_plan(plan: RenamePlan, recycle: Callable[[Path], None] = fsops.send_to_recycle_bin,
               cancel: CancelToken | None = None) -> RenameReport:
    """Execute exactly the planned operations, re-checking each one. Junk goes through `recycle`."""
    report = RenameReport(warnings=list(plan.warnings))
    for item in plan.items:
        if cancel is not None and cancel.cancelled:
            report.cancelled = True
            break
        name = item.source.name
        if item.is_audio:
            report.scanned += 1

        match item.status:
            case Status.UNCHANGED:
                pass
            case Status.MISSING_TAGS:
                report.missing_tags.append(f"{name} (missing: {item.detail})")
            case Status.NAME_TAKEN:
                report.name_taken.append(f"{name} -> {item.new_path.name}")
            case Status.UNREADABLE:
                report.errors.append(f"{name}: {item.detail}")
            case Status.KEPT:
                report.kept.append(f"{name} (kept: {item.detail})")
            case Status.JUNK:
                if not unchanged_since_plan(item):
                    report.skipped.append(f"{name}: changed since the preview, not sent to the Recycle Bin")
                    continue
                try:
                    recycle(item.source)
                except fsops.CannotRecycle as e:  # never deleted for good instead (SPEC §19)
                    report.kept.append(f"{name} (kept: can't be sent to the Recycle Bin: {e})")
                    continue
                except OSError as e:
                    report.errors.append(f"{name}: could not be sent to the Recycle Bin ({e})")
                    continue
                report.junk.append(name)
                report.recycled.append(item.source)
            case Status.RENAME:
                _apply_rename(item, report)
    return report


def preview_report(plan: RenamePlan) -> RenameReport:
    """What apply_plan would report if nothing changes meanwhile: the preview's counts and lists."""
    report = RenameReport(warnings=list(plan.warnings))
    for item in plan.items:
        name = item.source.name
        if item.is_audio:
            report.scanned += 1
        match item.status:
            case Status.MISSING_TAGS:
                report.missing_tags.append(f"{name} (missing: {item.detail})")
            case Status.NAME_TAKEN:
                report.name_taken.append(f"{name} -> {item.new_path.name}")
            case Status.UNREADABLE:
                report.errors.append(f"{name}: {item.detail}")
            case Status.KEPT:
                report.kept.append(f"{name} (kept: {item.detail})")
            case Status.JUNK:
                report.junk.append(name)
            case Status.RENAME:
                report.renamed.append(item.new_path.name)
                if item.fallback_missing is not None:
                    report.fallback.append(f"{item.new_path.name} (missing: {item.fallback_missing})")
                if item.sidecar is not None and item.sidecar.status is Status.RENAME:
                    report.sidecars_renamed.append(f"{item.sidecar.source.name} -> {item.sidecar.new_path.name}")
    return report


def count_files(count: int, verb_singular: str = "", verb_plural: str = "") -> str:
    """'1 file was' / '2 files were' (the script's count_files)."""
    return f"{count} file{'' if count == 1 else 's'}" + (
        f" {verb_singular if count == 1 else verb_plural}" if verb_singular else "")


def summary_sections(report: RenameReport, dry_run: bool) -> list[tuple[str, list[str]]]:
    """The script's print_summary, as (title, entries) sections, with its wording; then the app's additions
    (kept files, sidecars, skipped files, warnings). Empty sections are left out, as the script did, except the
    first line, which is always there."""
    was, were = ("would be", "would be") if dry_run else ("was", "were")
    junk_verb = "sent to the Recycle Bin as junk"
    sections = [
        (f"{len(report.renamed)}/{count_files(report.scanned, was, were)} renamed", report.renamed),
        (f"{count_files(len(report.fallback), was, were)} renamed with a fallback mask", report.fallback),
        (f"{count_files(len(report.missing_tags))} could not be renamed: missing tags", report.missing_tags),
        (f"{count_files(len(report.name_taken))} could not be renamed because another file already has that name",
         report.name_taken),
        (f"{count_files(len(report.errors))} could not be processed", report.errors),
        (f"{count_files(len(report.junk), was, were)} {junk_verb}", report.junk),
        (f"{count_files(len(report.kept))} kept", report.kept),
        (f"{len(report.sidecars_renamed)} .lrc file{'' if len(report.sidecars_renamed) == 1 else 's'} "
         f"{'would follow' if dry_run else 'followed'} {'its' if len(report.sidecars_renamed) == 1 else 'their'} "
         "music file", report.sidecars_renamed),
        (f"{count_files(len(report.skipped))} skipped: changed since the preview", report.skipped),
        ("Warnings", report.warnings),
    ]
    return [sections[0]] + [(title, entries) for title, entries in sections[1:] if entries]


def _apply_rename(item: PlanItem, report: RenameReport) -> None:
    name, new_name = item.source.name, item.new_path.name
    if not unchanged_since_plan(item):
        report.skipped.append(f"{name}: changed since the preview, not renamed")
        return
    if not target_free(item):
        report.name_taken.append(f"{name} -> {new_name}")
        return
    try:
        item.source.rename(item.new_path)
    except OSError as e:
        report.errors.append(f"{name}: could not be renamed to {new_name} ({e})")
        return
    report.steps.append((item.source, item.new_path))
    report.renamed.append(new_name)
    if item.fallback_missing is not None:
        report.fallback.append(f"{new_name} (missing: {item.fallback_missing})")

    sidecar = item.sidecar
    if sidecar is None or sidecar.status is not Status.RENAME:
        return
    if not unchanged_since_plan(sidecar) or not target_free(sidecar):
        report.warnings.append(f"{sidecar.source.name} was not renamed: it changed or "
                               f"{sidecar.new_path.name} exists now")
        return
    try:
        sidecar.source.rename(sidecar.new_path)
    except OSError as e:
        report.warnings.append(f"{sidecar.source.name} could not be renamed ({e})")
        return
    report.steps.append((sidecar.source, sidecar.new_path))
    report.sidecars_renamed.append(f"{sidecar.source.name} -> {sidecar.new_path.name}")


# ---------------------------------------------------------------------------
# Saved options (the Tools page's card defaults, in settings.json)
# ---------------------------------------------------------------------------

def options_to_json(options: RenameOptions) -> dict:
    """The card's own options. The masks and the replacement are the shared Settings.name_masks and
    name_replacement (SPEC §21): older settings files kept them here, and Settings.load migrates them."""
    return {
        "recursive": options.recursive,
        "cleanup": options.cleanup,
        "junk_extensions": sorted(options.junk_extensions),
        "audio_extensions": sorted(options.audio_extensions),
    }


def options_from_json(data) -> RenameOptions:
    """Saved options; a missing or mistyped value falls back to its default, so an old or hand-edited settings
    file never stops the Tools page. Values that are well typed but invalid are kept: validation shows them."""
    data = data if isinstance(data, dict) else {}
    default = DEFAULT_OPTIONS

    def strings(key, fallback):
        value = data.get(key)
        return value if isinstance(value, list) and all(isinstance(v, str) for v in value) else fallback

    def flag(key, fallback):
        value = data.get(key)
        return value if isinstance(value, bool) else fallback

    return RenameOptions(
        recursive=flag("recursive", default.recursive),
        cleanup=flag("cleanup", default.cleanup),
        junk_extensions=frozenset(strings("junk_extensions", sorted(default.junk_extensions))),
        audio_extensions=frozenset(strings("audio_extensions", sorted(default.audio_extensions))),
    )
