"""The right-click Rename with Refrain open (SPEC §31, §35): any mix of folders and files makes one preview in the
Rename window, beside the main window, and Apply runs it; the main window's index re-reads the folders. Files are
copies."""

import shutil

import pytest
from entry_helpers import entry_window

from refrain.context_menu import request as menu

ALBUM = "Fixture Artist - Fixture Album"


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "album" / ALBUM, lib / "Artist" / ALBUM)
    shutil.copytree(fixtures_dir / "cleanup" / "lrc", lib / "Artist" / "Lyrics")
    return lib


def preview(qtbot, open_app, paths):
    window = entry_window(qtbot, open_app, menu.RENAME, paths)
    qtbot.waitUntil(lambda: window.view.state in ("preview", "error"), timeout=10000)
    return window


def test_folders_and_files_in_one_preview(qtbot, open_app, library_dir, monkeypatch):
    main = open_app.window
    requested = []
    monkeypatch.setattr(main, "refresh_after_tool", requested.append)
    artist = library_dir / "Artist"
    album, lyrics = artist / ALBUM, artist / "Lyrics"
    window = preview(qtbot, open_app, [album, lyrics / "no_lyrics.flac", lyrics / "no_lyrics.lrc"])
    view = window.view
    assert window.parent() is None and main.center.currentWidget() is main.file_view  # its own window (SPEC §35)
    assert "1 folder and 2 files in" in view.heading.text()
    rows = {(row[0], row[1]): row for row in view.visible_rows()}
    assert rows[(ALBUM, "track2.flac")][2:4] == ("02 Second.flac", "rename")
    assert rows[(f"{ALBUM}/lite", "01 First.flac")][3] == "unchanged"  # the album's subfolders, as the card says
    assert {name for folder, name in rows if folder == "Lyrics"} == {"no_lyrics.flac"}  # only the clicked track
    view.apply_button.click()
    qtbot.waitUntil(lambda: view.state in ("result", "error"), timeout=10000)
    assert (album / "02 Second.flac").exists() and (lyrics / "02 No Lyrics.flac").exists()
    assert (lyrics / "02 No Lyrics.lrc").exists()  # its sidecar follows
    assert (lyrics / "with_lyrics.flac").exists()  # not clicked: left as it was
    qtbot.waitUntil(lambda: bool(requested))  # the main window's index catches up


def test_a_folder_in_use_is_refused_at_apply(qtbot, open_app, service, library_dir):
    album = library_dir / "Artist" / ALBUM
    window = preview(qtbot, open_app, [album])
    lock = service.locks.acquire([album], "Split")
    window.view.apply_button.click()
    assert "is busy: Split is running on it" in window.view.message.text() and window.view.state == "preview"
    service.locks.release(lock)
    assert (album / "track2.flac").exists()


def test_unsaved_tag_edits_are_asked_about_first(qtbot, open_app, library_dir, monkeypatch):
    """SPEC §35: an entry that changes files with unsaved Metadata edits asks Save, Discard or Cancel first."""
    main = open_app.window
    asked = []
    monkeypatch.setattr(main, "_unsaved_edits_under", lambda folder: asked.append(folder) or True)  # Cancel
    open_app.run_entry(menu.RENAME, [str(library_dir / "Artist" / ALBUM)])
    assert asked == [library_dir / "Artist" / ALBUM] and open_app.entry_windows == []


def test_the_entry_parses():
    assert menu.parse_arguments(["--rename", r"D:\a", r"D:\b.flac"]) == menu.StartRequest(
        menu.RENAME, (r"D:\a", r"D:\b.flac"))
