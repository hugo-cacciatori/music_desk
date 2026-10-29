"""The album workflow (SPEC §10 B): rsgain album scan, clipping fix, album values and LUFS comment, subfolder
copies; same order, values and report text as the script."""

import logging
import math
import os
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path

from refrain import paths
from refrain.core import cue, rsgain
from refrain.core.cancel import CancelToken
from refrain.core.formats import AUDIO_EXTENSIONS, REPLAYGAIN_EXTENSIONS
from refrain.core.replaygain.options import DEFAULT_OPTIONS, ReplayGainOptions
from refrain.core.replaygain.run_log import Clock, NullReporter, Reporter, RunLog
from refrain.core.tags import TaggedFile

log = logging.getLogger(__name__)


TRACK_GAIN = "replaygain_track_gain"
TRACK_PEAK = "replaygain_track_peak"
ALBUM_GAIN = "replaygain_album_gain"
ALBUM_PEAK = "replaygain_album_peak"


Scanner = Callable[..., rsgain.ScanResult]


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
