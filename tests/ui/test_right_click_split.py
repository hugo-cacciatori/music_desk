"""The right-click "Split this cue sheet…" with MusicDesk open (SPEC §31): the main window comes to the front on the
sheet's Split page, as "Split into tracks…" on its preview; Split runs the app's split job (the real bundled flac,
on copied fixtures)."""

import shutil

import pytest

from music_desk.app import Starter
from music_desk.context_menu import request as menu
from music_desk.settings import Settings


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "split" / "wav_image", lib / "Albums" / "wav_image")
    return lib


@pytest.fixture
def album(library_dir):
    return library_dir / "Albums" / "wav_image"


@pytest.fixture
def split(window):
    window.split.recycle = lambda _path: None
    window.split.check_recycle = lambda _path: None
    window.split.ask_leave = lambda _name: "discard"
    return window.split


def test_the_sheets_split_page_then_split(qtbot, window, album, split):
    assert window.tools.run_entry(menu.SPLIT, [str(album / "image.cue")])
    assert window.center.currentWidget() is split.page
    qtbot.waitUntil(lambda: split.page.state == "editing" and not split._planning
                    and not split._plan_timer.isActive(), timeout=20000)
    assert split.path == album / "image.cue" and split.page.split_button.isEnabled()
    split.page.split_button.click()
    qtbot.waitUntil(lambda: split.page.state in ("result", "error") and split.job is None, timeout=30000)
    assert split.page.state == "result"
    assert (album / "01 One.flac").exists() and (album / "02 Two.flac").exists()
    assert (album / "image.wav").exists() and (album / "image.cue").exists()  # a split is just a split
    assert window.jobs.jobs[-1].kind == "Split"


def test_anything_but_one_cue_is_refused(qtbot, window, album, split):
    for paths in ([album / "image.wav"], [album / "image.cue", album / "image.cue"], []):
        assert not window.tools.run_entry(menu.SPLIT, [str(p) for p in paths])
        assert window.statusBar().currentMessage() == "Split: choose one cue sheet (.cue)."
    assert window.center.currentWidget() is not split.page


def test_the_starter_hands_split_to_the_open_window(qtbot, service, album):
    starter = Starter(Settings(), lambda: service, check_tools=False)
    starter.open_folder(None)
    window = starter.window
    qtbot.addWidget(window)
    window.split.ask_leave = lambda _name: "discard"
    starter.run_entry(menu.SPLIT, [str(album / "image.cue")])
    qtbot.waitUntil(lambda: window.split.page.state == "editing", timeout=20000)


def test_the_entry_parses():
    assert menu.parse_arguments(["--split", r"D:\a.cue"]) == menu.StartRequest(menu.SPLIT, (r"D:\a.cue",))
