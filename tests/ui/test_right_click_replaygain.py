"""The right-click ReplayGain entries with Refrain open (SPEC §31, §35): each opens the ReplayGain page in its own
window beside the main window, and starts at once (no recap) with the Options window's target; the page shows the
report. The main window's index re-reads the folders, and indexing waits while rsgain runs. A fake scanner tags
files as rsgain would."""

import shutil
import threading
from functools import partial

import pytest
from entry_helpers import entry_window
from rsgain_fake import FakeScanner

from music_desk.context_menu import replaygain_entry
from music_desk.context_menu import request as menu
from music_desk.core.replaygain import ALBUM_GAIN, ALBUM_PEAK, TRACK_GAIN
from music_desk.core.tags import TaggedFile
from music_desk.services.replaygain_job import ALBUMS, LOOSE, TRACKS, ReplayGainRunner


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "replaygain" / "Fixture Artist", lib / "Fixture Artist")
    return lib


@pytest.fixture
def artist(library_dir):
    return library_dir / "Fixture Artist"


@pytest.fixture
def fake(monkeypatch):
    scanner = FakeScanner()
    monkeypatch.setattr(replaygain_entry, "ReplayGainRunner", partial(ReplayGainRunner, scanner=scanner))
    return scanner


def run_entry(qtbot, open_app, entry, paths):
    window = entry_window(qtbot, open_app, entry, paths)
    qtbot.waitUntil(lambda: window.view.state in ("result", "error"), timeout=20000)
    return window


def rg(path, key):
    return TaggedFile(path).get_replaygain(key)


def test_a_folder_as_an_album_in_its_own_window(qtbot, open_app, service, artist, fake, monkeypatch):
    main = open_app.window
    requested = []
    monkeypatch.setattr(main, "refresh_after_tool", requested.append)
    album = artist / "Album A"
    window = run_entry(qtbot, open_app, menu.REPLAYGAIN_ALBUM, [album])
    assert window.parent() is None and window.isVisible()  # its own window, beside the main one (SPEC §35)
    assert main.center.currentWidget() is main.file_view  # the main window is left as it was
    assert "ReplayGain · This album" in window.view.heading.text()
    finished = window.view.finished
    assert [log.result() for log in finished.album_logs] == ["OK"] and finished.log_path == album / "replaygain.log"
    assert rg(album / "lite" / "01 Tone.flac", ALBUM_GAIN) is not None  # the album workflow, subfolders included
    assert requested == [album]  # the main window's index re-reads it
    assert not service.indexer.paused


def test_indexing_waits_while_rsgain_runs(qtbot, open_app, service, artist, monkeypatch):
    """The library pauses indexing while a task reading whole albums holds a lock, from any window (SPEC §35)."""
    paused, event = [], threading.Event()
    scanner = FakeScanner(before=lambda folder: (paused.append(service.indexer.paused), event.wait(20)))
    monkeypatch.setattr(replaygain_entry, "ReplayGainRunner", partial(ReplayGainRunner, scanner=scanner))
    window = entry_window(qtbot, open_app, menu.REPLAYGAIN_ALBUM, [artist / "Album A"])
    qtbot.waitUntil(lambda: bool(paused), timeout=20000)
    assert paused == [True]
    qtbot.waitUntil(lambda: open_app.window._indexing_paused)  # the status line says so
    event.set()
    qtbot.waitUntil(lambda: window.view.state == "result", timeout=20000)
    assert not service.indexer.paused
    qtbot.waitUntil(lambda: not open_app.window._indexing_paused)


def test_a_folder_as_a_discography(qtbot, open_app, artist, fake):
    window = run_entry(qtbot, open_app, menu.REPLAYGAIN_DISCOGRAPHY, [artist])
    assert [row[0] for row in window.view.summary_rows()] == ["Album A", "Album B"]
    finished = window.view.finished
    assert finished.result is not None and finished.result.target is not None


