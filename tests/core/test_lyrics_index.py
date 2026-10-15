"""core/index/lyrics_index.py: pending tracks and recording results with the folder totals (SPEC §9)."""

import shutil
from datetime import date

import pytest

from music_desk.core.index import queries
from music_desk.core.index.lyrics_index import pending_count, pending_tracks, record_lyrics, retry_before
from music_desk.core.index.roots import add_root
from music_desk.core.index.scanner import Scanner
from music_desk.core.index.store import DbWriter, LyricsStore, connect_read, path_of
from music_desk.core.lyrics import FOUND, INSTRUMENTAL, NOT_FOUND, cache_key

TODAY = date(2026, 10, 15)
BEFORE = retry_before(TODAY)
ALBUM = "Fixture Artist - Fixture Album"
FIRST = cache_key("Fixture Artist", "First")  # 01 First.flac, clash.flac and lite/01 First.flac
FOUND_ENTRY = {"status": FOUND, "source": "lrclib", "lrclib_id": 1, "plain": "la", "synced": None,
               "checked": TODAY.isoformat()}


@pytest.fixture
def library(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "album" / ALBUM, lib / "Fixture Artist" / ALBUM)
    (lib / "Loose").mkdir()
    shutil.copy2(fixtures_dir / "formats" / "tone.aac", lib / "Loose" / "Some Band - Song.aac")  # no tags
    shutil.copy2(fixtures_dir / "formats" / "tone.aac", lib / "Loose" / "untitled.aac")  # no artist at all
    (lib / "Loose" / "broken.flac").write_bytes(b"not flac")
    return lib


@pytest.fixture
def writer(tmp_path):
    with DbWriter(tmp_path / "db" / "library.db") as writer:
        yield writer


@pytest.fixture
def read(writer, library):
    Scanner(writer).scan_root(add_root(writer, library).root_id)
    conn = connect_read(writer.path)
    yield conn
    conn.close()


def folder(read, library, relative: str) -> int:
    for (i,) in read.execute("SELECT id FROM entries WHERE is_dir = 1"):
        if path_of(read, i) == library / relative:
            return i
    raise KeyError(relative)


def put(writer, key, entry):
    return writer.submit(lambda conn: record_lyrics(conn, key, entry)).result()


def found_counts(read, library) -> dict[str, int]:
    names = {"": library, "artist": library / "Fixture Artist", "album": library / "Fixture Artist" / ALBUM,
             "lite": library / "Fixture Artist" / ALBUM / "lite"}
    rows = dict(read.execute("SELECT entry_id, n_lrclib_found FROM folder_stats").fetchall())
    ids = {path_of(read, i): i for i in rows}
    return {name: rows[ids[path]] for name, path in names.items()}


def test_every_key_is_pending_once_folder_by_folder(read, library):
    pending = pending_tracks(read, BEFORE)
    keys = [p.key for p in pending]
    assert len(keys) == len(set(keys))
    assert FIRST in keys
    assert cache_key("Some Band", "Song") in keys  # the file-name fallback, as the scanner keyed it
    assert not any("untitled" in k for k in keys)  # no artist: missing_tags, never looked up
    assert all(p.track.artist and p.track.title for p in pending)
    folders = [p.folder_id for p in pending]
    assert folders == sorted(folders)  # folder by folder


def test_the_track_sent_is_rebuilt_from_the_index(read, library):
    song = next(p for p in pending_tracks(read, BEFORE) if p.key == cache_key("Some Band", "Song"))
    assert (song.track.artist, song.track.title, song.track.album) == ("Some Band", "Song", None)
    first = next(p for p in pending_tracks(read, BEFORE) if p.key == FIRST)
    assert (first.track.album, first.track.duration) == ("Fixture Album", 1)


