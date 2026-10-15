"""core/index/scanner.py: phase A structure, phase B tags, incremental rescans, stats, priority, cancel (SPEC §4)."""

import os
import shutil
import time
from datetime import datetime

import pytest

from music_desk.core.cancel import CancelToken
from music_desk.core.index.roots import add_root, list_roots
from music_desk.core.index.scanner import Scanner, read_file
from music_desk.core.index.store import DbWriter, connect_read, path_of
from music_desk.core.lyrics import cache_key

NOW = datetime(2026, 10, 15, 12, 0, 0)
ALBUM = "Fixture Artist - Fixture Album"


@pytest.fixture
def library(tmp_path, fixtures_dir):
    """A copied library: the album fixture, the cleanup set and a few formats (never a real library)."""
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "album" / ALBUM, lib / "Fixture Artist" / ALBUM)
    shutil.copytree(fixtures_dir / "cleanup" / "lrc", lib / "Fixture Artist" / "Lrc")
    (lib / "Formats").mkdir()
    for name in ("tone.flac", "tone.mp3", "tone_aac.m4a", "tone.aac"):
        shutil.copy2(fixtures_dir / "formats" / name, lib / "Formats" / name)
    (lib / "Formats" / "notes.txt").write_text("not audio")
    (lib / "Empty").mkdir()
    return lib


@pytest.fixture
def writer(tmp_path):
    with DbWriter(tmp_path / "db" / "library.db") as writer:
        yield writer


@pytest.fixture
def read(writer):
    conn = connect_read(writer.path)
    yield conn
    conn.close()


@pytest.fixture
def root_id(writer, library):
    return add_root(writer, library).root_id


def scanner(writer, **kwargs):
    return Scanner(writer, now=lambda: NOW, **kwargs)


def indexed_paths(read, library) -> set[str]:
    ids = [row[0] for row in read.execute("SELECT id FROM entries WHERE parent_id IS NOT NULL")]
    return {path_of(read, i).relative_to(library).as_posix() for i in ids}


def disk_paths(library) -> set[str]:
    return {p.relative_to(library).as_posix() for p in library.rglob("*")}


def entry_id(read, library, relative: str) -> int:
    for (i,) in read.execute("SELECT id FROM entries WHERE parent_id IS NOT NULL"):
        if path_of(read, i) == library / relative:
            return i
    raise KeyError(relative)


def track(read, library, relative: str):
    return read.execute("SELECT * FROM tracks WHERE entry_id = ?", (entry_id(read, library, relative),)).fetchone()


# -- full scans --

def test_first_scan_indexes_everything(writer, read, library, root_id):
    report = scanner(writer).scan_root(root_id)
    assert indexed_paths(read, library) == disk_paths(library)
    audio = [p for p in library.rglob("*") if p.suffix.lower() in {".flac", ".mp3", ".m4a", ".aac"}]
    assert report.tags_read == len(audio) and report.tag_errors == 0
    assert report.added == len(disk_paths(library)) and report.removed == report.updated == 0
    assert read.execute("SELECT count(*) FROM tracks").fetchone()[0] == len(audio)
    assert list_roots(read)[0].last_scan == "2026-10-15T12:00:00"


def test_entry_columns(writer, read, library, root_id):
    scanner(writer).scan_root(root_id)
    notes = read.execute("SELECT is_dir, ext, size FROM entries WHERE id = ?",
                         (entry_id(read, library, "Formats/notes.txt"),)).fetchone()
    assert tuple(notes) == (0, ".txt", 9)
    upper = read.execute("SELECT ext FROM entries WHERE id = ?",
                         (entry_id(read, library, f"Fixture Artist/{ALBUM}/track6.FLAC"),)).fetchone()
    assert upper[0] == ".flac"


def test_track_rows(writer, read, library, root_id):
    scanner(writer).scan_root(root_id)
    row = track(read, library, "Formats/tone.flac")
    assert (row["format"], row["tag_family"], row["title"], row["artist"], row["album"], row["track"],
            row["date"], row["genre"]) == ("FLAC", "Vorbis", "Tone tone.flac", "Fixture Artist", "Fixture Formats",
                                           1, "2024", "Test")
    assert 0.5 < row["duration"] < 1.5 and row["sample_rate"] and row["channels"]
    assert row["lyrics_key"] == cache_key("Fixture Artist", "Tone tone.flac")
    assert row["read_error"] is None and row["read_size"] == (library / "Formats/tone.flac").stat().st_size

    lyrics = track(read, library, "Fixture Artist/Lrc/with_lyrics.flac")
    assert lyrics["has_lyrics_tag"] == 1
    raw = track(read, library, "Formats/tone.aac")
    assert (raw["tag_family"], raw["title"], raw["read_error"]) == ("none", None, None)
    assert raw["lyrics_key"] is None  # no artist in the tags or the file name