def test_several_folders_each_as_an_album(qtbot, open_app, artist, fake):
    folders = [artist / "Album A", artist / "Album B"]
    window = run_entry(qtbot, open_app, menu.REPLAYGAIN_ALBUMS, folders)
    view, finished = window.view, window.view.finished
    assert finished.kind == ALBUMS and "2 folders, each as an album" in view.heading.text()
    assert [log.folder for log in finished.album_logs] == folders
    assert all((folder / "replaygain.log").exists() for folder in folders)  # each with its own log
    assert finished.log_path is None and not view.open_log_button.isVisible()
    # each its own album: no levelling between them
    assert rg(folders[0] / "01 Tone.flac", ALBUM_GAIN) != rg(folders[1] / "01 Loud.flac", ALBUM_GAIN)
    assert view.message.text() == "Done: 2 albums OK."


def test_selected_tracks_as_an_album(qtbot, open_app, artist, fake):
    album = artist / "Album A"
    tracks = [album / "01 Tone.flac", album / "03 Spike.mp3"]
    window = run_entry(qtbot, open_app, menu.REPLAYGAIN_TRACKS, tracks)
    finished = window.view.finished
    assert finished.kind == TRACKS and "2 tracks as an album" in window.view.heading.text()
    assert sorted(fake.calls[0][0]) == ["01 Tone.flac", "03 Spike.mp3"]  # only they are measured
    assert rg(album / "02 Spike.flac", ALBUM_GAIN) is None  # nor is the other one tagged
    # The album values are theirs only: the lowest of their gains, the highest of their peaks.
    assert rg(tracks[0], ALBUM_GAIN) == rg(tracks[1], ALBUM_GAIN) == "0.37 dB"
    assert rg(tracks[0], ALBUM_PEAK) == "0.956879"
    assert finished.log_path == album / "replaygain.log" and finished.log_path.exists()
    assert "02 Spike.flac" not in finished.log_path.read_text(encoding="utf-8")


def test_tracks_each_on_its_own(qtbot, open_app, artist, fake):
    notes = artist / "Album A" / "notes.txt"
    notes.write_text("x", encoding="utf-8")
    tracks = [artist / "Album A" / "02 Spike.flac", artist / "Album B" / "02 Quiet.mp3"]
    window = run_entry(qtbot, open_app, menu.REPLAYGAIN_LOOSE, [*tracks, notes])
    view, finished = window.view, window.view.finished
    assert finished.kind == LOOSE and "each of 3 tracks on its own" in view.heading.text()
    assert view.message.text() == "Done: 2 tracks OK, each on its own. 1 file left out."
    assert "Album: the track's own values" in view.text.toPlainText()
    assert finished.log_path is None and not view.open_log_button.isVisible()
    assert rg(tracks[0], TRACK_GAIN) == rg(tracks[0], ALBUM_GAIN) == "0.08 dB"
    assert not (artist / "Album A" / "replaygain.log").exists()


def test_the_target_is_the_options_one(qtbot, open_app, artist, fake):
    card = open_app.window.tools.cards.replaygain_card  # the main window's cards are the entries' (one copy)
    options = card.options()
    card.set_options(type(options)(**{**options.__dict__, "target": -18.0}))
    run_entry(qtbot, open_app, menu.REPLAYGAIN_LOOSE, [artist / "Album A" / "01 Tone.flac"])
    assert fake.calls[0][2] == "-18"


def test_a_folder_in_use_is_refused(qtbot, open_app, service, artist, fake):
    lock = service.locks.acquire([artist / "Album A"], "Split")
    window = run_entry(qtbot, open_app, menu.REPLAYGAIN_ALBUM, [artist / "Album A"])
    assert "is busy: Split is running on it" in window.view.message.text()
    service.locks.release(lock)
    assert fake.calls == []


def test_tracks_of_several_folders_arent_one_album(qtbot, open_app, artist, fake):
    tracks = [artist / "Album A" / "01 Tone.flac", artist / "Album B" / "01 Loud.flac"]
    window = run_entry(qtbot, open_app, menu.REPLAYGAIN_TRACKS, tracks)
    assert "several folders" in window.view.message.text()
    assert fake.calls == []


def test_the_entries_parse(tmp_path):
    for entry in menu.REPLAYGAIN_ENTRIES:
        assert menu.parse_arguments([f"--{entry}", r"D:\a"]) == menu.StartRequest(entry, (r"D:\a",))
