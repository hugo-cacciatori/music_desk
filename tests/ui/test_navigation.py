"""Address bar, history and keyboard navigation (SPEC §5)."""

import shutil

from browser_helpers import column, entry
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest

from refrain.settings import Settings
from refrain.ui.main_window import MainWindow


def open_path(window, service, *names):
    window.open_folder(entry(service, *names))


# -- address bar --

def test_breadcrumb_segments(window, service, library_dir):
    open_path(window, service, "Artist", "Album")
    assert window.address_bar.segment_labels() == [str(library_dir), "Artist", "Album"]
    texts = [b.text() for b in window.address_bar.buttons]
    assert texts == [str(library_dir), "›", "Artist", "›", "Album", "›"]


def test_clicking_a_segment_opens_it(qtbot, window, service):
    open_path(window, service, "Artist", "Album")
    artist_button = next(b for b in window.address_bar.buttons if b.text() == "Artist")
    qtbot.mouseClick(artist_button, Qt.MouseButton.LeftButton)
    assert window.folder_id == entry(service, "Artist")


def test_segment_dropdown_lists_subfolders(window, service):
    open_path(window, service, "Artist", "Album")
    menu = window.address_bar.subfolder_menu(entry(service, "Artist", "Album"))
    assert [a.text() for a in menu.actions()] == ["CD 2", "CD 10"]
    menu.actions()[1].trigger()
    assert window.folder_id == entry(service, "Artist", "Album", "CD 10")
    empty = window.address_bar.subfolder_menu(entry(service, "Artist", "Scans"))
    assert [(a.text(), a.isEnabled()) for a in empty.actions()] == [("(no subfolders)", False)]


def test_ctrl_l_edits_the_path(qtbot, window, service, library_dir):
    open_path(window, service, "Artist")
    window.activateWindow()
    qtbot.keySequence(window, QKeySequence("Ctrl+L"))
    bar = window.address_bar
    assert bar.currentWidget() is bar.line and bar.line.text() == str(library_dir / "Artist")
    bar.line.setText(f'"{library_dir / "artist" / "album"}"')  # quoted, other case: as pasted from Explorer
    qtbot.keyClick(bar.line, Qt.Key.Key_Return)
    assert window.folder_id == entry(service, "Artist", "Album")
    assert bar.currentWidget() is bar.crumbs


def test_escape_leaves_the_text_mode(qtbot, window, service):
    open_path(window, service, "Artist")
    window.address_bar.edit()
    qtbot.keyClick(window.address_bar.line, Qt.Key.Key_Escape)
    assert window.address_bar.currentWidget() is window.address_bar.crumbs
    assert window.folder_id == entry(service, "Artist")


def test_typed_file_path_opens_its_folder(window, service, library_dir):
    window.go_to_path(str(library_dir / "Artist" / "Album" / "Track 3.mp3"))
    assert window.folder_id == entry(service, "Artist", "Album")
    assert window.file_view.selected_ids() == [entry(service, "Artist", "Album", "Track 3.mp3")]


def test_paths_outside_or_missing(window, service, library_dir, tmp_path):
    open_path(window, service, "Artist")
    window.go_to_path(str(tmp_path / "elsewhere"))
    assert "isn't in the library" in window.statusBar().currentMessage()
    window.go_to_path(str(library_dir / "Artist" / "Nope" / "Deeper"))
    assert "wasn't found" in window.statusBar().currentMessage()
    assert window.folder_id == entry(service, "Artist")


# -- history --

def test_back_and_forward(window, service):
    a, b, c = entry(service, "Artist"), entry(service, "Artist", "Album"), entry(service, "Other")
    window.open_folder(a)
    window.history_back.clear()  # the window may have opened the root at start
    for folder in (b, c):
        window.open_folder(folder)
    assert not window.toolbars.forward_action.isEnabled()
    window.toolbars.back_action.trigger()
    assert window.folder_id == b
    window.toolbars.back_action.trigger()
    assert window.folder_id == a and not window.toolbars.back_action.isEnabled()
    window.toolbars.forward_action.trigger()
    assert window.folder_id == b
    window.open_folder(entry(service, "Artist", "Scans"))  # a new step clears the forward history
    assert not window.toolbars.forward_action.isEnabled()
    window.toolbars.back_action.trigger()
    assert window.folder_id == b


