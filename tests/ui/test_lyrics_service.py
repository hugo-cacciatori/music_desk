"""The lyrics service: priority, deduplication, the circuit breaker, offline mode, resuming across restarts, and its
wiring into the library and the window (SPEC §9). LRCLIB is scripted; no test reaches the network."""

import shutil
import time
from datetime import date

import pytest
from lrclib_fake import ScriptedLrclib as Lrclib
from lrclib_fake import SleepRecorder, collect, found_for, no_jitter, unreachable

from music_desk.core.index.lyrics_index import pending_tracks, retry_before
from music_desk.core.index.roots import add_root
from music_desk.core.index.scanner import Scanner
from music_desk.core.index.store import DbWriter, connect_read, path_of
from music_desk.core.lyrics import FOUND, LrclibClient, LyricsOptions, cache_key
from music_desk.services.library import LibraryService
from music_desk.services.lyrics_service import IDLE, OFFLINE, LyricsService
from music_desk.ui.main_window import MainWindow

TODAY = date(2026, 10, 15)
ALBUM = "Fixture Artist - Fixture Album"
# the album fixture's keys: "First" (3 tracks) and "Second" (2 tracks) are shared
TITLES = {"First", "Second", "Disc Two", "Five: A/B?", "Bonus", "Fourth", "missing_title", "Loose", "Sixth"}


@pytest.fixture
def library(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "album" / ALBUM, lib / "Fixture Artist" / ALBUM)
    return lib


@pytest.fixture
def writer(tmp_path, library):
    with DbWriter(tmp_path / "db" / "library.db") as writer:
        Scanner(writer).scan_root(add_root(writer, library).root_id)
        yield writer


@pytest.fixture
def read(writer):
    conn = connect_read(writer.path)
    yield conn
    conn.close()


@pytest.fixture
def make(writer):
    services = []

    def make(lrclib: Lrclib, options: LyricsOptions | None = None, **kwargs) -> LyricsService:
        client = LrclibClient(options, urlopen=lrclib, sleep=SleepRecorder(), jitter=no_jitter)
        service = LyricsService(writer.path, writer, client=client, today=lambda: TODAY, **kwargs)
        services.append(service)
        return service

    yield make
    for service in services:
        service.stop()


def pending(read) -> list:
    return pending_tracks(read, retry_before(TODAY))


def folder(read, library, relative: str) -> int:
    for (i,) in read.execute("SELECT id FROM entries WHERE is_dir = 1"):
        if path_of(read, i) == library / relative:
            return i
    raise KeyError(relative)


def entry(read, library, relative: str) -> int:
    for (i,) in read.execute("SELECT id FROM entries WHERE is_dir = 0"):
        if path_of(read, i) == library / relative:
            return i
    raise KeyError(relative)


def lyrics_row(read, title: str):
    return read.execute("SELECT status FROM lyrics WHERE key = ?", (cache_key("Fixture Artist", title),)).fetchone()


def test_background_looks_every_key_up_once(qtbot, make, read):
    lrclib = Lrclib()
    service = make(lrclib)
    changed = collect(service.foldersChanged)
    service.start()
    qtbot.waitUntil(lambda: bool(changed), timeout=5000)
    qtbot.waitUntil(lambda: not pending(read) and service.state == IDLE, timeout=10000)
    assert set(lrclib.titles()) == TITLES
    assert all(lrclib.gets(title) == 1 for title in TITLES)  # shared keys looked up once
    assert lyrics_row(read, "First")[0] == FOUND
    assert read.execute("SELECT count(*) FROM lyrics").fetchone()[0] == len(TITLES)


def test_nothing_happens_before_start(qtbot, make, read):
    lrclib = Lrclib()
    make(lrclib)
    qtbot.wait(300)
    assert lrclib.requests == []


