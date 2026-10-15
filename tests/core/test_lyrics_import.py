"""core/index/lyrics_import.py: seek_lyrics' lyrics_cache.json imported into the index (SPEC §9).

The cache files are written by the frozen script itself (scan, cache_key, save_cache), so the keys and the file
format are the script's, not a re-creation."""

import os
import shutil
from datetime import date

import pytest

from music_desk.core.index.lyrics_import import CACHE_NAME, better, import_cache, merge_cache
from music_desk.core.index.lyrics_index import pending_tracks, record_lyrics, retry_before
from music_desk.core.index.roots import add_root
from music_desk.core.index.scanner import Scanner
from music_desk.core.index.store import DbWriter, connect_read, path_of, read_lyrics
from music_desk.core.lyrics import FOUND, INSTRUMENTAL, NOT_FOUND, cache_key

ALBUM = "Fixture Artist - Fixture Album"
FIRST = cache_key("Fixture Artist", "First")


def found(checked="2026-09-01", plain="la la", synced=None, lrclib_id=7):
    return {"status": FOUND, "source": "lrclib", "lrclib_id": lrclib_id, "plain": plain, "synced": synced,
            "checked": checked}


def not_found(checked="2026-09-01"):
    return {"status": NOT_FOUND, "checked": checked}


@pytest.fixture
def library(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "album" / ALBUM, lib / ALBUM)
    return lib


@pytest.fixture
def script_cache(reference_seek_lyrics, library):
    """A lyrics_cache.json as the script writes it, for the songs it finds in the library: First and Second
    found (Second synced), Bonus instrumental, the rest not found."""
    songs, _errors = reference_seek_lyrics.scan(library)
    cache = {}
    for song in songs:
        key = reference_seek_lyrics.cache_key(song)
        if song["title"] == "First":
            cache[key] = found()
        elif song["title"] == "Second":
            cache[key] = found(synced="[00:01.00]la la", lrclib_id=8)
        elif song["title"] == "Bonus":
            cache[key] = {"status": INSTRUMENTAL, "lrclib_id": 9, "checked": "2026-09-01"}
        else:
            cache[key] = not_found()
    path = library / CACHE_NAME
    reference_seek_lyrics.save_cache(path, cache)
    return cache


@pytest.fixture
def writer(tmp_path):
    with DbWriter(tmp_path / "db" / "library.db") as writer:
        yield writer


@pytest.fixture
def read(writer):
    conn = connect_read(writer.path)
    yield conn
    conn.close()


def index(writer, library) -> None:
    Scanner(writer).scan_root(add_root(writer, library).root_id)


def found_in(read, library, relative="") -> int:
    for (i,) in read.execute("SELECT id FROM entries WHERE is_dir = 1"):
        if path_of(read, i) == library / relative:
            return read.execute("SELECT n_lrclib_found FROM folder_stats WHERE entry_id = ?", (i,)).fetchone()[0]
    raise KeyError(relative)


def test_the_scripts_cache_is_imported_and_its_keys_match_the_index(writer, read, library, script_cache):
    before = (library / CACHE_NAME).read_bytes(), os.stat(library / CACHE_NAME).st_mtime_ns
    report = import_cache(writer, library)
    assert (report.imported, report.kept, report.invalid, report.error) == (len(script_cache), 0, 0, None)
    assert ((library / CACHE_NAME).read_bytes(), os.stat(library / CACHE_NAME).st_mtime_ns) == before  # untouched
    assert read_lyrics(read, FIRST) == found()
    index(writer, library)  # the scan's folder totals count the imported results
    # every track the index keys is covered by the script's entries: nothing left to look up
    assert pending_tracks(read, retry_before(date(2026, 10, 1))) == []
    assert found_in(read, library, ALBUM) == 5  # First ×3, Second ×2


def test_importing_after_the_scan_follows_the_totals(writer, read, library, script_cache):
    index(writer, library)
    assert found_in(read, library, ALBUM) == 0
    import_cache(writer, library)
    assert found_in(read, library, ALBUM) == 5
    assert found_in(read, library, f"{ALBUM}/lite") == 2


def test_no_cache_file(writer, library):
    assert import_cache(writer, library) is None


@pytest.mark.parametrize("content", [b"{not json", b"[1, 2]", b"\xff\xfe"])
def test_an_unreadable_cache_is_reported(writer, library, content):
    (library / CACHE_NAME).write_bytes(content)
    report = import_cache(writer, library)
    assert report.error and report.imported == 0
    assert report.message("X").startswith(f"{CACHE_NAME} in X couldn't be imported:")


def test_entries_the_script_never_wrote_are_skipped(writer, read):
    cache = {
        FIRST: {"status": "error", "error": "URLError", "checked": "2026-09-01"},  # errors were never cached
        "a\x1fb": {"status": FOUND, "checked": "2026-09-01"},  # found without lyrics
        "c\x1fd": {"status": NOT_FOUND, "checked": "yesterday"},
        "e\x1ff": "not an entry",
        "no separator": not_found(),
        "g\x1fh": {"status": INSTRUMENTAL, "lrclib_id": "7", "checked": "2026-09-01"},
        "Big\x1fCase": not_found(),
    }
    assert writer.submit(lambda conn: merge_cache(conn, cache)).result() == (1, 0, 6)
    assert read_lyrics(read, "big\x1fcase") == not_found()  # keys are casefolded, as cache_key does
    assert read_lyrics(read, FIRST) is None


@pytest.mark.parametrize(("new", "old", "replaces"), [
    (found(), None, True),
    (found(checked="2026-01-01"), not_found(checked="2026-10-01"), True),  # lyrics beat a newer not_found
    (not_found(checked="2026-10-01"), found(checked="2026-01-01"), False),
    (found(checked="2026-10-01"), found(checked="2026-09-01"), True),  # the newer result
    (found(checked="2026-09-01"), found(checked="2026-10-01"), False),
    (found(checked="2026-09-01", plain="other"), found(checked="2026-09-01"), False),  # a tie keeps the index's
    (not_found(checked="2026-10-01"), not_found(checked="2026-09-01"), True),
    ({"status": INSTRUMENTAL, "lrclib_id": 1, "checked": "2026-01-01"}, not_found(checked="2026-10-01"), True),
])
def test_merge_rule(new, old, replaces):
    assert better(new, old) is replaces


def test_merge_keeps_better_entries_already_in_the_index(writer, read):
    writer.submit(lambda conn: record_lyrics(conn, FIRST, found(checked="2026-10-01", plain="newer"))).result()
    cache = {FIRST: not_found(checked="2026-10-10"), "x\x1fy": found()}
    assert writer.submit(lambda conn: merge_cache(conn, cache)).result() == (1, 1, 0)
    assert read_lyrics(read, FIRST)["plain"] == "newer"
