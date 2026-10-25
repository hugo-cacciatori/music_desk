"""Clean up in the window (SPEC §32): the toolbar's button and the folder menus read the tree on a worker, then
open the Redundant files window with every box checked; nothing found opens no window. The Recycle Bin is a
recorder."""

import shutil

import pytest

from music_desk.core.index.store import path_of
from music_desk.ui.tools.tool_cards import CLEANUP

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


def folder_id(service, path) -> int:
    conn = service.conn
    return next(i for (i,) in conn.execute("SELECT id FROM entries WHERE is_dir = 1") if path_of(conn, i) == path)


def test_the_button_opens_the_window_with_every_box_checked(qtbot, window, service, library_dir, cleanup):
    folder = library_dir / "Cleanup"
    window.open_folder(folder_id(service, folder))
    window.file_view.select_ids([])
    window.tool_actions[CLEANUP].hovered.emit()
    assert window.tool_actions[CLEANUP].toolTip() == "Clean up Cleanup and its subfolders"
    window.tool_actions[CLEANUP].trigger()
    assert window.statusBar().currentMessage() == "Looking for files to clean up in Cleanup…"
    qtbot.waitUntil(lambda: cleanup.offer is not None and cleanup.offer.isVisible(), timeout=10000)
    offer = cleanup.offer
    assert offer.windowTitle() == "Redundant files: Cleanup"
    names = {item.path.name for item in offer.marked()}
    assert names == {"no_lyrics.lrc", "with_lyrics.lrc", "album.cue", "missing.cue", "checksums.sfv", "info.nfo",
                     "notes.txt"}  # all checked
    assert offer.kept_rows() == [("Fixture Album.cue", "kept: needed to play Fixture Album.flac")]
    offer.delete_button.click()
    qtbot.waitUntil(lambda: offer.state == "report", timeout=10000)
    assert {path.name for path in cleanup.recycled} == names
    assert window.statusBar().currentMessage() == "Sent 7 items to the Recycle Bin."
    offer.close()


def test_nothing_found_opens_no_window(qtbot, window, service, library_dir, cleanup):
    album = library_dir / "Artist" / ALBUM
    window.open_folder(folder_id(service, album))
    window.file_view.select_ids([])
    window.tools.start(CLEANUP)
    qtbot.waitUntil(lambda: window.statusBar().currentMessage() == f"Nothing to clean up in {ALBUM}.",
                    timeout=10000)
    assert cleanup.offer is None


def test_bad_options_start_nothing(qtbot, window, service, library_dir, cleanup):
    window.open_folder(folder_id(service, library_dir / "Cleanup"))
    card = window.tools.cards.cleanup_card
    card.junk_extensions.setText(".flac")
    card.junk_extensions.textEdited.emit(".flac")
    why = "fix the Clean up options first: File > Options…"
    assert window.tools.hover_text(CLEANUP) == (f"Clean up: {why}", False)
    window.tool_actions[CLEANUP].trigger()
    assert window.statusBar().currentMessage() == f"Clean up: {why}."
    assert cleanup.offer is None
    card.reset()