def test_unreadable_files_are_not_pending(read, library):
    broken = read.execute("SELECT entry_id FROM tracks WHERE read_error IS NOT NULL").fetchall()
    assert broken
    ids = {p.entry_id for p in pending_tracks(read, BEFORE)}
    assert not ids & {row[0] for row in broken}


def test_fresh_entries_are_not_pending_and_stale_not_found_are(writer, read, library):
    put(writer, FIRST, {"status": NOT_FOUND, "checked": "2026-10-01"})
    assert FIRST not in {p.key for p in pending_tracks(read, BEFORE)}
    put(writer, FIRST, {"status": NOT_FOUND, "checked": "2026-09-01"})  # older than 30 days
    assert FIRST in {p.key for p in pending_tracks(read, BEFORE)}
    put(writer, FIRST, {"status": INSTRUMENTAL, "lrclib_id": 3, "checked": "2020-01-01"})  # never stale
    assert FIRST not in {p.key for p in pending_tracks(read, BEFORE)}


def test_skip_limit_folder_and_entry(read, library):
    everything = pending_tracks(read, BEFORE)
    assert len(pending_tracks(read, BEFORE, limit=2)) == 2
    assert FIRST not in {p.key for p in pending_tracks(read, BEFORE, skip={FIRST})}
    lite = folder(read, library, f"Fixture Artist/{ALBUM}/lite")
    in_lite = pending_tracks(read, BEFORE, folder_id=lite)
    assert {p.track.title for p in in_lite} == {"First", "Second"}
    one = pending_tracks(read, BEFORE, entry_id=in_lite[0].entry_id)
    assert [p.entry_id for p in one] == [in_lite[0].entry_id]
    assert len(everything) > len(in_lite)


def test_record_lyrics_stores_the_entry_and_follows_the_totals(writer, read, library):
    assert found_counts(read, library) == {"": 0, "artist": 0, "album": 0, "lite": 0}
    folders = put(writer, FIRST, FOUND_ENTRY)
    album = folder(read, library, f"Fixture Artist/{ALBUM}")
    lite = folder(read, library, f"Fixture Artist/{ALBUM}/lite")
    assert folders == sorted({album, lite})
    assert LyricsStore(read, writer).get(FIRST) == FOUND_ENTRY
    # three tracks share the key: two in the album folder, one in lite (subtree totals)
    assert found_counts(read, library) == {"": 3, "artist": 3, "album": 3, "lite": 1}
    put(writer, FIRST, {**FOUND_ENTRY, "plain": "new"})  # still found: no change
    assert found_counts(read, library) == {"": 3, "artist": 3, "album": 3, "lite": 1}
    put(writer, FIRST, {"status": NOT_FOUND, "checked": TODAY.isoformat()})
    assert found_counts(read, library) == {"": 0, "artist": 0, "album": 0, "lite": 0}


def test_record_lyrics_for_a_key_no_track_has(writer, read, library):
    assert put(writer, "nobody\x1fnothing", FOUND_ENTRY) == []
    assert LyricsStore(read, writer).get("nobody\x1fnothing") == FOUND_ENTRY


def test_pending_count_matches_the_pending_tracks(writer, read, library):
    assert pending_count(read, BEFORE) == len(pending_tracks(read, BEFORE))
    put(writer, FIRST, FOUND_ENTRY)
    assert pending_count(read, BEFORE) == len(pending_tracks(read, BEFORE))


def test_the_lrclib_column_states(writer, read, library):
    put(writer, FIRST, FOUND_ENTRY)
    loose = {row.name: row.lrclib for row in queries.folder_listing(read, folder(read, library, "Loose"))}
    assert loose == {"Some Band - Song.aac": "pending", "untitled.aac": "no tags", "broken.flac": ""}
    album_id = folder(read, library, f"Fixture Artist/{ALBUM}")
    album = {row.name: row.lrclib for row in queries.folder_listing(read, album_id)}
    assert album["01 First.flac"] == "plain" and album["track2.flac"] == "pending" and album["lite"] is None