def test_replaygain_and_cover_columns(writer, read, tmp_path, fixtures_dir):
    lib = tmp_path / "rg"
    shutil.copytree(fixtures_dir / "edge", lib)
    shutil.copytree(fixtures_dir / "album" / ALBUM, lib / "album")
    root = add_root(writer, lib).root_id
    scanner(writer).scan_root(root)
    assert track(read, lib, "edge.flac")["has_cover"] == 1
    rg = track(read, lib, "album/lowercase_rg.mp3")
    assert rg["rg_track_gain"] is not None


def test_sidecar_flags(writer, read, library, root_id):
    scanner(writer).scan_root(root_id)
    flags = {name: track(read, library, f"Fixture Artist/Lrc/{name}")["has_lrc_sidecar"]
             for name in ("with_lyrics.flac", "no_lyrics.flac")}
    lrc_stems = {p.stem for p in (library / "Fixture Artist/Lrc").glob("*.lrc")}
    assert flags == {name: int(name.removesuffix(".flac") in lrc_stems) for name in flags}


def test_broken_audio_file_is_indexed_with_its_error(writer, read, library, root_id):
    (library / "Formats" / "broken.flac").write_bytes(b"not a flac")
    report = scanner(writer).scan_root(root_id)
    assert report.tag_errors == 1
    row = track(read, library, "Formats/broken.flac")
    assert row["read_error"] and row["title"] is None and row["format"] == "FLAC"


def test_search_index(writer, read, library, root_id):
    scanner(writer).scan_root(root_id)

    def hits(query):
        return {path_of(read, r[0]).name for r in read.execute("SELECT rowid FROM search WHERE search MATCH ?",
                                                                (query,))}
    assert hits('"tone.flac"') >= {"tone.flac"}
    assert hits('title:"Tone tone.mp3"') == {"tone.mp3"}
    assert "notes.txt" in hits("notes")
    assert "with_lyrics.flac" in hits('lyrics:"Fixture"')


def test_folder_stats_count_the_subtree(writer, read, library, root_id):
    scanner(writer).scan_root(root_id)
    top = list_roots(read)[0].top_entry_id

    def stats(folder_id):
        return read.execute("SELECT n_tracks, n_with_rg, n_with_lyrics_tag, formats, duration FROM folder_stats "
                            "WHERE entry_id = ?", (folder_id,)).fetchone()

    formats = stats(entry_id(read, library, "Formats"))
    assert tuple(formats)[:4] == (4, 0, 0, "AAC, FLAC, M4A, MP3") and formats[4] > 3
    album_tracks = len([p for p in (library / "Fixture Artist" / ALBUM).rglob("*")
                        if p.suffix.lower() in {".flac", ".mp3"}])
    assert stats(entry_id(read, library, f"Fixture Artist/{ALBUM}"))[0] == album_tracks
    total = read.execute("SELECT count(*) FROM tracks").fetchone()[0]
    assert stats(top)[0] == total
    assert tuple(stats(entry_id(read, library, "Empty"))) == (0, 0, 0, None, 0)


# -- incremental --

def test_rescan_without_changes_writes_nothing(writer, read, library, root_id):
    scanner(writer).scan_root(root_id)
    report = scanner(writer).scan_root(root_id)
    assert (report.added, report.updated, report.removed, report.tags_read) == (0, 0, 0, 0)


