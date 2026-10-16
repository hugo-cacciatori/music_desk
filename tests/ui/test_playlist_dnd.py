"""Filling and arranging the playlist builder (SPEC §15, M7 task 3): drags from the folder tree (a multi-selection),
the file list and the search results, in Explorer's order; drops at a row; duplicates skipped with a status
message; moving rows by drag and Ctrl+Up/Down; Delete; Clear after a confirmation.

A real drag (QDrag.exec) blocks until the mouse is released, so the tests take what a drag would carry from the
views' `drag_ids` and drop it on the model, plus one real drop event to check the view's wiring.
"""

from pathlib import Path

from browser_helpers import entry
from PySide6.QtCore import QItemSelectionModel, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from music_desk.core.index import queries
from music_desk.ui.entry_drag import ENTRY_IDS_MIME, entry_ids, entry_mime

ALBUM = "Fixture Artist - Fixture Album"
ALBUM_FILES = ["track 1.flac", "Track 2.flac", "Track 3.mp3", "Track 10.flac"]


def names(window) -> list[str]:
    return [Path(item.path).name for item in window.playlist_panel.model.builder.items]


def drop(window, ids, row: int = -1) -> bool:
    model = window.playlist_panel.model
    return model.dropMimeData(entry_mime(ids), Qt.DropAction.CopyAction, row, 0, model.index(-1, -1))


def select_tree(window, service, *folders: tuple[str, ...]) -> None:
    """Ctrl+click on each folder (revealed and expanded first, as the user would)."""
    tree = window.sidebar.tree
    top = service.roots()[0].top_entry_id
    selection = tree.selectionModel()
    selection.clearSelection()
    for names_ in folders:
        chain = [top] + [entry(service, *names_[:n]) for n in range(1, len(names_) + 1)]
        index = tree.model().reveal(chain)
        parent = index.parent()
        while parent.isValid():
            tree.expand(parent)
            parent = parent.parent()
        selection.select(index, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)


def test_a_tree_multi_selection_lands_in_natural_order(window, service):
    window.set_playlist_visible(True)
    select_tree(window, service, ("Other", ALBUM), ("Artist", "Album"))  # picked in the reverse order
    ids = window.sidebar.tree.drag_ids()
    assert ids == [entry(service, "Artist", "Album"), entry(service, "Other", ALBUM)]  # the tree's order
    assert drop(window, ids)
    expected = queries.track_ids_for(service.conn, ids)
    assert window.playlist_panel.model.builder.ids == expected
    got = names(window)
    assert got[:4] == ALBUM_FILES
    other = got[4:]
    assert other[0] == "01 First.flac" and "01 Disc Two.flac" in other
    folders = [Path(item.path).parent.name for item in window.playlist_panel.model.builder.items[4:]]
    first_sub = next(i for i, name in enumerate(folders) if name != ALBUM)
    assert all(name != ALBUM for name in folders[first_sub:])  # the folder's files before its subfolders'


def test_a_drop_inserts_at_its_row_and_skips_duplicates(window, service):
    shown = []
    window.statusBar().messageChanged.connect(shown.append)
    drop(window, [entry(service, "Other", ALBUM, "lite")])
    assert names(window) == ["01 First.flac", "02 Second.flac"]
    assert shown[-1] == "Added 2 tracks to the playlist."
    album = entry(service, "Artist", "Album")
    drop(window, [album], row=1)
    assert names(window) == ["01 First.flac", *ALBUM_FILES, "02 Second.flac"]
    drop(window, [album, entry(service, "Other", ALBUM, "bonus")])
    assert names(window)[-1] == "01 Bonus.flac"
    assert shown[-1] == "Added 1 track to the playlist. 4 already in it were skipped."


def test_files_from_the_list_keep_the_view_order_and_non_music_is_left_out(window, service):
    window.open_folder(entry(service, "Artist", "Album"))
    view = window.file_view
    view.view.selectAll()
    ids = view.view.drag_ids()
    assert ids == view.selected_ids()
    shown = []
    window.statusBar().messageChanged.connect(shown.append)
    drop(window, ids)  # the subfolders (empty) and cover.jpg bring nothing
    assert names(window) == ALBUM_FILES
    drop(window, [entry(service, "Artist", "Scans")])
    assert shown[-1] == "Nothing to add: there is no music in what was dropped."


