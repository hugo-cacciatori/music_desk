"""The right-click Clean up with MusicDesk open (SPEC §31, §35): every clicked folder read as the app's Clean up does,
one Redundant files window for them all, beside the main window, whose index re-reads the folders. The Recycle Bin
is a recorder."""

import shutil

import pytest

from music_desk.context_menu import request as menu
from music_desk.context_menu.cleanup_entry import CleanupEntry
from music_desk.ui.tools.tool_cards import CLEANUP, ToolScope

ALBUM = "Fixture Artist - Fixture Album"


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "cleanup", lib / "Cleanup")
    shutil.copytree(fixtures_dir / "album" / ALBUM, lib / "Artist" / ALBUM)
    return lib


def test_several_folders_one_window(qtbot, open_app, library_dir, monkeypatch):
    main = open_app.window
    requested = []
    monkeypatch.setattr(main, "refresh_after_tool", requested.append)
    lrc, cue = library_dir / "Cleanup" / "lrc", library_dir / "Cleanup" / "per_track_cue"
    open_app.run_entry(menu.CLEANUP, [str(lrc), str(cue)])
    entry = open_app.entry_windows[-1]
    assert isinstance(entry, CleanupEntry)
    entry.recycled = []
    entry.recycle = entry.recycled.append
    entry.check_recycle = lambda _path: None
    qtbot.waitUntil(lambda: entry.offer is not None and entry.offer.isVisible(), timeout=10000)
    offer = entry.offer
    qtbot.addWidget(offer)
    assert offer.parent() is None and main.center.currentWidget() is main.file_view  # its own window (SPEC §35)
    assert offer.windowTitle() == "Redundant files: Cleanup"  # the folder holding them both
    names = {item.path.name for item in offer.marked()}
    assert names == {"no_lyrics.lrc", "with_lyrics.lrc", "album.cue", "missing.cue"}  # both folders, all checked
    offer.delete_button.click()
    qtbot.waitUntil(lambda: offer.state == "report", timeout=10000)
    assert {path.name for path in entry.recycled} == names
    assert set(requested) == {lrc, cue}  # the main window's index re-reads them
    offer.close()


def test_the_toolbars_clean_up_keeps_its_status_line(qtbot, window, library_dir):
    """The toolbar's Clean up, finding nothing, says so in the status bar (the right-click one in a window)."""
    album = library_dir / "Artist" / ALBUM
    for junk in album.rglob("*"):
        if junk.suffix.lower() in (".cue", ".nfo", ".sfv", ".txt", ".lrc"):
            junk.unlink()
    window.cleanup.open(window.tools.cards.request(CLEANUP, ToolScope(album, -1)))
    qtbot.waitUntil(lambda: "Nothing to clean up" in window.statusBar().currentMessage(), timeout=10000)
    assert window.cleanup.offer is None


def test_the_entry_parses():
    assert menu.parse_arguments(["--cleanup", r"D:\a"]) == menu.StartRequest(menu.CLEANUP, (r"D:\a",))
