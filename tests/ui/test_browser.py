"""The folder tree and the file list (SPEC §5): lazy loading, natural order, columns, sort, filter, incremental
updates without resets, and the persisted layout."""

import os
import shutil

from browser_helpers import child_names, column, entry
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy

from refrain.core.index import queries
from refrain.settings import Settings
from refrain.ui.file_view import COLUMN_INDEX, format_duration, format_size
from refrain.ui.folder_tree import ENTRY_ID_ROLE
from refrain.ui.main_window import MainWindow

# -- queries --

def test_child_folders_natural_order(service):
    names = [f.name for f in queries.child_folders(service.conn, entry(service, "Artist", "Album"))]
    assert names == ["CD 2", "CD 10"]
    artist = queries.child_folders(service.conn, entry(service, "Artist"))
    assert [(f.name, f.has_subfolders, f.n_tracks) for f in artist] == [("Album", True, 4), ("Scans", False, 0)]
    only_music = queries.child_folders(service.conn, entry(service, "Artist"), only_music=True)
    assert [f.name for f in only_music] == ["Album"]


def test_folder_listing_columns(service):
    rows = {r.name: r for r in queries.folder_listing(service.conn, entry(service, "Artist", "Album"))}
    flac = rows["Track 2.flac"]
    assert (flac.is_audio, flac.format, flac.title, flac.rg, flac.lrclib) == (
        True, "FLAC", "Tone tone.flac", "none", "pending")
    assert rows["cover.jpg"].is_audio is False and rows["cover.jpg"].rg is None
    assert rows["CD 2"].is_dir and rows["CD 2"].n_tracks == 0
    assert queries.folder_path_ids(service.conn, entry(service, "Artist", "Album")) == [
        service.roots()[0].top_entry_id, entry(service, "Artist"), entry(service, "Artist", "Album")]


def test_formatting():
    assert (format_duration(None), format_duration(59.6), format_duration(3725)) == ("", "1:00", "1:02:05")
    assert (format_size(None), format_size(512), format_size(2048), format_size(5 * 1024 ** 3)) == (
        "", "512 B", "2.0 KB", "5.0 GB")


# -- folder tree --

def test_tree_is_lazy_and_natural(qtbot, window, service, library_dir):
    model = window.sidebar.model
    top = model.index(0, 0)
    assert top.data() == str(library_dir)
    assert model.rowCount(top) == 0 and model.hasChildren(top)  # nothing loaded until expanded
    window.sidebar.tree.expand(top)
    assert child_names(model, top) == ["Artist", "Other"]
    artist = model.index(0, 0, top)
    window.sidebar.tree.expand(artist)
    album = model.index(0, 0, artist)
    window.sidebar.tree.expand(album)
    assert child_names(model, album) == ["CD 2", "CD 10"]
    assert not model.hasChildren(model.index(1, 0, artist))  # Scans has no subfolders


def test_tree_updates_without_reset(qtbot, window, service, library_dir):
    model = window.sidebar.model
    window.sidebar.tree.select_entry(queries.folder_path_ids(service.conn, entry(service, "Artist", "Album")))
    album = window.sidebar.tree.currentIndex()
    window.sidebar.tree.expand(album)
    resets = QSignalSpy(model.modelReset)

    os.rename(library_dir / "Artist" / "Album" / "CD 10", library_dir / "Artist" / "Album" / "CD 1")
    (library_dir / "Artist" / "Album" / "CD 3").mkdir()
    shutil.rmtree(library_dir / "Artist" / "Album" / "CD 2")
    qtbot.waitUntil(lambda: child_names(model, window.sidebar.tree.currentIndex()) == ["CD 1", "CD 3"],
                    timeout=10000)
    assert resets.count() == 0
    assert window.sidebar.tree.currentIndex().data() == "Album"  # the selection stayed


