"""Discography mode: every album under a folder, one at a time, then all leveled to the lowest album gain."""

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from refrain.core import rsgain
from refrain.core.cancel import CancelToken
from refrain.core.replaygain.album import (
    ALBUM_GAIN,
    Scanner,
    find_keyword_subfolders,
    image_names,
    list_audio_files,
    parse_replaygain,
    process_album,
)
from refrain.core.replaygain.options import DEFAULT_OPTIONS, ReplayGainOptions
from refrain.core.replaygain.run_log import Clock, NullReporter, Reporter, RunLog, format_gain, plural
from refrain.core.tags import TaggedFile

SUMMARY_COLUMNS = ("Album", "Result", "Album gain", "Leveled to", "Files changed")  # the summary's table


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
    header = SUMMARY_COLUMNS
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