def test_selected_track_then_open_folder_then_the_rest(qtbot, make, read, library):
    lrclib = Lrclib()
    service = make(lrclib, workers=1)
    service.pause()
    service.prioritize_folder(folder(read, library, f"Fixture Artist/{ALBUM}/lite"))
    service.prioritize_track(entry(read, library, f"Fixture Artist/{ALBUM}/track6.FLAC"))
    qtbot.waitUntil(lambda: not service._requests and len(service._tiers[0]) == 1 and len(service._tiers[1]) == 2)
    service.start()
    service.resume()
    qtbot.waitUntil(lambda: not pending(read) and service.state == IDLE, timeout=10000)
    titles = lrclib.titles()
    assert titles[0] == "Sixth"
    assert set(titles[1:3]) == {"First", "Second"}  # lite/
    assert set(titles) == TITLES


def test_a_folder_request_replaces_the_previous_folder(qtbot, make, read, library):
    lrclib = Lrclib()
    service = make(lrclib, workers=1)
    service.pause()
    service.prioritize_folder(folder(read, library, f"Fixture Artist/{ALBUM}/lite"))
    service.prioritize_folder(folder(read, library, f"Fixture Artist/{ALBUM}/bonus"))
    qtbot.waitUntil(lambda: not service._requests)
    assert [item.track.title for item in service._tiers[1]] == ["Bonus"]


def test_offline_makes_no_call(qtbot, make, read, library):
    lrclib = Lrclib()
    service = make(lrclib, offline=True)
    service.start()
    service.prioritize_track(entry(read, library, f"Fixture Artist/{ALBUM}/track6.FLAC"))
    qtbot.wait(300)
    assert lrclib.requests == []
    assert service.state == OFFLINE
    assert read.execute("SELECT count(*) FROM lyrics").fetchone()[0] == 0
    service.set_offline(False)
    qtbot.waitUntil(lambda: not pending(read), timeout=10000)
    assert set(lrclib.titles()) == TITLES


def test_errors_are_not_cached(qtbot, make, read):
    lrclib = Lrclib(unreachable)
    service = make(lrclib, LyricsOptions(max_attempts=1), workers=1)
    ready = collect(service.entryReady)
    service.start()
    qtbot.waitUntil(lambda: bool(ready), timeout=5000)
    assert ready[0][1]["status"] == "error"
    assert read.execute("SELECT count(*) FROM lyrics").fetchone()[0] == 0
    assert len(pending(read)) == len(TITLES)


def test_the_circuit_breaker_pauses_and_retry_now_resumes(qtbot, make, read):
    lrclib = Lrclib(unreachable)
    service = make(lrclib, LyricsOptions(max_attempts=1, max_consecutive_failures=3), workers=1, pause_seconds=600)
    tripped = collect(service.unreachable)
    service.start()
    qtbot.waitUntil(lambda: bool(tripped), timeout=5000)
    assert tripped[0][0] > time.time() + 500
    asked = len(lrclib.requests)
    qtbot.wait(300)
    assert len(lrclib.requests) == asked  # paused: no more calls
    assert service.state == "unreachable"
    lrclib.answer = found_for({"First"})
    resumed = collect(service.resumed)
    service.retry_now()
    qtbot.waitUntil(lambda: bool(resumed), timeout=2000)
    qtbot.waitUntil(lambda: not pending(read), timeout=10000)  # the keys that failed are tried again too
    assert lyrics_row(read, "First")[0] == FOUND


def test_the_circuit_breaker_resumes_by_itself(qtbot, make, read):
    lrclib = Lrclib(unreachable)
    service = make(lrclib, LyricsOptions(max_attempts=1, max_consecutive_failures=3), workers=2, pause_seconds=0.5)
    tripped, resumed = collect(service.unreachable), collect(service.resumed)
    service.start()
    qtbot.waitUntil(lambda: bool(tripped), timeout=5000)
    lrclib.answer = found_for(set())
    qtbot.waitUntil(lambda: bool(resumed), timeout=5000)
    qtbot.waitUntil(lambda: not pending(read), timeout=10000)


