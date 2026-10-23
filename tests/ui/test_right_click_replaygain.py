"""The right-click ReplayGain entries with MusicDesk open (SPEC §31): each starts at once as this app's ReplayGain
job (no recap), with the Options window's target, and its page shows the report. A fake scanner tags files as
rsgain would."""

import shutil

import pytest
from rsgain_fake import FakeScanner

from music_desk.app import Starter
from music_desk.context_menu import request as menu
from music_desk.core.replaygain import ALBUM_GAIN, ALBUM_PEAK, TRACK_GAIN
from music_desk.core.tags import TaggedFile
from music_desk.services.replaygain_job import ALBUMS, LOOSE, TRACKS
from music_desk.settings import Settings


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "replaygain" / "Fixture Artist", lib / "Fixture Artist")
    return lib


@pytest.fixture
def artist(library_dir):
    return library_dir / "Fixture Artist"


@pytest.fixture
def fake(window):
    scanner = FakeScanner()
    window.tools.rg_runner.scanner = scanner
    return scanner


def run_entry(qtbot, window, entry, paths):
    assert window.tools.run_entry(entry, [str(p) for p in paths])
    qtbot.waitUntil(lambda: window.rg_view.state in ("result", "error") and window.tools.rg_job is None,
                    timeout=20000)
    return window.rg_view.finished


def rg(path, key):
    return TaggedFile(path).get_replaygain(key)


def test_a_folder_as_an_album(qtbot, window, artist, fake):
    album = artist / "Album A"
    finished = run_entry(qtbot, window, menu.REPLAYGAIN_ALBUM, [album])
    assert "ReplayGain · This album" in window.rg_view.heading.text()
    assert window.center.currentWidget() is window.rg_view
    assert [log.result() for log in finished.album_logs] == ["OK"] and finished.log_path == album / "replaygain.log"
    assert rg(album / "lite" / "01 Tone.flac", ALBUM_GAIN) is not None  # the album workflow, subfolders included
    assert window.jobs.jobs[-1].kind == "ReplayGain"


def test_a_folder_as_a_discography(qtbot, window, artist, fake):
    finished = run_entry(qtbot, window, menu.REPLAYGAIN_DISCOGRAPHY, [artist])
    assert [row[0] for row in window.rg_view.summary_rows()] == ["Album A", "Album B"]
    assert finished.result is not None and finished.result.target is not None


def test_several_folders_each_as_an_album(qtbot, window, artist, fake):
    folders = [artist / "Album A", artist / "Album B"]
    finished = run_entry(qtbot, window, menu.REPLAYGAIN_ALBUMS, folders)
    assert finished.kind == ALBUMS and "2 folders, each as an album" in window.rg_view.heading.text()
    assert [log.folder for log in finished.album_logs] == folders
    assert all((folder / "replaygain.log").exists() for folder in folders)  # each with its own log
    assert finished.log_path is None and not window.rg_view.open_log_button.isVisible()
    # each its own album: no levelling between them
    assert rg(folders[0] / "01 Tone.flac", ALBUM_GAIN) != rg(folders[1] / "01 Loud.flac", ALBUM_GAIN)
    assert window.rg_view.message.text() == "Done: 2 albums OK."


def test_selected_tracks_as_an_album(qtbot, window, artist, fake):
    album = artist / "Album A"
    tracks = [album / "01 Tone.flac", album / "03 Spike.mp3"]
    finished = run_entry(qtbot, window, menu.REPLAYGAIN_TRACKS, tracks)
    assert finished.kind == TRACKS and "2 tracks as an album" in window.rg_view.heading.text()
    assert sorted(fake.calls[0][0]) == ["01 Tone.flac", "03 Spike.mp3"]  # only they are measured
    assert rg(album / "02 Spike.flac", ALBUM_GAIN) is None  # nor is the other one tagged
    # The album values are theirs only: the lowest of their gains, the highest of their peaks.
    assert rg(tracks[0], ALBUM_GAIN) == rg(tracks[1], ALBUM_GAIN) == "0.37 dB"
    assert rg(tracks[0], ALBUM_PEAK) == "0.956879"
    assert finished.log_path == album / "replaygain.log" and finished.log_path.exists()
    assert "02 Spike.flac" not in finished.log_path.read_text(encoding="utf-8")


def test_tracks_each_on_its_own(qtbot, window, artist, fake, tmp_path):
    notes = artist / "Album A" / "notes.txt"
    notes.write_text("x", encoding="utf-8")
    tracks = [artist / "Album A" / "02 Spike.flac", artist / "Album B" / "02 Quiet.mp3"]
    finished = run_entry(qtbot, window, menu.REPLAYGAIN_LOOSE, [*tracks, notes])
    view = window.rg_view
    assert finished.kind == LOOSE and "each of 3 tracks on its own" in view.heading.text()
    assert view.message.text() == "Done: 2 tracks OK, each on its own. 1 file left out."
    assert "Album: the track's own values" in view.text.toPlainText()
    assert finished.log_path is None and not view.open_log_button.isVisible()
    assert rg(tracks[0], TRACK_GAIN) == rg(tracks[0], ALBUM_GAIN) == "0.08 dB"
    assert not (artist / "Album A" / "replaygain.log").exists()
    assert "2 tracks each on its own, OK" in window.jobs.jobs[-1].status


def test_the_target_is_the_options_one(qtbot, window, artist, fake):
    card = window.tools.cards.replaygain_card
    options = card.options()
    card.set_options(type(options)(**{**options.__dict__, "target": -18.0}))
    run_entry(qtbot, window, menu.REPLAYGAIN_LOOSE, [artist / "Album A" / "01 Tone.flac"])
    assert fake.calls[0][2] == "-18"


def test_a_folder_in_use_is_refused(qtbot, window, service, artist, fake):
    lock = service.locks.acquire([artist / "Album A"], "the Split tool")
    assert not window.tools.run_entry(menu.REPLAYGAIN_ALBUM, [str(artist / "Album A")])
    assert "is in use by the Split tool" in window.toast.label.text()
    service.locks.release(lock)
    assert fake.calls == []


def test_the_starter_hands_an_entry_to_the_open_window(qtbot, service, artist, monkeypatch):
    starter = Starter(Settings(), lambda: service, check_tools=False)
    starter.open_folder(None)
    window = starter.window
    qtbot.addWidget(window)
    window.tools.rg_runner.scanner = FakeScanner()
    starter.run_entry(menu.REPLAYGAIN_ALBUM, [str(artist / "Album B")])
    qtbot.waitUntil(lambda: window.rg_view.state == "result", timeout=20000)
    assert (artist / "Album B" / "replaygain.log").exists()


def test_the_entries_parse(tmp_path):
    for entry in menu.REPLAYGAIN_ENTRIES:
        assert menu.parse_arguments([f"--{entry}", r"D:\a"]) == menu.StartRequest(entry, (r"D:\a",))
