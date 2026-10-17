"""The Lyrics tab: every state with a scripted LRCLIB, the actions, and its place in the side panel (SPEC §8)."""

import shutil
import threading
import time
from datetime import date

import pytest
from lrclib_fake import ScriptedLrclib, SleepRecorder, collect, found_for, http_error, no_jitter, unreachable
from PySide6.QtWidgets import QApplication

from music_desk.core.index.roots import add_root
from music_desk.core.index.scanner import Scanner
from music_desk.core.index.store import DbWriter, connect_read, path_of
from music_desk.core.lyrics import LrclibClient, LyricsOptions
from music_desk.core.tags import TaggedFile
from music_desk.services.lyrics_service import LyricsService
from music_desk.ui.main_window import MainWindow
from music_desk.ui.side_panel import lyrics_tab as tab_module
from music_desk.ui.side_panel.lyrics_tab import LyricsTab

TODAY = date(2026, 10, 15)
ALBUM = "Fixture Artist - Fixture Album"
SYNCED = "[00:01.00]la la"


def answer(endpoint, params):
    """First: plain lyrics; Sixth: synced; Bonus: instrumental; Tagged/Other: plain "la la"; the rest: not found."""
    title = params["track_name"]
    if endpoint != "get":
        return []
    record = {"id": 42, "artistName": params["artist_name"], "trackName": title, "duration": 1,
              "instrumental": False, "plainLyrics": "la la", "syncedLyrics": None}
    if title in ("First", "Tagged", "Other"):
        return record
    if title == "Sixth":
        return {**record, "syncedLyrics": SYNCED}
    if title == "Bonus":
        return {**record, "instrumental": True, "plainLyrics": None}
    raise http_error(404)


