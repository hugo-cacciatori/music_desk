"""The Advanced search window (SPEC §26), opened by its toolbar button: a separate, non-modal window of album lists
to fix by hand, over a copied fixture library."""

import shutil
import threading

import pytest
from browser_helpers import entry
from PySide6.QtWidgets import QToolBar

from refrain.core.index import album_search
from refrain.core.tags import TaggedFile
from refrain.services import album_search_service

RG = {"replaygain_track_gain": "-6.00 dB", "replaygain_track_peak": "0.9", "replaygain_album_gain": "-6.50 dB",
      "replaygain_album_peak": "0.95"}


@pytest.fixture
def library_dir(library_dir, fixtures_dir):
    """The shared library (Artist/Album has no ReplayGain tags), plus an EAC-style rip and a tagged album."""
    shutil.copytree(fixtures_dir / "split" / "hidden_track", library_dir / "Rips" / "EAC")
    tagged = library_dir / "Tagged"
    tagged.mkdir()
    shutil.copy2(fixtures_dir / "formats" / "tone.flac", tagged / "01.flac")
    tag_replaygain(tagged / "01.flac")
    return library_dir


def tag_replaygain(path):
    tagged = TaggedFile(path)
    for key, value in RG.items():
        tagged.set_replaygain(key, value)
    tagged.save()


def open_window(window):
    window.advanced_search_action.trigger()
    return window.advanced_search


def run(qtbot, search_window, key=None):
    if key is not None:
        next(b for b in search_window.options.buttons() if b.property("search") == key).setChecked(True)
    search_window.start_button.click()
    qtbot.waitUntil(lambda: search_window.start_button.isEnabled(), timeout=10000)
    return search_window.paths()


def test_the_menus_and_the_toolbar(window):
    # No menu bar since SPEC §36 task 3: the … button holds Options… (§30), About and Exit.
    assert window.menuBar().actions() == []
    toolbar = [a.text() for a in window.findChild(QToolBar, "tools").actions() if a.text()]
    assert toolbar[-2:] == ["Playlist", "Advanced search"]
    assert window.advanced_search_action.isEnabled()


def test_hide_folders_without_music_left_the_tree_menu(window):
    """It is in … > Options…, Browser only (SPEC §12)."""
    menu = window.sidebar.context_menu_for(window.sidebar.model.index(0, 0))
    assert not any("Hide folders" in action.text() for action in menu.actions())


def test_a_separate_window_that_blocks_nothing(qtbot, window, service):
    search_window = open_window(window)
    assert search_window.isWindow() and search_window.isVisible() and not search_window.isModal()
    assert search_window.windowTitle() == "Advanced search" and search_window.parent() is window
    window.open_folder(entry(service, "Artist", "Album"))  # the main window keeps working beside it
    assert window.folder_id == entry(service, "Artist", "Album") and window.isEnabled()
    assert open_window(window) is search_window  # one at a time, brought to the front


def test_albums_without_replaygain(qtbot, window, library_dir):
    search_window = open_window(window)
    assert not search_window.refresh_button.isEnabled()  # nothing ran yet
    found = run(qtbot, search_window, album_search.WITHOUT_REPLAYGAIN)
    assert str(library_dir / "Artist" / "Album") in found and str(library_dir / "Rips" / "EAC") in found
    assert str(library_dir / "Tagged") not in found
    assert search_window.status.text() == f"{len(found)} albums found (albums without ReplayGain tags)."
    assert search_window.refresh_button.isEnabled()


def test_albums_not_split(qtbot, window, library_dir):
    search_window = open_window(window)
    assert run(qtbot, search_window, album_search.NOT_SPLIT) == [str(library_dir / "Rips" / "EAC")]


def test_a_double_click_shows_the_folder_in_the_main_window(qtbot, window, service, library_dir):
    search_window = open_window(window)
    run(qtbot, search_window, album_search.NOT_SPLIT)
    search_window.results.itemActivated.emit(search_window.results.item(0))
    assert window.folder_id == entry(service, "Rips", "EAC")
    assert window.center.currentWidget() is window.file_view