def test_rescan_picks_up_changes(writer, read, library, root_id, fixtures_dir):
    scanner(writer).scan_root(root_id)
    old_flac = entry_id(read, library, "Formats/tone.flac")

    shutil.copy2(fixtures_dir / "formats" / "tone.opus", library / "Formats" / "new.opus")  # added
    (library / "Formats" / "tone.mp3").unlink()  # removed
    shutil.rmtree(library / "Fixture Artist" / "Lrc")  # a whole folder removed
    os.rename(library / "Empty", library / "Renamed")  # renamed outside the app
    flac = library / "Formats" / "tone.flac"  # changed
    flac.write_bytes(flac.read_bytes())
    os.utime(flac, (time.time() + 60, time.time() + 60))

    report = scanner(writer).scan_root(root_id)
    assert indexed_paths(read, library) == disk_paths(library)
    assert report.tags_read == 2  # new.opus and the changed tone.flac
    assert entry_id(read, library, "Formats/tone.flac") == old_flac  # updated in place, id kept
    assert track(read, library, "Formats/new.opus")["format"] == "OPUS"
    orphans = read.execute("SELECT count(*) FROM tracks t LEFT JOIN entries e ON e.id = t.entry_id "
                           "WHERE e.id IS NULL").fetchone()[0]
    assert orphans == 0
    assert read.execute("SELECT count(*) FROM search").fetchone()[0] == len(disk_paths(library))


def test_case_only_rename_updates_the_name(writer, read, library, root_id):
    scanner(writer).scan_root(root_id)
    old = entry_id(read, library, "Formats/notes.txt")
    os.rename(library / "Formats" / "notes.txt", library / "Formats" / "NOTES.txt")
    scanner(writer).scan_root(root_id)
    assert entry_id(read, library, "Formats/NOTES.txt") == old


def test_file_replaced_by_a_folder(writer, read, library, root_id):
    scanner(writer).scan_root(root_id)
    (library / "Formats" / "notes.txt").unlink()
    (library / "Formats" / "notes.txt").mkdir()
    (library / "Formats" / "notes.txt" / "inner.txt").write_text("x")
    scanner(writer).scan_root(root_id)
    assert indexed_paths(read, library) == disk_paths(library)


def test_offline_root_keeps_its_index(writer, read, library, root_id, tmp_path):
    scanner(writer).scan_root(root_id)
    before = indexed_paths(read, library)
    shutil.move(library, tmp_path / "unplugged")
    report = scanner(writer).scan_root(root_id)
    assert report.offline
    assert indexed_paths(read, library) == before