def test_hide_folders_without_music(qtbot, window, service):
    model = window.sidebar.model
    window.sidebar.tree.select_entry(queries.folder_path_ids(service.conn, entry(service, "Artist")))
    artist = window.sidebar.tree.currentIndex()
    window.sidebar.tree.expand(artist)
    assert child_names(model, artist) == ["Album", "Scans"]
    browser = window.tools.cards.browser_card  # … > Options…, Browser (SPEC §12)
    browser.hide_without_music.setChecked(True)
    assert child_names(model, window.sidebar.tree.currentIndex()) == ["Album"]
    assert window.settings.hide_folders_without_music
    browser.reset()  # Restore defaults: off
    assert child_names(model, window.sidebar.tree.currentIndex()) == ["Album", "Scans"]
    assert not window.settings.hide_folders_without_music


# -- file list --

def test_selecting_a_folder_lists_it(qtbot, window, service):
    album = entry(service, "Artist", "Album")
    window.sidebar.tree.select_entry(queries.folder_path_ids(service.conn, album))
    assert window.folder_id == album
    assert column(window, "name") == ["CD 2", "CD 10", "cover.jpg", "track 1.flac", "Track 2.flac", "Track 3.mp3",
                                      "Track 10.flac"]
    assert column(window, "format")[3:] == ["FLAC", "FLAC", "MP3", "FLAC"]
    assert column(window, "rg")[3] == "–" and column(window, "rg")[2] == ""


def test_folders_stay_first_in_both_directions(window, service):
    window.open_folder(entry(service, "Artist", "Album"))
    window.file_view.view.sortByColumn(COLUMN_INDEX["name"], Qt.SortOrder.DescendingOrder)
    assert column(window, "name") == ["CD 10", "CD 2", "Track 10.flac", "Track 3.mp3", "Track 2.flac",
                                      "track 1.flac", "cover.jpg"]
    window.file_view.view.sortByColumn(COLUMN_INDEX["format"], Qt.SortOrder.AscendingOrder)
    names = column(window, "name")
    assert names[:2] == ["CD 2", "CD 10"] and names[-1] == "cover.jpg"  # blank formats sort last


def test_non_audio_files_are_dimmed(window, service):
    window.open_folder(entry(service, "Artist", "Album"))
    proxy = window.file_view.proxy
    rows = {proxy.index(r, 0).data(): proxy.index(r, 0) for r in range(proxy.rowCount())}
    assert rows["cover.jpg"].data(Qt.ItemDataRole.ForegroundRole) is not None
    assert rows["Track 2.flac"].data(Qt.ItemDataRole.ForegroundRole) is None


def test_opening_a_folder_row(qtbot, window, service):
    window.open_folder(entry(service, "Artist"))
    proxy = window.file_view.proxy
    album_row = column(window, "name").index("Album")
    assert column(window, "title")[album_row] == "4 tracks"
    window.file_view.view.activated.emit(proxy.index(album_row, 0))
    assert window.folder_id == entry(service, "Artist", "Album")
    assert window.sidebar.tree.currentIndex().data(ENTRY_ID_ROLE) == window.folder_id


def test_filter(qtbot, window, service):
    window.open_folder(entry(service, "Artist", "Album"))
    window.file_view.show_filter()
    assert window.file_view.filter.isVisible()
    qtbot.keyClicks(window.file_view.filter, "TRACK 1")
    assert column(window, "name") == ["track 1.flac", "Track 10.flac"]
    window.file_view.close_filter()
    assert len(column(window, "name")) == 7 and not window.file_view.filter.isVisible()


def test_list_updates_in_place(qtbot, window, service, library_dir, fixtures_dir):
    album = library_dir / "Artist" / "Album"
    window.open_folder(entry(service, "Artist", "Album"))
    view = window.file_view
    view.select_ids([entry(service, "Artist", "Album", "Track 3.mp3")])
    resets = QSignalSpy(view.model.modelReset)

    shutil.copy2(fixtures_dir / "formats" / "tone.ogg", album / "Track 4.ogg")
    (album / "cover.jpg").unlink()
    qtbot.waitUntil(lambda: "Track 4.ogg" in column(window, "name") and "cover.jpg" not in column(window, "name"),
                    timeout=10000)
    qtbot.waitUntil(lambda: column(window, "format")[column(window, "name").index("Track 4.ogg")] == "OGG",
                    timeout=10000)
    assert resets.count() == 0
    assert view.selected_ids() == [entry(service, "Artist", "Album", "Track 3.mp3")]
    assert column(window, "name").index("Track 4.ogg") == column(window, "name").index("Track 3.mp3") + 1


