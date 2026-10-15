"""Temporary .m3u8 playlists for "Play folder", "Play selection" and "Play results" (SPEC §14).

They are the app's own files, written to %LOCALAPPDATA%\\MusicDesk\\playlists and opened with the default player.
Only the newest ones are kept. The playlist builder's export (M7) writes the same format where the user chooses.
"""

import logging
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from music_desk import paths
from music_desk.core.formats import AUDIO_EXTENSIONS
from music_desk.core.fsops import natural_key
from music_desk.core.tags import TaggedFile

log = logging.getLogger(__name__)

KEEP_PLAYLISTS = 20
PREFIX = "play-"


@dataclass(frozen=True)
class PlaylistEntry:
    path: Path
    duration: float | None = None
    artist: str | None = None
    title: str | None = None

    @property
    def label(self) -> str:
        """`Artist - Title`, else the title, else the file name without its extension."""
        artist, title = (self.artist or "").strip(), (self.title or "").strip()
        if artist and title:
            return f"{artist} - {title}"
        return title or self.path.stem


def entry_for(path: str | Path) -> PlaylistEntry:
    """An entry read from the file's tags; a file whose tags can't be read still plays, labelled by its name."""
    path = Path(path)
    try:
        tagged = TaggedFile(path)
        fields = tagged.fields()
        return PlaylistEntry(path, tagged.stream_info().duration, fields.artist, fields.title)
    except Exception as e:  # noqa: BLE001 - a bad file must not stop the playlist
        log.warning("playlist: could not read tags of %s: %s", path, e)
        return PlaylistEntry(path)


def is_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTENSIONS


def folder_tracks(folder: str | Path, recursive: bool = False) -> list[Path]:
    """The folder's audio files in natural order, then (recursive) each subfolder's, also in natural order."""
    folder = Path(folder)
    files, subfolders = [], []
    with os.scandir(folder) as entries:
        for entry in entries:
            if entry.is_dir():
                subfolders.append(Path(entry.path))
            elif entry.is_file() and is_audio(Path(entry.name)):
                files.append(Path(entry.path))
    result = sorted(files, key=lambda p: natural_key(p.name))
    if recursive:
        for subfolder in sorted(subfolders, key=lambda p: natural_key(p.name)):
            result += folder_tracks(subfolder, recursive=True)
    return result


def render(entries: Iterable[PlaylistEntry], relative_to: Path | None = None) -> str:
    """Extended M3U text, audio files only, Windows line endings. Paths are absolute, or relative to the folder
    `relative_to` (where the playlist is saved); a file on another drive keeps its absolute path."""
    lines = ["#EXTM3U"]
    for entry in entries:
        if not is_audio(entry.path):
            continue
        seconds = round(entry.duration) if entry.duration else -1
        lines.append(f"#EXTINF:{seconds},{entry.label}")
        lines.append(playlist_path(entry.path, relative_to))
    return "\r\n".join(lines) + "\r\n"


def playlist_path(path: Path, relative_to: Path | None) -> str:
    absolute = str(path.absolute())
    if relative_to is None:
        return absolute
    base = str(Path(relative_to).absolute())
    if os.path.splitdrive(absolute)[0].casefold() != os.path.splitdrive(base)[0].casefold():
        return absolute  # another drive: no relative path exists
    return os.path.relpath(absolute, base)


def export_playlist(entries: Iterable[PlaylistEntry], target: str | Path, relative: bool) -> Path:
    """The playlist builder's export (SPEC §15): a UTF-8 .m3u8 where the user chose, replacing a file there (the
    Save dialog asked first). Relative paths are relative to the file's folder."""
    target = Path(target)
    text = render(entries, target.parent if relative else None)
    temp = target.with_name(target.name + ".tmp")
    temp.write_bytes(text.encode("utf-8"))
    os.replace(temp, target)  # a crash can't leave half a playlist where the user saved it
    return target


def write_temp_playlist(entries: Iterable[PlaylistEntry], directory: Path | None = None, keep: int = KEEP_PLAYLISTS,
                        now: Callable[[], datetime] = datetime.now) -> Path:
    """Write a UTF-8 .m3u8 under a new timestamped name, then remove all but the newest `keep` playlists."""
    directory = directory or paths.playlists_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = now().strftime("%Y%m%d-%H%M%S")
    target = directory / f"{PREFIX}{stamp}.m3u8"
    counter = 1
    while target.exists():
        counter += 1
        target = directory / f"{PREFIX}{stamp}-{counter}.m3u8"
    target.write_bytes(render(entries).encode("utf-8"))
    prune(directory, keep)
    return target


def prune(directory: Path, keep: int = KEEP_PLAYLISTS) -> None:
    """Remove the oldest temporary playlists. Only the app's own `play-*.m3u8` files in its own folder."""
    playlists = sorted(directory.glob(f"{PREFIX}*.m3u8"), key=lambda p: natural_key(p.name))  # names are timestamps
    for old in playlists[:-keep] if keep else playlists:
        try:
            old.unlink()
        except OSError as e:
            log.warning("could not remove old playlist %s: %s", old, e)