@pytest.fixture
def library(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    album = lib / "Fixture Artist" / ALBUM
    shutil.copytree(fixtures_dir / "album" / ALBUM, album)
    for name, title, lyrics in (("tagged.flac", "Tagged", "la la\r\n"), ("other.flac", "Other", "something else")):
        shutil.copy2(album / "01 First.flac", album / name)
        tagged = TaggedFile(album / name)
        tagged.set_fields({"title": title, "lyrics": lyrics})
        tagged.save()
    (lib / "Loose").mkdir()
    shutil.copy2(fixtures_dir / "formats" / "tone.aac", lib / "Loose" / "untitled.aac")  # no artist at all
    (lib / "Loose" / "broken.flac").write_bytes(b"not flac")
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
def make(qtbot, writer, read):
    services = []

    def make(lrclib=None, options=None, **kwargs):
        lrclib = lrclib or ScriptedLrclib(answer)
        client = LrclibClient(options, urlopen=lrclib, sleep=SleepRecorder(), jitter=no_jitter)
        service = LyricsService(writer.path, writer, client=client, today=lambda: TODAY, **kwargs)
        services.append(service)
        tab = LyricsTab(lambda: read, service)
        tab.today = lambda: TODAY
        qtbot.addWidget(tab)
        tab.show()
        return service, tab

    yield make
    for service in services:
        service.stop()


def entry(read, library, relative: str) -> int:
    for (i,) in read.execute("SELECT id FROM entries WHERE is_dir = 0"):
        if path_of(read, i) == library / relative:
            return i
    raise KeyError(relative)


def track(read, library, name: str) -> int:
    return entry(read, library, f"Fixture Artist/{ALBUM}/{name}")


def select(service, tab, entry_id):
    """What the window does for a single selected track."""
    service.prioritize_track(entry_id)
    tab.show_track(entry_id)


def visible(*widgets) -> list[bool]:
    return [w.isVisible() for w in widgets]


def test_starts_with_a_message(make):
    _service, tab = make()
    assert tab.state == tab_module.MESSAGE
    assert tab.status.text() == "Select a track to see its lyrics from LRCLIB."
    assert visible(tab.text, tab.genius_button, tab.retry_button, tab.spinner) == [False] * 4


def test_looking_up_then_found(qtbot, make, read, library):
    release = threading.Event()

    def slow(endpoint, params):
        release.wait(5)
        return answer(endpoint, params)

    service, tab = make(ScriptedLrclib(slow), workers=1)
    select(service, tab, track(read, library, "01 First.flac"))
    assert tab.state == tab_module.LOOKING_UP
    assert tab.spinner.isVisible() and tab.status.text() == "Looking up on LRCLIB…"
    release.set()
    qtbot.waitUntil(lambda: tab.state == tab_module.FOUND_STATE, timeout=5000)
    assert tab.shown_text() == "la la"
    assert tab.source.text() == f"LRCLIB #42 · checked {TODAY.isoformat()}"
    assert not tab.timestamps.isEnabled()  # plain lyrics only
    assert visible(tab.copy_button, tab.genius_button, tab.retry_button) == [True, False, False]


def test_synced_lyrics_toggle_timestamps_and_copy(qtbot, make, read, library):
    service, tab = make()
    select(service, tab, track(read, library, "track6.FLAC"))
    qtbot.waitUntil(lambda: tab.state == tab_module.FOUND_STATE, timeout=5000)
    assert tab.timestamps.isEnabled() and not tab.timestamps.isChecked()
    assert tab.shown_text() == "la la"
    tab.timestamps.setChecked(True)
    assert tab.shown_text() == SYNCED
    tab.copy()
    assert QApplication.clipboard().text() == SYNCED


def test_a_lyrics_tag_plays_no_part(qtbot, make, read, library):
    service, tab = make()
    select(service, tab, track(read, library, "tagged.flac"))
    qtbot.waitUntil(lambda: tab.state == tab_module.FOUND_STATE, timeout=5000)
    # The file's own lyrics tag plays no part (M12, SPEC §26): no note about it, nothing to save into it.
    assert tab.shown_text() and not hasattr(tab, "note") and not hasattr(tab, "save_button")
    assert "tag" not in tab.status.text()


def test_instrumental(qtbot, make, read, library):
    service, tab = make()
    select(service, tab, track(read, library, "bonus/01 Bonus.flac"))
    qtbot.waitUntil(lambda: tab.state == tab_module.INSTRUMENTAL_STATE, timeout=5000)
    assert tab.status.text() == "LRCLIB lists this track as instrumental."
    assert visible(tab.text, tab.source, tab.genius_button, tab.google_button, tab.retry_button) == \
        [False, True, True, True, False]


def test_not_found_offers_searches_and_retry(qtbot, make, read, library):
    lrclib = ScriptedLrclib(answer)
    service, tab = make(lrclib)
    select(service, tab, track(read, library, "track2.flac"))
    qtbot.waitUntil(lambda: tab.state == tab_module.NOT_FOUND_STATE, timeout=5000)
    assert tab.status.text().startswith(f"LRCLIB has no lyrics for this track (checked {TODAY.isoformat()}).")
    assert visible(tab.genius_button, tab.google_button, tab.retry_button) == [True, True, True]

    urls = collect(tab.urlRequested)
    tab.genius_button.click()
    tab.google_button.click()
    assert [u[0] for u in urls] == ["https://genius.com/search?q=Fixture%20Artist%20Second",
                                    "https://www.google.com/search?q=Fixture%20Artist%20Second+lyrics"]

    asked = lrclib.gets("Second")
    lrclib.answer = found_for({"Second"})
    tab.retry_button.click()
    assert tab.state == tab_module.LOOKING_UP  # the cached not_found isn't shown while the retry runs
    qtbot.waitUntil(lambda: tab.state == tab_module.FOUND_STATE, timeout=5000)
    assert lrclib.gets("Second") == asked + 1  # looked up again although the not_found was fresh


def test_missing_tags(make, read, library):
    _service, tab = make()
    tab.show_track(entry(read, library, "Loose/untitled.aac"))
    assert tab.state == tab_module.MISSING_TAGS
    assert "no artist or title" in tab.status.text()
    assert tab.search_urls()[0] == "https://genius.com/search?q=untitled"  # the title alone
    assert visible(tab.genius_button, tab.retry_button) == [True, False]


def test_unreadable_file(make, read, library):
    _service, tab = make()
    tab.show_track(entry(read, library, "Loose/broken.flac"))
    assert tab.state == tab_module.UNREADABLE
    assert visible(tab.genius_button, tab.retry_button, tab.spinner) == [False, False, False]


def test_lookup_error_then_retry(qtbot, make, read, library):
    lrclib = ScriptedLrclib(unreachable)
    service, tab = make(lrclib, LyricsOptions(max_attempts=1), workers=1)
    select(service, tab, track(read, library, "01 First.flac"))
    qtbot.waitUntil(lambda: tab.state == tab_module.ERROR, timeout=5000)
    assert tab.status.text() == "The lookup failed (URLError: <urlopen error no route>). It is tried again later."
    assert visible(tab.genius_button, tab.retry_button) == [True, True]
    lrclib.answer = answer
    tab.retry_button.click()
    qtbot.waitUntil(lambda: tab.state == tab_module.FOUND_STATE, timeout=5000)


def test_offline(qtbot, make, read, library):
    service, tab = make(offline=True)
    select(service, tab, track(read, library, "01 First.flac"))
    assert tab.state == tab_module.OFFLINE_STATE
    assert tab.status.text() == "Lyrics lookups are off, so LRCLIB isn't contacted."
    assert visible(tab.genius_button, tab.retry_button) == [True, False]
    service.set_offline(False)
    qtbot.waitUntil(lambda: tab.state == tab_module.FOUND_STATE, timeout=5000)


def test_found_lyrics_show_even_offline(qtbot, make, read, library):
    service, tab = make()
    first = track(read, library, "01 First.flac")
    select(service, tab, first)
    qtbot.waitUntil(lambda: tab.state == tab_module.FOUND_STATE, timeout=5000)
    service.set_offline(True)
    tab.show_track(first)
    assert tab.state == tab_module.FOUND_STATE


def test_unreachable_and_retry_now(qtbot, make, read, library):
    lrclib = ScriptedLrclib(unreachable)
    service, tab = make(lrclib, LyricsOptions(max_attempts=1, max_consecutive_failures=1), workers=1)
    select(service, tab, track(read, library, "01 First.flac"))
    qtbot.waitUntil(lambda: tab.state == tab_module.UNREACHABLE_STATE, timeout=5000)
    resume = time.strftime("%H:%M", time.localtime(service.resume_at))
    assert tab.status.text() == f"LRCLIB is unreachable. Lookups resume at {resume}."
    assert visible(tab.genius_button, tab.retry_button) == [True, True]
    lrclib.answer = answer
    tab.retry_button.click()
    qtbot.waitUntil(lambda: tab.state == tab_module.FOUND_STATE, timeout=5000)
    assert service.state != "unreachable"


def test_paused(qtbot, make, read, library):
    service, tab = make()
    service.pause()
    select(service, tab, track(read, library, "01 First.flac"))
    assert tab.state == tab_module.PAUSED_STATE
    service.resume()
    qtbot.waitUntil(lambda: tab.state == tab_module.FOUND_STATE, timeout=5000)


def test_results_for_other_keys_leave_the_tab_alone(qtbot, make, read, library):
    service, tab = make(offline=True)
    tab.show_track(track(read, library, "01 First.flac"))
    tab._entry_ready("someone\x1felse", {"status": "found"})
    assert tab.state == tab_module.OFFLINE_STATE


# -- in the window --

def test_window_shows_the_selected_track_and_opens_searches(qtbot, service, library_dir, monkeypatch):
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    launched = []
    window.launch = launched.append
    conn = service.conn
    album = next(i for (i,) in conn.execute("SELECT id FROM entries WHERE is_dir = 1")
                 if path_of(conn, i) == library_dir / "Artist" / "Album")
    window.open_folder(album)
    lyrics = window.side_panel.lyrics
    assert lyrics.state == tab_module.MESSAGE

    ids = {path_of(conn, i).name: i for (i,) in conn.execute("SELECT id FROM entries WHERE parent_id = ?", (album,))}
    window.file_view.select_ids([ids["Track 2.flac"]])
    qtbot.waitUntil(lambda: lyrics.state == tab_module.OFFLINE_STATE)  # UI test libraries are offline
    lyrics.google_button.click()
    qtbot.waitUntil(lambda: bool(launched))
    assert launched[0].startswith("https://www.google.com/search?q=")

    window.file_view.select_ids([ids["Track 2.flac"], ids["Track 3.mp3"]])
    qtbot.waitUntil(lambda: lyrics.status.text() == "Select a single track to see its lyrics.")
    window.file_view.select_ids([ids["CD 2"]])
    qtbot.waitUntil(lambda: lyrics.state == tab_module.MESSAGE and lyrics.entry_id is None)