def test_a_restart_resumes_with_the_pending_tracks(qtbot, make, read):
    """Errors stay pending; a new service (the next app start) looks up only what has no entry."""
    first = Lrclib(lambda endpoint, params: found_for({"First"})(endpoint, params)
                   if params["track_name"] in {"First", "Second", "Bonus"} else unreachable(endpoint, params))
    service = make(first, LyricsOptions(max_attempts=1), workers=2)
    service.start()
    qtbot.waitUntil(lambda: len(pending(read)) == len(TITLES) - 3 and service.state == IDLE, timeout=10000)
    service.stop()

    again = Lrclib()
    service = make(again)
    service.start()
    qtbot.waitUntil(lambda: not pending(read), timeout=10000)
    assert set(again.titles()) == TITLES - {"First", "Second", "Bonus"}


def test_folders_changed_names_the_folders_of_the_key(qtbot, make, read, library):
    service = make(Lrclib(), workers=1)
    changed = collect(service.foldersChanged)
    service.prioritize_track(entry(read, library, f"Fixture Artist/{ALBUM}/lite/01 First.flac"))
    qtbot.waitUntil(lambda: bool(changed), timeout=5000)
    assert changed[0][0] == sorted({folder(read, library, f"Fixture Artist/{ALBUM}"),
                                 folder(read, library, f"Fixture Artist/{ALBUM}/lite")})
    stats = read.execute("SELECT n_lrclib_found FROM folder_stats WHERE entry_id = ?",
                         (folder(read, library, f"Fixture Artist/{ALBUM}"),)).fetchone()[0]
    assert stats == 3


# -- wiring --

def test_the_library_starts_lookups_once_indexed(qtbot, tmp_path, library):
    lrclib = Lrclib()
    client = LrclibClient(urlopen=lrclib, sleep=SleepRecorder(), jitter=no_jitter)
    service = LibraryService(tmp_path / "db2" / "library.db", lyrics_client=client, lyrics_offline=False)
    try:
        with qtbot.waitSignal(service.addFinished, timeout=5000):
            service.add_folders([library])
        qtbot.waitUntil(lambda: set(lrclib.titles()) == TITLES, timeout=20000)
    finally:
        service.close()


def test_ui_test_libraries_are_offline(service):
    assert service.lyrics.state == OFFLINE


def test_the_window_prioritizes_and_offers_retry(qtbot, service, library_dir, monkeypatch):
    calls = []
    monkeypatch.setattr(service.lyrics, "prioritize_folder", lambda i: calls.append(("folder", i)))
    monkeypatch.setattr(service.lyrics, "prioritize_track", lambda i: calls.append(("track", i)))
    monkeypatch.setattr(service.lyrics, "retry_now", lambda: calls.append(("retry",)))
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    album = folder(service.conn, library_dir, "Artist/Album")
    window.open_folder(album)
    assert ("folder", album) in calls
    track = entry(service.conn, library_dir, "Artist/Album/Track 2.flac")
    window.file_view.select_ids([track])
    qtbot.waitUntil(lambda: ("track", track) in calls)

    window.lrclib_unreachable(time.time() + 600)
    assert window.toast.label.text() == "LRCLIB unreachable, retrying in 10 min."
    window.toast.button.click()
    assert ("retry",) in calls


def test_adding_a_root_imports_its_lyrics_cache(qtbot, tmp_path, library, reference_seek_lyrics):
    songs, _errors = reference_seek_lyrics.scan(library)
    cache = {reference_seek_lyrics.cache_key(s): {"status": "not_found", "checked": "2026-10-01"} for s in songs}
    reference_seek_lyrics.save_cache(library / "lyrics_cache.json", cache)
    service = LibraryService(tmp_path / "db3" / "library.db", lyrics_offline=True)
    try:
        notices = collect(service.notice)
        with qtbot.waitSignal(service.addFinished, timeout=5000):
            service.add_folders([library])
        qtbot.waitUntil(lambda: bool(notices), timeout=5000)
        assert notices[0][0] == f"Imported {len(cache)} lyrics lookups from lyrics_cache.json in {library}."
        assert service.conn.execute("SELECT count(*) FROM lyrics").fetchone()[0] == len(cache)
    finally:
        service.close()
