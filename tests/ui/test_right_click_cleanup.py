"""The right-click Clean up with MusicDesk open (SPEC §31): every clicked folder read as the app's Clean up does,
one Redundant files window for them all; nothing found says so in a small window too. The Recycle Bin is a
recorder."""

import shutil

import pytest

from music_desk.app import Starter
from music_desk.context_menu import request as menu
from music_desk.settings import Settings
from music_desk.ui.tools.tool_cards import CLEANUP, ToolScope

ALBUM = "Fixture Artist - Fixture Album"


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "cleanup", lib / "Cleanup")
    shutil.copytree(fixtures_dir / "album" / ALBUM, lib / "Artist" / ALBUM)
    return lib


@pytest.fixture
def cleanup(window):
    controller = window.cleanup
    controller.recycled = []
    controller.recycle = controller.recycled.append
    controller.check_recycle = lambda _path: None
    return controller


def test_several_folders_one_window(qtbot, window, library_dir, cleanup):
    lrc, cue = library_dir / "Cleanup" / "lrc", library_dir / "Cleanup" / "per_track_cue"
    assert window.tools.run_entry(menu.CLEANUP, [str(lrc), str(cue)])
    assert window.statusBar().currentMessage() == "Looking for files to clean up in 2 folders…"
    qtbot.waitUntil(lambda: cleanup.offer is not None and cleanup.offer.isVisible(), timeout=10000)
    offer = cleanup.offer
    assert offer.windowTitle() == "Redundant files: Cleanup"  # the folder holding them both
    names = {item.path.name for item in offer.marked()}
    assert names == {"no_lyrics.lrc", "with_lyrics.lrc", "album.cue", "missing.cue"}  # both folders, all checked
    offer.delete_button.click()
    qtbot.waitUntil(lambda: offer.state == "report", timeout=10000)
    assert {path.name for path in cleanup.recycled} == names
    offer.close()


def test_nothing_found_says_so_in_a_window(qtbot, window, library_dir, cleanup):
    album = library_dir / "Artist" / ALBUM
    for junk in album.rglob("*"):
        if junk.suffix.lower() in (".cue", ".nfo", ".sfv", ".txt", ".lrc"):
            junk.unlink()
    assert window.tools.run_entry(menu.CLEANUP, [str(album)])
    qtbot.waitUntil(lambda: cleanup.notice is not None and cleanup.notice.isVisible(), timeout=10000)
    assert cleanup.notice.message.text() == f"Nothing to clean up in {ALBUM}."
    assert cleanup.offer is None
    cleanup.notice.close_button.click()
    assert not cleanup.notice.isVisible()


def test_the_toolbars_clean_up_keeps_its_status_line(qtbot, window, library_dir, cleanup):
    """Only the menu's Clean up opens the small window: the app's own, finding nothing, says it in the status bar."""
    album = library_dir / "Artist" / ALBUM
    for junk in album.rglob("*"):
        if junk.suffix.lower() in (".cue", ".nfo", ".sfv", ".txt", ".lrc"):
            junk.unlink()
    window.tools.run_entry(menu.CLEANUP, [str(album)])
    qtbot.waitUntil(lambda: cleanup.notice is not None, timeout=10000)
    first = cleanup.notice
    first.close()
    cleanup.open(window.tools.cards.request(CLEANUP, ToolScope(album, -1)))
    qtbot.waitUntil(lambda: "Nothing to clean up" in window.statusBar().currentMessage(), timeout=10000)
    assert cleanup.notice is first and not first.isVisible()


def test_options_needing_a_fix_refuse(qtbot, window, library_dir, cleanup, monkeypatch):
    monkeypatch.setattr(window.tools.cards, "problems", lambda tool: ["an extension is wrong"])
    assert not window.tools.run_entry(menu.CLEANUP, [str(library_dir / "Cleanup")])
    assert "fix the Clean up options first" in window.statusBar().currentMessage()


def test_the_starter_hands_clean_up_to_the_open_window(qtbot, service, library_dir):
    starter = Starter(Settings(), lambda: service, check_tools=False)
    starter.open_folder(None)
    window = starter.window
    qtbot.addWidget(window)
    starter.run_entry(menu.CLEANUP, [str(library_dir / "Cleanup" / "lrc")])
    qtbot.waitUntil(lambda: window.cleanup.offer is not None and window.cleanup.offer.isVisible(), timeout=10000)
    window.cleanup.offer.close()


def test_the_entry_parses():
    assert menu.parse_arguments(["--cleanup", r"D:\a"]) == menu.StartRequest(menu.CLEANUP, (r"D:\a",))
