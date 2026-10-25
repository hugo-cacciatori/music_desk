"""The right-click "Trim this track…" with MusicDesk open (SPEC §31, §35): the Trim page of that track in a window of
its own, beside the main window; a format Trim can't cut is refused by the page; Trim runs the app's trim (the real
bundled flac, on copied fixtures), and the main window's index re-reads the folder."""

import shutil

import pytest

from music_desk.context_menu import request as menu
from music_desk.context_menu.tool_host import TrimWindow


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


def trim_window(open_app, path) -> TrimWindow:
    open_app.run_entry(menu.TRIM, [str(path)])
    window = open_app.entry_windows[-1]
    assert isinstance(window, TrimWindow)
    return window  # not given to qtbot: the fixture's release() closes it (test_right_click_split_trim_alone.py)


def ready(qtbot, trim):
    qtbot.waitUntil(lambda: trim.page.state in ("editing", "error") and (trim.page.state == "error" or (
        trim.plan is not None and not trim._planning)), timeout=10000)


def test_the_tracks_trim_page_then_trim(qtbot, open_app, album, monkeypatch):
    main = open_app.window
    requested = []
    monkeypatch.setattr(main, "refresh_after_tool", requested.append)
    window = trim_window(open_app, album / "tone.wav")
    assert window.parent() is None and main.center.currentWidget() is main.file_view  # its own window (SPEC §35)
    trim = window.controller
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
    qtbot.waitUntil(lambda: bool(requested))  # the main window's index catches up


def test_a_format_trim_cant_cut_is_refused_by_the_page(qtbot, open_app, album):
    trim = trim_window(open_app, album / "tone.mp3").controller
    ready(qtbot, trim)
    assert trim.page.state == "error" and "MP3" in trim.page.message.text()


def test_the_entry_parses():
    assert menu.parse_arguments(["--trim", r"D:\a.flac"]) == menu.StartRequest(menu.TRIM, (r"D:\a.flac",))
