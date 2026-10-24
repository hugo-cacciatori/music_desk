"""The right-click Rename with MusicDesk open (SPEC §31): any mix of folders and files makes one preview in the main
window, and Apply runs it as the app's Rename job. Files are copies."""

import shutil

import pytest

from music_desk.app import Starter
from music_desk.context_menu import request as menu
from music_desk.settings import Settings

ALBUM = "Fixture Artist - Fixture Album"


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "album" / ALBUM, lib / "Artist" / ALBUM)
    shutil.copytree(fixtures_dir / "cleanup" / "lrc", lib / "Artist" / "Lyrics")
    return lib


def preview(qtbot, window, paths):
    assert window.tools.run_entry(menu.RENAME, [str(p) for p in paths])
    view = window.rename_preview
    assert window.center.currentWidget() is view
    qtbot.waitUntil(lambda: view.state in ("preview", "error"), timeout=10000)
    return view


def test_folders_and_files_in_one_preview(qtbot, window, service, library_dir):
    artist = library_dir / "Artist"
    album, lyrics = artist / ALBUM, artist / "Lyrics"
    view = preview(qtbot, window, [album, lyrics / "no_lyrics.flac", lyrics / "no_lyrics.lrc"])
    assert "1 folder and 2 files in" in view.heading.text()
    rows = {(row[0], row[1]): row for row in view.visible_rows()}
    assert rows[(ALBUM, "track2.flac")][2:4] == ("02 Second.flac", "rename")
    assert rows[(f"{ALBUM}/lite", "01 First.flac")][3] == "unchanged"  # the album's subfolders, as the card says
    assert {name for folder, name in rows if folder == "Lyrics"} == {"no_lyrics.flac"}  # only the clicked track
    view.apply_button.click()
    qtbot.waitUntil(lambda: view.state in ("result", "error") and window.tools.rename_job is None, timeout=10000)
    assert (album / "02 Second.flac").exists() and (lyrics / "02 No Lyrics.flac").exists()
    assert (lyrics / "02 No Lyrics.lrc").exists()  # its sidecar follows
    assert (lyrics / "with_lyrics.flac").exists()  # not clicked: left as it was
    assert window.jobs.jobs[-1].kind == "Rename"


def test_a_folder_in_use_is_refused_at_apply(qtbot, window, service, library_dir):
    album = library_dir / "Artist" / ALBUM
    view = preview(qtbot, window, [album])
    lock = service.locks.acquire([album], "the Split tool")
    view.apply_button.click()
    assert "is in use by the Split tool" in window.toast.label.text() and view.state == "preview"
    service.locks.release(lock)
    assert (album / "track2.flac").exists()


def test_options_needing_a_fix_refuse_the_preview(qtbot, window, library_dir, monkeypatch):
    monkeypatch.setattr(window.tools.cards, "problems", lambda tool: ["a mask is empty"])
    assert not window.tools.run_entry(menu.RENAME, [str(library_dir / "Artist")])
    assert "fix the File names or Rename options first" in window.statusBar().currentMessage()


def test_the_starter_hands_rename_to_the_open_window(qtbot, service, library_dir):
    starter = Starter(Settings(), lambda: service, check_tools=False)
    starter.open_folder(None)
    window = starter.window
    qtbot.addWidget(window)
    starter.run_entry(menu.RENAME, [str(library_dir / "Artist" / ALBUM)])
    qtbot.waitUntil(lambda: window.rename_preview.state == "preview", timeout=10000)


def test_the_entry_parses():
    assert menu.parse_arguments(["--rename", r"D:\a", r"D:\b.flac"]) == menu.StartRequest(
        menu.RENAME, (r"D:\a", r"D:\b.flac"))