def test_deleted_open_folder_opens_its_parent(qtbot, window, service, library_dir):
    window.open_folder(entry(service, "Artist", "Scans"))
    artist = entry(service, "Artist")
    shutil.rmtree(library_dir / "Artist" / "Scans")
    qtbot.waitUntil(lambda: window.folder_id == artist, timeout=10000)
    qtbot.waitUntil(lambda: column(window, "name") == ["Album"], timeout=10000)
    assert window.sidebar.tree.currentIndex().data(ENTRY_ID_ROLE) == artist


def test_removing_the_root_closes_the_folder(qtbot, window, service, monkeypatch):
    window.open_folder(entry(service, "Artist", "Album"))
    monkeypatch.setattr(window.roots, "confirm_remove", lambda _root: True)
    with qtbot.waitSignal(service.rootsChanged, timeout=5000):
        window.roots.remove_root(service.roots()[0])
    qtbot.waitUntil(lambda: window.folder_id is None, timeout=5000)
    assert column(window, "name") == [] and window.sidebar.root_texts() == []


def test_header_menu_hides_columns(window):
    header = window.file_view.view.header()
    assert header.isSectionHidden(COLUMN_INDEX["bitrate"])
    menu = window.file_view.header_menu()
    bitrate = next(a for a in menu.actions() if a.text() == "Bitrate")
    bitrate.setChecked(True)
    assert not header.isSectionHidden(COLUMN_INDEX["bitrate"])
    assert "Name" not in [a.text() for a in menu.actions()]


# -- persisted state --

def test_state_round_trip(qtbot, tmp_path, service):
    settings = Settings(path=tmp_path / "settings.json")
    window = MainWindow(service, settings)
    qtbot.addWidget(window)
    album = entry(service, "Artist", "Album")
    window.open_folder(album)
    header = window.file_view.view.header()
    header.setSectionHidden(COLUMN_INDEX["artist"], True)
    header.moveSection(header.visualIndex(COLUMN_INDEX["year"]), 1)
    window.file_view.view.sortByColumn(COLUMN_INDEX["size"], Qt.SortOrder.DescendingOrder)
    window.close()

    saved = Settings.load(tmp_path / "settings.json")
    assert saved.last_folder.endswith(os.path.join("Artist", "Album"))
    assert any(p.endswith(os.path.normcase(os.path.join("lib", "Artist"))) for p in saved.expanded_folders)

    again = MainWindow(service, saved)
    qtbot.addWidget(again)
    assert again.folder_id == album
    header = again.file_view.view.header()
    assert header.isSectionHidden(COLUMN_INDEX["artist"])
    assert header.visualIndex(COLUMN_INDEX["year"]) == 1
    assert (header.sortIndicatorSection(), header.sortIndicatorOrder()) == (COLUMN_INDEX["size"],
                                                                              Qt.SortOrder.DescendingOrder)
    assert again.sidebar.tree.isExpanded(again.sidebar.model.index_for(entry(service, "Artist")))


def test_bad_saved_layout_is_ignored(qtbot, service):
    window = MainWindow(service, Settings(file_list_header="not base64!", window_geometry="%%%"))
    qtbot.addWidget(window)
    assert window.file_view.view.header().sortIndicatorSection() == 0


def test_a_layout_saved_before_m12_is_dropped(window):
    """Qt restores a header by position: a layout saved with the ♪ column would shift the columns after it."""
    view = window.file_view
    assert view.save_layout().startswith("v2:")
    assert view.restore_layout(view.save_layout())
    old = view.save_layout().removeprefix("v2:")  # what 0.4.0 and earlier saved
    assert not view.restore_layout(old)
