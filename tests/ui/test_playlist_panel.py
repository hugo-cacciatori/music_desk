"""The playlist builder's panel (SPEC §15, M7 task 2): showing and hiding it without resizing the tree or the side
panel, its columns, the header sort that reorders the playlist, missing tracks, the footer, and keeping the
playlist across restarts. Drag and drop is in test_playlist_dnd.py; here tracks are added through the model."""

from pathlib import Path

import pytest
from browser_helpers import entry
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QToolBar

from music_desk.core.index import queries
from music_desk.core.playlist_builder import Builder, Item
from music_desk.settings import Settings
from music_desk.ui.main_window import MainWindow
from music_desk.ui.playlist_panel import COLUMN_INDEX, COLUMNS, PLACEHOLDER

ALBUM = "Fixture Artist - Fixture Album"


def add_folder(window, service, *names) -> tuple[int, int]:
    """What a drop of that folder will do (task 3): its tracks, in Explorer's order."""
    ids = queries.track_ids_for(service.conn, [entry(service, *names)])
    rows = queries.track_rows(service.conn, ids)
    return window.playlist_panel.model.add([Item(i, str(rows[i].path)) for i in ids])


def test_showing_takes_only_the_centres_width(qtbot, window):
    window.resize(1400, 800)
    qtbot.wait(50)
    before = window.splitter.sizes()
    assert before[2] == 0 and not window.playlist_panel.isVisible()
    window.playlist_action.trigger()
    assert window.playlist_panel.isVisible() and window.playlist_action.isChecked()
    shown = window.splitter.sizes()
    assert shown[0] == before[0] and shown[3] == before[3]  # the tree and the side panel keep their widths
    assert shown[2] >= 200 and shown[1] == before[1] - shown[2]
    window.set_playlist_visible(False)
    assert window.splitter.sizes() == before and not window.playlist_action.isChecked()


def test_toolbar_button_and_shortcut(window):
    assert window.playlist_action.shortcut().toString() == "Ctrl+Shift+P"
    assert window.playlist_action.isCheckable() and window.playlist_action in window.findChild(QToolBar).actions()


def test_empty_state(window):
    panel = window.playlist_panel
    assert panel.model.rowCount() == 0 and panel.footer.text() == "Empty"
    assert PLACEHOLDER == "Drag folders or songs here to build a playlist"


def test_default_columns_and_rows(window, service):
    panel = window.playlist_panel
    added, skipped = add_folder(window, service, "Artist", "Album")
    assert (added, skipped) == (4, 0)
    header = panel.view.header()
    shown = [c.key for i, c in enumerate(COLUMNS) if not header.isSectionHidden(i)]
    assert shown == ["position", "title", "artist", "duration"]
    assert panel.column_texts("position") == ["1", "2", "3", "4"]
    assert panel.column_texts("name") == ["track 1.flac", "Track 2.flac", "Track 3.mp3", "Track 10.flac"]
    assert panel.column_texts("artist") == ["Fixture Artist"] * 4
    assert panel.footer.text().startswith("4 tracks · ")
    assert add_folder(window, service, "Artist", "Album") == (0, 4)  # already there


def test_header_menu_shows_any_column(window):
    menu = window.playlist_panel.header_menu()
    titles = [a.text() for a in menu.actions() if a.text()]
    assert "Album" in titles and "Folder" in titles and "Title" not in titles  # the title always stays
    album = next(a for a in menu.actions() if a.text() == "Album")
    album.setChecked(True)
    assert not window.playlist_panel.view.header().isSectionHidden(COLUMN_INDEX["album"])


def test_a_header_click_reorders_the_playlist(window, service):
    panel = window.playlist_panel
    add_folder(window, service, "Artist", "Album")
    add_folder(window, service, "Other", ALBUM, "lite")
    panel.view.sortByColumn(COLUMN_INDEX["name"], Qt.SortOrder.DescendingOrder)
    names = panel.column_texts("name")
    assert names[0] == "Track 10.flac" and names[-1] == "01 First.flac"
    assert [Path(item.path).name for item in panel.model.builder.items] == names  # the order itself changed
    added = ["track 1.flac", "Track 2.flac", "Track 3.mp3", "Track 10.flac", "01 First.flac", "02 Second.flac"]
    panel.view.sortByColumn(COLUMN_INDEX["position"], Qt.SortOrder.AscendingOrder)  # "#": the order added in
    assert panel.column_texts("name") == added
    panel.view.sortByColumn(COLUMN_INDEX["position"], Qt.SortOrder.DescendingOrder)
    assert panel.column_texts("name") == list(reversed(added))
    panel.model.sort(COLUMN_INDEX["position"], Qt.SortOrder.DescendingOrder)  # asked again: the same order
    assert panel.column_texts("name") == list(reversed(added))


def test_missing_tracks_are_dimmed(window, service, library_dir):
    panel = window.playlist_panel
    add_folder(window, service, "Artist", "Album")
    panel.model.add([Item(None, str(library_dir / "Gone" / "lost song.flac"))])
    row = panel.model.rowCount() - 1
    index = panel.model.index(row, COLUMN_INDEX["title"])
    assert index.data() == "lost song"
    assert index.data(Qt.ItemDataRole.ForegroundRole) is not None
    assert "Not in the library any more" in index.data(Qt.ItemDataRole.ToolTipRole)
    assert panel.footer.text().endswith("· 1 not in the library")


def test_renames_keep_the_track(qtbot, window, service, library_dir):
    panel = window.playlist_panel
    add_folder(window, service, "Artist", "Album")
    old = library_dir / "Artist" / "Album" / "Track 2.flac"
    new = old.with_name("Track 02.flac")
    old.rename(new)
    service.indexer.apply_renames([(old, new)])
    qtbot.waitUntil(lambda: any(item.path == str(new) for item in panel.model.builder.items), timeout=10000)
    assert not any(panel.model.missing(row) for row in range(panel.model.rowCount()))


@pytest.fixture
def settings(tmp_path):
    return Settings(path=tmp_path / "app" / "settings.json")


def test_the_playlist_and_the_panel_survive_a_restart(qtbot, service, settings):
    first = MainWindow(service, settings)
    qtbot.addWidget(first)
    first.show()
    first.set_playlist_visible(True)
    add_folder(first, service, "Artist", "Album")
    first.playlist_panel.view.header().setSectionHidden(COLUMN_INDEX["artist"], True)
    first.close()
    saved = Builder.load(settings.path.with_name("playlist.json"))
    assert len(saved) == 4

    again = MainWindow(service, Settings.load(settings.path))
    qtbot.addWidget(again)
    again.show()
    assert again.playlist_panel.isVisible() and again.playlist_action.isChecked()
    assert again.playlist_panel.column_texts("name")[0] == "track 1.flac"
    assert again.playlist_panel.view.header().isSectionHidden(COLUMN_INDEX["artist"])
