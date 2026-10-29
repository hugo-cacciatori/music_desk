"""Importing seek_lyrics' `lyrics_cache.json` into the index's lyrics cache (SPEC §9). Plain Python, no Qt.

The script kept its LRCLIB results in that file at the top of the folder it scanned: key = casefold(artist +
"\\x1f" + title), the same key as the app's (core.lyrics.cache_key over the same artist and title fallbacks), and
entries of the same shape (found, instrumental, not_found; errors were never cached). Importing them spares those
lookups. The file is only read, never changed or removed.

Merge rule, per key: a result with lyrics or "instrumental" beats a not_found, whatever the dates; between two of
the same kind, the newer `checked` date wins, and the index's entry wins a tie. So an import never loses a better
or newer result the app already has.
"""

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from refrain.core.fsops import open_shared
from refrain.core.index.lyrics_index import record_lyrics
from refrain.core.index.store import DbWriter, read_lyrics
from refrain.core.lyrics import FOUND, INSTRUMENTAL, NOT_FOUND

log = logging.getLogger(__name__)

CACHE_NAME = "lyrics_cache.json"  # seek_lyrics.CACHE_NAME
MAX_BYTES = 256 * 1024 * 1024  # far above any real cache; a bigger file isn't the script's


@dataclass(frozen=True)
class ImportReport:
    path: Path
    imported: int = 0  # entries written into the index
    kept: int = 0  # entries the index already had as good or better
    invalid: int = 0  # entries that aren't the script's shape (skipped)
    error: str | None = None  # the file couldn't be read at all

    def message(self, folder: str) -> str:
        """The status bar's line about this import."""
        if self.error:
            return f"{CACHE_NAME} in {folder} couldn't be imported: {self.error}"
        if self.imported:
            return f"Imported {self.imported:,} lyrics lookups from {CACHE_NAME} in {folder}."
        return f"{CACHE_NAME} in {folder} had nothing newer than the library's lyrics."


def _clean(entry) -> dict | None:
    """The entry in the index's shape, or None when it isn't a cache entry the script would have written."""
    if not isinstance(entry, dict):
        return None
    status, checked = entry.get("status"), entry.get("checked")
    if not isinstance(checked, str):
        return None
    try:
        date.fromisoformat(checked)
    except ValueError:
        return None
    lrclib_id = entry.get("lrclib_id")
    if lrclib_id is not None and not isinstance(lrclib_id, int):
        return None
    if status == NOT_FOUND:
        return {"status": NOT_FOUND, "checked": checked}
    if status == INSTRUMENTAL:
        return {"status": INSTRUMENTAL, "lrclib_id": lrclib_id, "checked": checked}
    if status == FOUND:
        plain, synced = entry.get("plain"), entry.get("synced")
        if not all(v is None or isinstance(v, str) for v in (plain, synced)) or not (plain or synced):
            return None
        return {"status": FOUND, "source": entry.get("source") or "lrclib", "lrclib_id": lrclib_id,
                "plain": plain, "synced": synced, "checked": checked}
    return None  # errors and anything else were never cached by the script


def _rank(entry: dict) -> int:
    return 0 if entry["status"] == NOT_FOUND else 1


def better(new: dict, old: dict | None) -> bool:
    """Whether an imported entry should replace the index's (the merge rule above)."""
    if old is None:
        return True
    if _rank(new) != _rank(old):
        return _rank(new) > _rank(old)
    return str(new["checked"]) > str(old.get("checked") or "")


def read_cache(path: Path) -> dict:
    """The file's entries by key. Raises OSError or ValueError for a missing, unreadable or malformed file."""
    with open_shared(path) as f:
        data = f.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("the file is too large to be a lyrics cache")
    cache = json.loads(data.decode("utf-8-sig"))
    if not isinstance(cache, dict):
        raise ValueError("not a lyrics cache (expected a JSON object)")
    return cache


def merge_cache(conn: sqlite3.Connection, cache: dict) -> tuple[int, int, int]:
    """Write the entries that win the merge (a writer job). Returns (imported, kept, invalid)."""
    imported = kept = invalid = 0
    for raw_key, raw_entry in cache.items():
        entry = _clean(raw_entry)
        if entry is None or not isinstance(raw_key, str) or "\x1f" not in raw_key:
            invalid += 1
            continue
        key = raw_key.casefold()
        if better(entry, read_lyrics(conn, key)):
            record_lyrics(conn, key, entry)  # the folder totals follow for tracks already indexed
            imported += 1
        else:
            kept += 1
    return imported, kept, invalid


def import_cache(writer: DbWriter, folder: str | Path) -> ImportReport | None:
    """Import `lyrics_cache.json` from the top of a library folder, if there is one (blocks: file system and
    writer; the app calls it off the UI thread). None when the folder has no cache file."""
    path = Path(folder) / CACHE_NAME
    if not path.is_file():
        return None
    try:
        cache = read_cache(path)
    except (OSError, ValueError) as e:
        log.warning("could not import %s: %s", path, e)
        return ImportReport(path, error=str(e))
    imported, kept, invalid = writer.submit(lambda conn: merge_cache(conn, cache)).result()
    log.info("imported %s: %d entries, %d kept, %d invalid", path, imported, kept, invalid)
    return ImportReport(path, imported, kept, invalid)
