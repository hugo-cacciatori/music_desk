"""The right-click "Trim this track…" with MusicDesk open (SPEC §31): the main window comes to the front on the Trim
page of that track, as Trim… on the Metadata tab; a format Trim can't cut is refused by the page; Trim runs the app's
trim job (the real bundled flac, on copied fixtures)."""

import shutil

import pytest

from music_desk.app import Starter
from music_desk.context_menu import request as menu
from music_desk.settings import Settings


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    album = lib / "Album"
    album.mkdir(parents=True)
    for name in ("tone.wav", "tone.mp3"):
        shutil.copy2(fixtures_dir / "formats" / name, album / name)
    return lib


@pytest.fixture
def album(library_dir):
    return library_dir / "Album"


def ready(qtbot, trim):
    qtbot.waitUntil(lambda: trim.page.state in ("editing", "error") and (trim.page.state == "error" or (
        trim.plan is not None and not trim._planning)), timeout=10000)


def test_the_tracks_trim_page_then_trim(qtbot, window, album):
    trim = window.trim
    assert window.tools.run_entry(menu.TRIM, [str(album / "tone.wav")])
    assert window.center.currentWidget() is trim.page
    ready(qtbot, trim)
    assert trim.page.state == "editing" and trim.path == album / "tone.wav"
    trim.page.start_edit.setText("0:00.500")  # typed, as a user would: nothing to cut until then
    trim.page.start_edit.textEdited.emit("0:00.500")
    ready(qtbot, trim)
    assert trim.page.trim_button.isEnabled()
    trim.page.trim_button.click()
    qtbot.waitUntil(lambda: trim.page.state in ("result", "error") and trim.job is None, timeout=30000)
    assert trim.page.state == "result"
    assert (album / "tone.wav").exists()  # the original stays; the copy is in the subfolder
    assert list(album.glob("*/tone.flac"))
    assert window.jobs.jobs[-1].kind == "Trim"


def test_a_format_trim_cant_cut_is_refused_by_the_page(qtbot, window, album):
    trim = window.trim
    assert window.tools.run_entry(menu.TRIM, [str(album / "tone.mp3")])
    ready(qtbot, trim)
    assert trim.page.state == "error" and "MP3" in trim.page.message.text()


def test_anything_but_one_file_is_refused(qtbot, window, album):
    for paths in ([album], [album / "tone.wav", album / "tone.mp3"], []):
        assert not window.tools.run_entry(menu.TRIM, [str(p) for p in paths])
        assert window.statusBar().currentMessage() == "Trim: choose one track (FLAC, WAV or AIFF)."
    assert window.center.currentWidget() is not window.trim.page


def test_the_starter_hands_trim_to_the_open_window(qtbot, service, album):
    starter = Starter(Settings(), lambda: service, check_tools=False)
    starter.open_folder(None)
    window = starter.window
    qtbot.addWidget(window)
    starter.run_entry(menu.TRIM, [str(album / "tone.wav")])
    qtbot.waitUntil(lambda: window.trim.page.state == "editing", timeout=10000)


def test_the_entry_parses():
    assert menu.parse_arguments(["--trim", r"D:\a.flac"]) == menu.StartRequest(menu.TRIM, (r"D:\a.flac",))