def test_search_results_drag_their_tracks(qtbot, window):
    window.open_search()
    QTest.keyClicks(window.search_field, "fir")
    qtbot.waitUntil(lambda: window._search_token is not None and window.search_shown == window._search_token
                    and not window._search_timer.isActive(), timeout=10000)
    results = window.search_view
    results.view.selectAll()
    ids = results.view.drag_ids()
    assert len(ids) == 3 and ids == results.selected_ids()
    drop(window, ids)
    assert len(names(window)) == 3


def test_drags_carry_entry_ids_only(window, service):
    """Ground rule 1: no file URL or text that File Explorer could take, and a copy only (never a move)."""
    mime = entry_mime([1, 2])
    assert mime.formats() == [ENTRY_IDS_MIME] and not mime.hasUrls() and not mime.hasText()
    assert entry_ids(mime) == [1, 2]
    rows = window.playlist_panel.model.mimeData([window.playlist_panel.model.index(0, 0)])
    assert not rows.hasUrls() and not rows.hasText()
    for view in (window.file_view.view, window.sidebar.tree, window.search_view.view):
        assert view.defaultDropAction() == Qt.DropAction.CopyAction
    assert window.playlist_panel.model.supportedDragActions() == Qt.DropAction.CopyAction


def test_a_real_drop_on_the_view(qtbot, window, service):
    window.set_playlist_visible(True)
    view = window.playlist_panel.view
    qtbot.waitUntil(view.isVisible)
    mime = entry_mime([entry(service, "Artist", "Album")])
    point = QPointF(view.viewport().rect().center())
    args = (Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    enter = QDragEnterEvent(point, *args)
    QApplication.sendEvent(view.viewport(), enter)
    assert enter.isAccepted()
    QApplication.sendEvent(view.viewport(), QDragMoveEvent(point, *args))
    QApplication.sendEvent(view.viewport(), QDropEvent(point, *args))
    assert names(window) == ALBUM_FILES


def focus_list(qtbot, window) -> None:
    window.set_playlist_visible(True)
    window.activateWindow()
    window.playlist_panel.view.setFocus()
    qtbot.waitUntil(window.playlist_panel.view.hasFocus, timeout=3000)


def test_moving_rows(qtbot, window, service):
    panel = window.playlist_panel
    drop(window, [entry(service, "Artist", "Album")])
    model = panel.model
    rows = model.mimeData([model.index(0, 0), model.index(0, 1), model.index(1, 0)])  # a drag of rows 0 and 1
    model.dropMimeData(rows, Qt.DropAction.CopyAction, 4, 0, model.index(-1, -1))  # dropped at the end
    assert names(window) == ["Track 3.mp3", "Track 10.flac", "track 1.flac", "Track 2.flac"]
    assert panel.selected_rows() == [2, 3]

    focus_list(qtbot, window)
    panel.select_rows([1])
    QTest.keyClick(panel.view, Qt.Key.Key_Up, Qt.KeyboardModifier.ControlModifier)
    assert names(window)[0] == "Track 10.flac" and panel.selected_rows() == [0]
    QTest.keyClick(panel.view, Qt.Key.Key_Up, Qt.KeyboardModifier.ControlModifier)  # at the edge: nothing moves
    assert names(window)[0] == "Track 10.flac"
    panel.select_rows([0, 1])
    QTest.keyClick(panel.view, Qt.Key.Key_Down, Qt.KeyboardModifier.ControlModifier)
    assert names(window) == ["track 1.flac", "Track 10.flac", "Track 3.mp3", "Track 2.flac"]
    assert panel.selected_rows() == [1, 2]


def test_delete_and_clear(qtbot, window, service):
    panel = window.playlist_panel
    assert not panel.clear_button.isEnabled()
    drop(window, [entry(service, "Artist", "Album")])
    assert panel.clear_button.isEnabled()
    focus_list(qtbot, window)
    panel.select_rows([1, 2])
    QTest.keyClick(panel.view, Qt.Key.Key_Delete)
    assert names(window) == ["track 1.flac", "Track 10.flac"]
    assert panel.selected_rows() == [1]

    asked = []
    panel.ask_clear = lambda count: asked.append(count) or False
    panel.clear_button.click()
    assert asked == [2] and len(names(window)) == 2  # Cancel keeps it
    panel.ask_clear = lambda count: True
    panel.clear_button.click()
    assert names(window) == [] and panel.footer.text() == "Empty" and not panel.clear_button.isEnabled()
    drop(window, [entry(service, "Artist", "Album")])  # cleared tracks can come back
    assert names(window) == ALBUM_FILES
