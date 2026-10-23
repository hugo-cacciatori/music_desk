"""The Advanced search's album searches (SPEC §26): album folders to go through and fix by hand. No Qt.

Each search reads the index on its own read connection (a worker's), not the disk, except the few `.cue` sheets
the "not split" search has to open to see which file they play. A search only lists folders; nothing is changed.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from music_desk.core import cue
from music_desk.core.formats import AUDIO_EXTENSIONS, TagFamily
from music_desk.core.fsops import natural_key
from music_desk.core.index.store import consistent_read, path_of

WITHOUT_REPLAYGAIN = "without_replaygain"
NOT_SPLIT = "not_split"
# The window's options, in order: (key, label).
SEARCHES = ((WITHOUT_REPLAYGAIN, "Find albums without ReplayGain tags"),
            (NOT_SPLIT, "Find albums that are not split"))

_NO_RG = ("t.rg_track_gain IS NULL AND t.rg_track_peak IS NULL AND t.rg_album_gain IS NULL "
          "AND t.rg_album_peak IS NULL")


@dataclass(frozen=True)
class AlbumResult:
    folder_id: int
    path: Path


def run(conn: sqlite3.Connection, search: str, cancelled: Callable[[], bool] | None = None) -> list[AlbumResult]:
    """One search's albums, sorted by path in Explorer's order."""
    with consistent_read(conn):
        if search == WITHOUT_REPLAYGAIN:
            ids = without_replaygain(conn)
        elif search == NOT_SPLIT:
            ids = not_split(conn, cancelled)
        else:
            raise ValueError(search)
        results = [AlbumResult(i, path_of(conn, i)) for i in ids]
    return sorted(results, key=lambda r: natural_key(str(r.path)))


def without_replaygain(conn: sqlite3.Connection) -> list[int]:
    """Folders with at least one track whose tags are read and that has no ReplayGain tag at all (the RG column's
    "none"), so the album needs a ReplayGain run. Unread or unreadable tracks, and formats without tags, aren't
    judged."""
    rows = conn.execute(
        f"""
        SELECT DISTINCT e.parent_id FROM entries e JOIN tracks t ON t.entry_id = e.id
        WHERE t.read_error IS NULL AND t.tag_family IS NOT NULL AND t.tag_family != ? AND {_NO_RG}
        """, (TagFamily.NONE.value,))
    return [r[0] for r in rows]


def not_split(conn: sqlite3.Connection, cancelled: Callable[[], bool] | None = None) -> list[int]:
    """Folders still in the form of an EAC rip (Hugo, 2026-10-17): a `.cue` whose FILE lines name one FLAC, WAV or
    AIFF file of that folder, and no other audio file beside it, so it was never split. The log isn't required.
    The index finds the candidates (a .cue and exactly one audio file, of a format the split reads); only their
    sheets are read, to check the FILE line. A sheet that can't be read doesn't count."""
    audio = sorted(AUDIO_EXTENSIONS)
    marks = ", ".join("?" * len(audio))
    rows = conn.execute(
        f"""
        SELECT parent_id,
               sum(ext = '.cue'),
               sum(ext IN ({marks})),
               max(CASE WHEN ext IN ({marks}) THEN name END)
        FROM entries WHERE is_dir = 0 GROUP BY parent_id
        HAVING sum(ext = '.cue') > 0 AND sum(ext IN ({marks})) = 1
        """, (*audio, *audio, *audio)).fetchall()
    found = []
    for folder_id, _cues, _audio, image in rows:
        if cancelled is not None and cancelled():
            break
        if Path(image).suffix.lower() not in cue.SUPPORTED:
            continue
        folder = path_of(conn, folder_id)
        sheets = [name for (name,) in conn.execute(
            "SELECT name FROM entries WHERE parent_id = ? AND is_dir = 0 AND ext = '.cue'", (folder_id,))]
        if any(_plays(folder / sheet, image) for sheet in sheets):
            found.append(folder_id)
    return found


def _plays(sheet_path: Path, image: str) -> bool:
    """The sheet's FILE lines all name `image` (ignoring case, as Windows does)."""
    try:
        sheet = cue.read_cue_sheet(sheet_path)
    except (OSError, cue.CueError):
        return False
    names = {name.casefold() for name in sheet.files}
    return names == {image.casefold()}