def test_refresh_follows_a_fix(qtbot, window, service, library_dir):
    search_window = open_window(window)
    run(qtbot, search_window, album_search.WITHOUT_REPLAYGAIN)
    album = library_dir / "Artist" / "Album"
    assert str(album) in search_window.paths()
    search_window.options.buttons()[1].setChecked(True)  # Refresh runs the last search, not the one picked now
    for path in (p for p in album.iterdir() if p.suffix in (".flac", ".mp3")):
        tag_replaygain(path)
        service.indexer.update_file(str(path))
    folder = entry(service, "Artist", "Album")
    qtbot.waitUntil(lambda: service.conn.execute(
        "SELECT count(*) FROM tracks t JOIN entries e ON e.id = t.entry_id WHERE e.parent_id = ? "
        "AND t.rg_track_gain IS NULL", (folder,)).fetchone()[0] == 0, timeout=10000)
    search_window.refresh_button.click()
    qtbot.waitUntil(lambda: search_window.start_button.isEnabled(), timeout=10000)
    assert str(album) not in search_window.paths() and "without ReplayGain" in search_window.status.text()


def test_an_empty_result_can_be_refreshed(qtbot, window, library_dir):
    for sheet in (library_dir / "Rips" / "EAC").glob("*.cue"):
        sheet.write_text('FILE "elsewhere.flac" WAVE\n', encoding="utf-8")  # it no longer plays the image
    search_window = open_window(window)
    assert run(qtbot, search_window, album_search.NOT_SPLIT) == []
    assert search_window.status.text() == "No album found (albums that are not split)."
    assert search_window.refresh_button.isEnabled()


def test_clear_empties_the_list(qtbot, window, library_dir):
    search_window = open_window(window)
    assert not search_window.clear_button.isEnabled()  # nothing to clear yet
    assert run(qtbot, search_window, album_search.WITHOUT_REPLAYGAIN)
    assert search_window.clear_button.isEnabled()
    search_window.clear_button.click()
    assert search_window.paths() == [] and search_window.status.text() == ""
    assert not search_window.refresh_button.isEnabled() and not search_window.clear_button.isEnabled()
    assert search_window.start_button.isEnabled()
    assert run(qtbot, search_window)  # a new search works as before


def test_clear_drops_a_running_search(qtbot, window, monkeypatch):
    release, finished = threading.Event(), threading.Event()
    real = album_search.run

    def slow(conn, search, cancelled=None):
        release.wait(10)
        try:
            return real(conn, search, cancelled)
        finally:
            finished.set()

    monkeypatch.setattr(album_search_service.album_search, "run", slow)
    search_window = open_window(window)
    search_window.start_button.click()
    assert search_window.clear_button.isEnabled()
    search_window.clear_button.click()
    release.set()
    qtbot.waitUntil(finished.is_set, timeout=10000)
    qtbot.wait(100)  # the queued result is delivered, and dropped
    assert search_window.paths() == [] and search_window.status.text() == ""


def test_the_search_runs_on_a_worker(qtbot, window, monkeypatch):
    release, started = threading.Event(), threading.Event()
    real = album_search.run

    def slow(conn, search, cancelled=None):
        started.set()
        release.wait(10)
        return real(conn, search, cancelled)

    monkeypatch.setattr(album_search_service.album_search, "run", slow)
    search_window = open_window(window)
    search_window.start_button.click()
    qtbot.waitUntil(started.is_set, timeout=5000)
    assert search_window.status.text() == "Searching…" and not search_window.start_button.isEnabled()
    release.set()
    qtbot.waitUntil(lambda: search_window.start_button.isEnabled(), timeout=10000)


def test_disabled_without_a_library(qtbot):
    from refrain.ui.main_window import MainWindow

    window = MainWindow(None)
    qtbot.addWidget(window)
    assert not window.advanced_search_action.isEnabled()