def test_unlistable_folder_keeps_its_index(writer, read, library, root_id, monkeypatch):
    from music_desk.core.index import scanner as scanner_module

    scanner(writer).scan_root(root_id)
    before = indexed_paths(read, library)
    real = scanner_module.list_folder

    def flaky(path):
        if path.endswith("Formats"):
            raise PermissionError("access denied")
        return real(path)

    monkeypatch.setattr(scanner_module, "list_folder", flaky)
    (library / "Formats" / "tone.flac").unlink()
    report = scanner(writer).scan_root(root_id)
    assert report.unreadable_folders == 1
    assert indexed_paths(read, library) == before  # tone.flac is still indexed: Formats couldn't be checked


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks")
def test_links_are_not_followed(writer, read, library, root_id):
    try:
        os.symlink(library, library / "loop", target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks needs Developer Mode or admin rights")
    scanner(writer).scan_root(root_id)
    assert "loop" not in indexed_paths(read, library)


# -- folders, priority, cancel, progress --

def test_scan_folder_reconciles_one_folder(writer, read, library, root_id, fixtures_dir):
    scanner(writer).scan_root(root_id)
    shutil.copy2(fixtures_dir / "formats" / "tone.ogg", library / "Formats" / "added.ogg")
    (library / "Formats" / "sub").mkdir()
    (library / "Formats" / "sub" / "deep.txt").write_text("x")
    report = scanner(writer).scan_folder(entry_id(read, library, "Formats"))
    assert report.tags_read == 1
    assert track(read, library, "Formats/added.ogg")["format"] == "OGG"
    assert "Formats/sub" in indexed_paths(read, library)
    assert "Formats/sub/deep.txt" not in indexed_paths(read, library)  # one level only
    assert read.execute("SELECT n_tracks FROM folder_stats WHERE entry_id = ?",
                        (entry_id(read, library, "Formats"),)).fetchone()[0] == 5


def test_priority_folder_is_served_first(writer, read, library, root_id):
    scanner(writer).scan_root(root_id)
    target = entry_id(read, library, f"Fixture Artist/{ALBUM}/lite")
    writer.execute("DELETE FROM tracks").result()  # every tag has to be read again
    served = []
    order = []
    s = scanner(writer, batch_rows=1, tag_batch=1, on_folder=served.append,
                on_progress=lambda p: order.append((p.phase, p.tags_done)))
    s.request_folder(target)
    s.request_folder(target)  # once is enough
    s.scan_root(root_id)
    assert served == [target]
    assert order[0][0] == "structure"
    assert read.execute("SELECT count(*) FROM tracks").fetchone()[0] > 0


def test_priority_during_the_walk_does_not_duplicate(writer, read, library, root_id):
    """A requested folder inserted moments ago is filled once, by whichever comes first."""
    served = []
    s = scanner(writer, batch_rows=1, on_folder=served.append)
    calls = []

    def on_progress(progress):
        if not calls and progress.folders >= 2:
            calls.append(1)
            conn = connect_read(writer.path)
            album = next((i for (i,) in conn.execute("SELECT id FROM entries WHERE name = ?", (ALBUM,))), None)
            conn.close()
            if album:
                s.request_folder(album)

    s.on_progress = on_progress
    s.scan_root(root_id)
    assert len(served) == 1  # the album was inserted, then served before the walk reached it
    assert indexed_paths(read, library) == disk_paths(library)


def test_cancel_stops_without_finishing(writer, read, library, root_id):
    cancel = CancelToken()
    s = scanner(writer, batch_rows=1, on_progress=lambda _p: cancel.cancel())
    report = s.scan_root(root_id, cancel)
    assert report.cancelled
    assert list_roots(read)[0].last_scan is None
    finished = scanner(writer).scan_root(root_id)  # the next scan completes the index
    assert not finished.cancelled
    assert indexed_paths(read, library) == disk_paths(library)


def test_progress_reaches_done(writer, library, root_id):
    phases = []
    scanner(writer, on_progress=lambda p: phases.append(p.phase)).scan_root(root_id)
    assert phases[-1] == "done"
    assert "tags" in phases and phases.index("stats") < phases.index("done")


def test_read_file_never_raises(tmp_path):
    path = tmp_path / "x.mp3"
    path.write_bytes(b"")
    row, lyrics = read_file(path, 0, 1.0)
    assert row["read_error"] and lyrics is None and row["format"] == "MP3"


# -- timing --

def make_tree(base, folders: int, files_per_folder: int, audio_source=None):
    for f in range(folders):
        folder = base / f"Artist {f // 10}" / f"Album {f}"
        folder.mkdir(parents=True)
        for n in range(files_per_folder):
            (folder / f"{n:02d} file.txt").write_bytes(b"")
        if audio_source:
            shutil.copy2(audio_source, folder / "01 track.flac")


def test_ten_thousand_entries(writer, read, tmp_path, fixtures_dir):
    lib = tmp_path / "big"
    make_tree(lib, 500, 19, fixtures_dir / "formats" / "tone.flac")  # 500 folders, 10,000 files, 500 tracks
    root = add_root(writer, lib).root_id
    start = time.perf_counter()
    report = scanner(writer).scan_root(root)
    first = time.perf_counter() - start
    assert report.files == 10_000 and report.tags_read == 500
    start = time.perf_counter()
    scanner(writer).scan_root(root)
    again = time.perf_counter() - start
    print(f"\n10k entries: first scan {first:.2f} s, rescan {again:.2f} s")
    assert first < 60 and again < 30


@pytest.mark.slow
def test_hundred_thousand_entries(writer, tmp_path):
    lib = tmp_path / "huge"
    make_tree(lib, 5000, 20)
    root = add_root(writer, lib).root_id
    start = time.perf_counter()
    report = scanner(writer).scan_root(root)
    elapsed = time.perf_counter() - start
    print(f"\n100k entries: phase A {elapsed:.2f} s")
    assert report.files == 100_000
    assert elapsed < 300


# -- path scans (the watcher's entry point) --

def test_find_folder(writer, read, library, root_id):
    from music_desk.core.index.scanner import find_folder

    scanner(writer).scan_root(root_id)
    album = entry_id(read, library, f"Fixture Artist/{ALBUM}")
    top = list_roots(read)[0].top_entry_id
    assert find_folder(read, library) == (top, True)
    assert find_folder(read, str(library / "fixture artist" / ALBUM.upper())) == (album, True)
    assert find_folder(read, library / "Fixture Artist" / ALBUM / "new" / "deeper") == (album, False)
    assert find_folder(read, library / "Formats" / "tone.flac") == (entry_id(read, library, "Formats"), False)
    assert find_folder(read, library.parent / "elsewhere") is None


def test_scan_path_finds_new_subtrees(writer, read, library, root_id, fixtures_dir):
    scanner(writer).scan_root(root_id)
    new = library / "Fixture Artist" / "New Album" / "CD1"
    new.mkdir(parents=True)
    shutil.copy2(fixtures_dir / "formats" / "tone.flac", new / "01.flac")
    report = scanner(writer).scan_path(new)  # not indexed yet: its nearest ancestor's subtree is scanned
    assert report.folder_id == entry_id(read, library, "Fixture Artist")
    assert indexed_paths(read, library) == disk_paths(library)
    assert track(read, library, "Fixture Artist/New Album/CD1/01.flac")["format"] == "FLAC"
    assert scanner(writer).scan_path(library.parent / "elsewhere") is None


def test_scan_folder_recursive(writer, read, library, root_id):
    scanner(writer).scan_root(root_id)
    (library / "Formats" / "a" / "b").mkdir(parents=True)
    (library / "Formats" / "a" / "b" / "c.txt").write_text("x")
    scanner(writer).scan_folder(entry_id(read, library, "Formats"), recursive=True)
    assert "Formats/a/b/c.txt" in indexed_paths(read, library)
    assert list_roots(read)[0].last_scan == "2026-10-15T12:00:00"  # set by the root scan, not changed since


def test_scan_folder_of_a_vanished_entry(writer, read, library, root_id):
    scanner(writer).scan_root(root_id)
    gone = entry_id(read, library, "Empty")
    writer.execute("DELETE FROM entries WHERE id = ?", (gone,)).result()
    assert scanner(writer).scan_folder(gone) is None


def test_scan_of_a_removed_root(writer):
    assert scanner(writer).scan_root(999) is None


# -- one file after a tag save --

def test_update_file_rereads_one_file(writer, read, library, root_id):
    from music_desk.core.tags import TaggedFile

    scanner(writer).scan_root(root_id)
    path = library / "Formats" / "tone.flac"
    formats = entry_id(read, library, "Formats")
    stats_before = read.execute("SELECT n_tracks, n_with_lyrics_tag FROM folder_stats WHERE entry_id = ?",
                                (formats,)).fetchone()
    top = list_roots(read)[0].top_entry_id
    top_before = read.execute("SELECT n_with_lyrics_tag FROM folder_stats WHERE entry_id = ?", (top,)).fetchone()[0]

    tagged = TaggedFile(path)
    tagged.set_fields({"title": "Retitled", "lyrics": "la la"})
    tagged.save()
    report = scanner(writer).update_file(path)
    assert report.folder_id == formats and report.tags_read == 1

    row = track(read, library, "Formats/tone.flac")
    assert row["title"] == "Retitled" and row["has_lyrics_tag"] == 1
    assert row["read_size"] == path.stat().st_size
    stats = read.execute("SELECT n_tracks, n_with_lyrics_tag FROM folder_stats WHERE entry_id = ?",
                         (formats,)).fetchone()
    assert tuple(stats) == (stats_before[0], stats_before[1] + 1)  # adjusted by the difference only
    assert read.execute("SELECT n_with_lyrics_tag FROM folder_stats WHERE entry_id = ?",
                        (top,)).fetchone()[0] == top_before + 1  # up to the root
    hits = [r[0] for r in read.execute("SELECT rowid FROM search WHERE search MATCH 'Retitled'")]
    assert hits == [entry_id(read, library, "Formats/tone.flac")]
    assert scanner(writer).scan_root(root_id).tags_read == 0  # the index is already up to date


def test_update_file_of_an_unknown_file_scans_its_folder(writer, read, library, root_id, fixtures_dir):
    scanner(writer).scan_root(root_id)
    new = library / "Formats" / "new.opus"
    shutil.copy2(fixtures_dir / "formats" / "tone.opus", new)
    report = scanner(writer).update_file(new)
    assert report.folder_id == entry_id(read, library, "Formats")
    assert track(read, library, "Formats/new.opus")["format"] == "OPUS"
    assert scanner(writer).update_file(library.parent / "elsewhere" / "x.flac") is None