def test_history_skips_vanished_folders(qtbot, window, service, library_dir):
    a, scans = entry(service, "Artist"), entry(service, "Artist", "Scans")
    window.open_folder(a)
    window.open_folder(scans)
    window.open_folder(entry(service, "Other"))
    shutil.rmtree(library_dir / "Artist" / "Scans")
    qtbot.waitUntil(lambda: not window._folder_exists(scans), timeout=10000)
    window.go_back()
    assert window.folder_id == a


def test_keyboard_and_mouse_shortcuts(qtbot, window, service):
    a, b = entry(service, "Artist"), entry(service, "Artist", "Album")
    window.open_folder(a)
    window.open_folder(b)
    window.activateWindow()
    qtbot.keyClick(window, Qt.Key.Key_Left, Qt.KeyboardModifier.AltModifier)
    assert window.folder_id == a
    qtbot.keyClick(window, Qt.Key.Key_Right, Qt.KeyboardModifier.AltModifier)
    assert window.folder_id == b
    qtbot.mouseClick(window.file_view.view.viewport(), Qt.MouseButton.BackButton)
    assert window.folder_id == a
    qtbot.mouseClick(window.file_view.view.viewport(), Qt.MouseButton.ForwardButton)
    assert window.folder_id == b


# -- up --

def test_up_selects_the_folder_you_came_from(qtbot, window, service):
    album = entry(service, "Artist", "Album")
    window.open_folder(album)
    window.toolbars.up_action.trigger()
    assert window.folder_id == entry(service, "Artist")
    assert window.file_view.selected_ids() == [album]
    window.go_up()
    window.go_up()  # at the root: nothing above
    assert window.folder_id == service.roots()[0].top_entry_id
    assert not window.toolbars.up_action.isEnabled()


def test_every_way_up_selects_the_folder_you_came_from(window, service):
    """Back, the address bar and the tree too (SPEC §5); from deeper down, the folder on the way back."""
    artist, album = entry(service, "Artist"), entry(service, "Artist", "Album")
    cd2 = entry(service, "Artist", "Album", "CD 2")
    window.open_folder(artist)
    window.open_folder(album)
    window.go_back()  # Back to the parent
    assert window.folder_id == artist and window.file_view.selected_ids() == [album]
    window.open_folder(cd2)
    window.address_bar.folderRequested.emit(artist)  # a crumb two levels up
    assert window.folder_id == artist and window.file_view.selected_ids() == [album]
    window.open_folder(cd2)
    window.sidebar.folderSelected.emit(album)  # the tree
    assert window.folder_id == album and window.file_view.selected_ids() == [cd2]


def test_going_down_or_forward_selects_nothing(window, service):
    artist, album = entry(service, "Artist"), entry(service, "Artist", "Album")
    window.open_folder(artist)
    window.open_folder(album)
    assert window.file_view.selected_ids() == []
    window.go_back()
    window.go_forward()  # forward goes down again
    assert window.folder_id == album and window.file_view.selected_ids() == []


