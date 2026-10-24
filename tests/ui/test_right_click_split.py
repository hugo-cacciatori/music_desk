"""The right-click "Split this cue sheet…" (SPEC §31, Hugo 2026-10-24): a recap of what the split will do, then
Split or Cancel only; the split runs in the same window (the real bundled flac, on copied fixtures), then its report.
The same window with MusicDesk open (the split is then one of its jobs) or closed. 0.9.0 opened the cue editor
instead, whose Split button is hidden: these tests check the button is on screen, not only clickable in code."""

import shutil

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from music_desk.app import Starter
from music_desk.context_menu import request as menu
from music_desk.context_menu.split_recap import FIX_IT, SplitRecapWindow
from music_desk.settings import Settings


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    for name in ("wav_image", "hidden_track", "data_track"):
        shutil.copytree(fixtures_dir / "split" / name, lib / "Albums" / name)
    return lib


@pytest.fixture
def albums(library_dir):
    return library_dir / "Albums"


@pytest.fixture
def quits(monkeypatch):
    calls = []
    monkeypatch.setattr("music_desk.app.QApplication.quit", lambda: calls.append(True))
    return calls


@pytest.fixture
def starter(qtbot, service):
    starter = Starter(Settings(), lambda: service, check_tools=False)
    yield starter
    starter.release()  # the entries' windows and the cards made without a main window, freed while Qt runs
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


def recap(qtbot, starter, cue) -> SplitRecapWindow:
    starter.run_entry(menu.SPLIT, [str(cue)])
    window = starter.entry_windows[-1]
    assert isinstance(window, SplitRecapWindow)
    qtbot.waitUntil(lambda: window.state in ("recap", "error"), timeout=20000)
    return window


def buttons(window) -> list[str]:
    return [b.text() for b in (window.split_button, window.cancel_button, window.stop_button, window.close_button)
            if b.isVisible()]


def test_a_recap_then_split(qtbot, starter, albums, quits):
    album = albums / "hidden_track"
    window = recap(qtbot, starter, album / "Split Album.cue")
    assert starter.window is None and window.isVisible()
    assert buttons(window) == ["Split", "Cancel"] and window.split_button.isEnabled()  # on screen, usable
    rows = [[window.tracks.item(r, c).text() for c in range(3)] for r in range(window.tracks.rowCount())]
    assert rows == [["hidden", "00 Hidden track.flac", "0:03"], ["01", "01 Opening.flac", "0:03"],
                    ["02", "02 Second Song.flac", "0:02"], ["03", "03 Duet.flac", "0:03"]]
    assert "FLAC, 44.1 kHz, 0:12" in window.details.text() and str(album) in window.details.text()
    assert window.message.text().startswith("Split writes 4 tracks as FLAC files")
    window.split_button.click()
    qtbot.waitUntil(lambda: window.state == "result", timeout=30000)
    assert buttons(window) == ["Close"] and window.report.toPlainText().startswith("Done: 4 tracks split")
    assert (album / "01 Opening.flac").exists() and (album / "00 Hidden track.flac").exists()
    assert quits == []  # the report stays until it is closed
    window.close_button.click()
    assert starter.entry_windows == [] and quits == [True]


def test_cancel_splits_nothing(qtbot, starter, albums, quits):
    album = albums / "wav_image"
    window = recap(qtbot, starter, album / "image.cue")
    window.cancel_button.click()
    assert not list(album.glob("0*.flac")) and quits == [True]


def test_a_sheet_that_needs_a_look_says_why(qtbot, starter, albums, quits):
    window = recap(qtbot, starter, next((albums / "data_track").glob("*.cue")))
    assert buttons(window) == ["Split", "Cancel"] and not window.split_button.isEnabled()
    assert "can't be split as it is" in window.message.text() and FIX_IT in window.message.text()
    assert "data track" in window.message.text()


def test_with_musicdesk_open_the_same_window_and_a_job(qtbot, starter, service, albums, quits):
    starter.open_folder(None)
    main = starter.window
    qtbot.addWidget(main)
    page = main.center.currentWidget()
    album = albums / "wav_image"
    window = recap(qtbot, starter, album / "image.cue")
    assert main.center.currentWidget() is page  # the main window stays where it was
    window.split_button.click()
    qtbot.waitUntil(lambda: window.state == "result", timeout=30000)
    assert (album / "01 One.flac").exists()
    [job] = main.jobs.jobs
    assert job.kind == "Split" and job.state == "done"
    window.close()


def test_anything_but_one_cue_is_refused(qtbot, starter, albums, quits):
    starter.run_entry(menu.SPLIT, [str(albums / "wav_image" / "image.wav")])
    window = starter.entry_windows[-1]
    assert window.state == "error" and buttons(window) == ["Close"]


def test_the_entry_parses():
    assert menu.parse_arguments(["--split", r"D:\a.cue"]) == menu.StartRequest(menu.SPLIT, (r"D:\a.cue",))
