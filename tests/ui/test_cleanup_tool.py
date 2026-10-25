"""The toolbar's Clean up (SPEC §32, §35): the Clean up entry on the selected folders or the open folder; its window
reads the trees on a worker, then opens the Redundant files window with every box checked; nothing found says so
in a small window. The Recycle Bin is a recorder."""

import shutil

import pytest

from music_desk.context_menu.cleanup_entry import CleanupEntry
from music_desk.core.index.store import path_of
from music_desk.ui.tools.tool_cards import CLEANUP

ALBUM = "Fixture Artist - Fixture Album"


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "cleanup", lib / "Cleanup")
    shutil.copytree(fixtures_dir / "album" / ALBUM, lib / "Artist" / ALBUM)
    return lib


def folder_id(service, path) -> int:
    conn = service.conn
    return next(i for (i,) in conn.execute("SELECT id FROM entries WHERE is_dir = 1") if path_of(conn, i) == path)


def click(qtbot, open_app, service, folder, names=()) -> CleanupEntry | None:
    """The toolbar's Clean up on `folder` (open), or on the named items selected in it: its entry, if it started."""
    main = open_app.window
    main.open_folder(folder_id(service, folder))
    ids = {r.name: r.id for r in main.file_view.model.rows}
    main.file_view.select_ids([ids[name] for name in names])
    before = list(open_app.entry_windows)
    main.tool_actions[CLEANUP].hovered.emit()
    main.tool_actions[CLEANUP].trigger()
    started = [w for w in open_app.entry_windows if w not in before]
    if not started:
        return None
    [entry] = started
    assert isinstance(entry, CleanupEntry)
    entry.recycled = []
    entry.recycle = entry.recycled.append
    entry.check_recycle = lambda _path: None
    qtbot.waitUntil(lambda: entry.offer is not None or entry.notice is not None, timeout=10000)
    for window in (entry.offer, entry.notice):
        if window is not None:
            qtbot.addWidget(window)
    return entry


def test_the_button_opens_the_window_with_every_box_checked(qtbot, open_app, service, library_dir):
    folder = library_dir / "Cleanup"
    entry = click(qtbot, open_app, service, folder)
    main = open_app.window
    assert main.tool_actions[CLEANUP].toolTip().startswith("Clean up this folder… (Cleanup). Lists the redundant")
    assert entry is not None and entry.offer is not None
    offer = entry.offer
    assert offer.parent() is None and main.center.currentWidget() is main.file_view  # its own window (SPEC §35)
    assert offer.windowTitle() == "Redundant files: Cleanup"
    names = {item.path.name for item in offer.marked()}
    assert names == {"no_lyrics.lrc", "with_lyrics.lrc", "album.cue", "missing.cue", "checksums.sfv", "info.nfo",
                     "notes.txt"}  # all checked
    assert offer.kept_rows() == [("Fixture Album.cue", "kept: needed to play Fixture Album.flac")]
    offer.delete_button.click()
    qtbot.waitUntil(lambda: offer.state == "report", timeout=10000)
    assert {path.name for path in entry.recycled} == names
    offer.close()


def test_selected_folders_are_cleaned_up_together(qtbot, open_app, service, library_dir):
    entry = click(qtbot, open_app, service, library_dir / "Cleanup", ["lrc", "per_track_cue"])
    assert entry is not None and entry.offer is not None
    assert {item.path.name for item in entry.offer.marked()} == {"no_lyrics.lrc", "with_lyrics.lrc", "album.cue",
                                                                "missing.cue"}
    entry.offer.close()


def test_files_are_refused_with_what_to_select(qtbot, open_app, service, library_dir):
    main = open_app.window
    assert click(qtbot, open_app, service, library_dir / "Artist" / ALBUM, ["track2.flac"]) is None
    why = "Clean up: it works on folders: select folders, or nothing for the open folder."
    assert main.statusBar().currentMessage() == why and main.tool_actions[CLEANUP].toolTip() == why


def test_nothing_found_says_so(qtbot, open_app, service, library_dir):
    album = library_dir / "Artist" / ALBUM
    for junk in album.rglob("*"):
        if junk.suffix.lower() in (".cue", ".nfo", ".sfv", ".txt", ".lrc"):
            junk.unlink()
    entry = click(qtbot, open_app, service, album)
    assert entry is not None and entry.offer is None and entry.notice is not None
    assert entry.notice.message.text() == f"Nothing to clean up in {ALBUM}."
    entry.notice.close_button.click()


def test_bad_options_start_nothing(qtbot, open_app, service, library_dir):
    card = open_app.window.tools.cards.cleanup_card
    card.junk_extensions.setText(".flac")
    card.junk_extensions.textEdited.emit(".flac")
    entry = click(qtbot, open_app, service, library_dir / "Cleanup")
    assert entry is not None and entry.offer is None and entry.notice is not None
    assert "File > Options…" in entry.notice.message.text()
    entry.notice.close_button.click()
    card.reset()