def test_backspace_and_alt_up(qtbot, window, service):
    window.open_folder(entry(service, "Artist", "Album", "CD 2"))
    window.file_view.view.setFocus()
    qtbot.keyClick(window.file_view.view, Qt.Key.Key_Backspace)
    assert window.folder_id == entry(service, "Artist", "Album")
    qtbot.keyClick(window.file_view.view, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
    assert window.folder_id == entry(service, "Artist")


def test_backspace_still_edits_text(qtbot, window, service):
    window.open_folder(entry(service, "Artist", "Album"))
    window.file_view.show_filter()
    qtbot.keyClicks(window.file_view.filter, "abc")
    qtbot.keyClick(window.file_view.filter, Qt.Key.Key_Backspace)
    assert window.file_view.filter.text() == "ab"
    assert window.folder_id == entry(service, "Artist", "Album")


# -- list keys and F5 --

def test_typing_selects_by_name_and_enter_opens(qtbot, window, service):
    window.open_folder(entry(service, "Artist", "Album"))
    view = window.file_view.view
    view.setFocus()
    qtbot.keyClicks(view, "track 3")
    assert view.currentIndex().data() == "Track 3.mp3"
    qtbot.keyClicks(view, "")
    view.setCurrentIndex(window.file_view.proxy.index(column(window, "name").index("CD 10"), 0))
    qtbot.keyClick(view, Qt.Key.Key_Return)
    assert window.folder_id == entry(service, "Artist", "Album", "CD 10")


def test_f5_rescans_the_open_folder(qtbot, window, service, monkeypatch):
    scanned = []
    album = entry(service, "Artist", "Album")
    window.open_folder(album)
    monkeypatch.setattr(service.indexer, "request_folder", scanned.append)  # first in the queue
    window.activateWindow()
    qtbot.keyClick(window, Qt.Key.Key_F5)
    assert scanned == [album]


def test_a_long_path_never_pushes_the_toolbar_into_its_overflow(qtbot, window, service):
    """The breadcrumb hides its leading folders when space runs short (found with the playlist builder's button,
    2026-10-16: a long path had pushed the search field into the toolbar's overflow menu)."""
    window.open_folder(entry(service, "Other", "Fixture Artist - Fixture Album", "lite"))
    window.resize(900, 600)
    qtbot.waitUntil(lambda: window.address_bar.hidden_segments() > 0, timeout=3000)
    assert window.search_field.isVisible() and window.search_field.width() >= 150
    assert window.address_bar.buttons[-2].isVisible()  # the open folder itself always shows
    # Wide enough for the whole path, whatever the fonts and the temp folder's length (the root's label is its
    # full path): the address bar has its row to itself since SPEC §33.
    bar = window.address_bar
    needed = sum(button.sizeHint().width() for button in bar.buttons) + 8
    window.resize(window.width() + 2 * (needed - bar.width()) + 200, 600)
    qtbot.waitUntil(lambda: window.address_bar.hidden_segments() == 0, timeout=3000)


# -- the folder tree and File Explorer (SPEC §33) --


def test_the_tree_is_hidden_by_default_and_kept(qtbot, service):
    settings = Settings()
    window = MainWindow(service, settings)
    qtbot.addWidget(window)
    window.show()
    centre = window.splitter.sizes()[1]
    assert not window.sidebar.isVisibleTo(window) and not window.toolbars.folders_action.isChecked()
    window.toolbars.folders_action.trigger()  # one click shows it
    assert window.sidebar.isVisibleTo(window) and settings.tree_visible
    sizes = window.splitter.sizes()
    handle = window.splitter.handleWidth()  # shows and hides with the tree
    assert sizes[0] > 0 and abs(sizes[1] - (centre - sizes[0])) <= handle  # its width comes from the centre only
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    QTest.keyClick(window, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert not window.sidebar.isVisibleTo(window) and not settings.tree_visible
    assert abs(window.splitter.sizes()[1] - centre) <= handle


def test_open_in_file_explorer(qtbot, window, service, library_dir):
    shown = []
    window.shell.show_in_explorer = lambda path, is_dir: shown.append((path, is_dir))
    open_path(window, service, "Artist", "Album")
    assert window.toolbars.explorer_action.isEnabled()
    window.toolbars.explorer_action.trigger()
    qtbot.waitUntil(lambda: bool(shown))
    assert shown == [(str(library_dir / "Artist" / "Album"), True)]

