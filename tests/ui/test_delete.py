"""Delete to the Recycle Bin (SPEC §19; Ground rule 1): Del, Shift+Del and the menus, the question, the refusals
and the selection afterwards.

The Recycle Bin is never used: the window's recycler moves the files into tmp_path/bin (so the index sees them
go) and records them, and its check is replaced too.
"""

import shutil
import threading
from pathlib import Path

import pytest
from browser_helpers import entry
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtTest import QTest

from music_desk.core.fsops import CannotRecycle


class Bin:
    """Stands in for the Recycle Bin: moves items aside and records them, or fails for the names in `fail`."""

    def __init__(self, folder: Path, fail: dict[str, OSError] | None = None) -> None:
        self.folder = folder
        self.fail = fail or {}
        self.paths: list[Path] = []
        self.lock = threading.Lock()

    def __call__(self, path: Path) -> None:
        if path.name in self.fail:
            raise self.fail[path.name]
        self.folder.mkdir(exist_ok=True)
        shutil.move(path, self.folder / f"{len(self.paths)}-{path.name}")
        with self.lock:
            self.paths.append(path)


@pytest.fixture
def recycle_bin(window, tmp_path):
    bin_ = Bin(tmp_path / "bin")
    window.delete.recycle = bin_
    window.delete.recycle_check = lambda _path: None
    return bin_


def album_dir(library_dir) -> Path:
    return library_dir / "Artist" / "Album"


def focus(qtbot, window, view) -> None:
    window.activateWindow()
    view.setFocus()
    qtbot.waitUntil(view.hasFocus, timeout=3000)


def test_del_sends_the_selection_and_selects_the_next_row(qtbot, window, service, library_dir, recycle_bin):
    window.open_folder(entry(service, "Artist", "Album"))
    view = window.file_view
    chosen = [entry(service, "Artist", "Album", name) for name in ("Track 2.flac", "Track 3.mp3")]
    shown = view.visible_ids()
    expected_next = next(i for i in shown[max(shown.index(c) for c in chosen) + 1:])
    view.select_ids(chosen)
    focus(qtbot, window, view.view)
    QTest.keyClick(view.view, Qt.Key.Key_Delete)
    qtbot.waitUntil(lambda: len(recycle_bin.paths) == 2, timeout=5000)
    assert sorted(p.name for p in recycle_bin.paths) == ["Track 2.flac", "Track 3.mp3"]
    # Both files leave the list, but not always together: the watcher can refresh it between the two moves
    # (measured with a slow bin), so wait for the end state rather than for the first file.
    qtbot.waitUntil(lambda: not {"Track 2.flac", "Track 3.mp3"} & set(view.visible_names())
                    and view.selected_ids() == [expected_next], timeout=5000)
    assert "track 1.flac" in view.visible_names()
    qtbot.waitUntil(lambda: window.statusBar().currentMessage() == "Sent 2 items to the Recycle Bin.", timeout=5000)
    assert service.locks.held() == []


def test_shift_del_does_the_same_never_a_permanent_delete(qtbot, window, service, library_dir, recycle_bin):
    window.open_folder(entry(service, "Artist", "Album"))
    window.file_view.select_ids([entry(service, "Artist", "Album", "cover.jpg")])
    focus(qtbot, window, window.file_view.view)
    QTest.keyClick(window.file_view.view, Qt.Key.Key_Delete, Qt.KeyboardModifier.ShiftModifier)
    qtbot.waitUntil(lambda: len(recycle_bin.paths) == 1, timeout=5000)
    qtbot.waitUntil(lambda: window.statusBar().currentMessage() == "Sent 'cover.jpg' to the Recycle Bin.",
                    timeout=5000)


def test_a_folder_goes_whole(qtbot, window, service, library_dir, recycle_bin):
    window.delete.delete_entries([entry(service, "Artist", "Album")], window.file_view.view)
    qtbot.waitUntil(lambda: len(recycle_bin.paths) == 1, timeout=5000)
    assert not album_dir(library_dir).exists()
    qtbot.waitUntil(lambda: service.conn.execute(
        "SELECT 1 FROM entries WHERE name = 'Album'").fetchone() is None, timeout=5000)


def test_a_folder_and_its_subfolder_send_the_folder_once(qtbot, window, service, library_dir, recycle_bin):
    album, cd2 = entry(service, "Artist", "Album"), entry(service, "Artist", "Album", "CD 2")
    window.delete.delete_entries([cd2, album], window.sidebar.tree)
    qtbot.waitUntil(lambda: len(recycle_bin.paths) == 1, timeout=5000)
    assert recycle_bin.paths == [album_dir(library_dir)]


def test_a_library_root_is_refused(qtbot, window, service, library_dir, recycle_bin):
    window.delete.delete_entries([service.roots()[0].top_entry_id], window.sidebar.tree)
    assert window.toast.isVisible() and "is a library folder. Use Remove from library" in window.toast.label.text()
    assert recycle_bin.paths == [] and service.locks.held() == []


def test_an_item_a_job_holds_is_refused(qtbot, window, service, library_dir, recycle_bin):
    token = service.locks.acquire([album_dir(library_dir)], "Rename")
    try:
        window.delete.delete_entries([entry(service, "Artist", "Album", "Track 2.flac")], window.file_view.view)
        assert window.toast.label.text() == "Track 2.flac is busy: Rename is running on it."
    finally:
        service.locks.release(token)
    assert recycle_bin.paths == []


def test_a_place_without_a_recycle_bin_is_refused(qtbot, window, service, library_dir, recycle_bin, explorer_calls):
    window.delete.recycle_check = lambda path: "X: is a network location, which has no Recycle Bin"
    window.delete.delete_entries([entry(service, "Artist", "Album", "Track 2.flac")], window.file_view.view)
    qtbot.waitUntil(window.toast.isVisible, timeout=5000)
    assert window.toast.label.text() == (
        "MusicDesk only deletes to the Recycle Bin, and X: is a network location, which has no Recycle Bin, so "
        "nothing was deleted. You can delete it in File Explorer.")
    assert window.toast.button.text() == "Open in File Explorer"
    window.toast.button.click()
    qtbot.waitUntil(lambda: explorer_calls == [(str(album_dir(library_dir) / "Track 2.flac"), False)], timeout=5000)
    assert recycle_bin.paths == [] and service.locks.held() == []


@pytest.fixture
def explorer_calls(window):
    calls = []
    window.show_in_explorer = lambda path, is_dir: calls.append((path, is_dir))
    return calls


def test_a_failure_names_its_file_and_the_others_still_go(qtbot, window, service, library_dir, recycle_bin):
    recycle_bin.fail = {"Track 2.flac": PermissionError(13, "The file is open in another program")}
    ids = [entry(service, "Artist", "Album", name) for name in ("Track 2.flac", "Track 3.mp3")]
    window.delete.delete_entries(ids, window.file_view.view)
    qtbot.waitUntil(window.toast.isVisible, timeout=5000)
    assert window.toast.label.text() == ("Not sent to the Recycle Bin: Track 2.flac: The file is open in another "
                                         "program")
    assert [p.name for p in recycle_bin.paths] == ["Track 3.mp3"]
    assert window.statusBar().currentMessage() == "Sent 'Track 3.mp3' to the Recycle Bin."


def test_cannot_recycle_from_the_recycler_is_a_failure_not_a_delete(qtbot, window, service, library_dir,
                                                                     recycle_bin):
    recycle_bin.fail = {"Track 2.flac": CannotRecycle("X: has no Recycle Bin")}
    window.delete.delete_entries([entry(service, "Artist", "Album", "Track 2.flac")], window.file_view.view)
    qtbot.waitUntil(window.toast.isVisible, timeout=5000)
    assert "Track 2.flac: X: has no Recycle Bin" in window.toast.label.text()
    assert (album_dir(library_dir) / "Track 2.flac").exists()


def test_deleting_the_open_folder_from_the_tree_opens_its_parent(qtbot, window, service, library_dir, recycle_bin):
    cd2 = entry(service, "Artist", "Album", "CD 2")
    window.open_folder(cd2)
    tree = window.sidebar.tree
    focus(qtbot, window, tree)
    tree.selectionModel().select(tree.currentIndex(), QItemSelectionModel.SelectionFlag.ClearAndSelect
                                 | QItemSelectionModel.SelectionFlag.Rows)
    assert tree.selected_ids() == [cd2]
    QTest.keyClick(tree, Qt.Key.Key_Delete)
    qtbot.waitUntil(lambda: window.folder_id == entry(service, "Artist", "Album"), timeout=5000)


def test_del_in_the_search_results(qtbot, window, service, library_dir, recycle_bin):
    from test_library_search import names as result_names
    from test_library_search import search

    search(qtbot, window, "Track 1")
    chosen = entry(service, "Artist", "Album", "Track 10.flac")
    window.search_view.select_ids([chosen])
    focus(qtbot, window, window.search_view.view)
    QTest.keyClick(window.search_view.view, Qt.Key.Key_Delete)
    qtbot.waitUntil(lambda: len(recycle_bin.paths) == 1, timeout=5000)
    qtbot.waitUntil(lambda: "Track 10.flac" not in result_names(window), timeout=10000)
    # The next row is selected when the delete reports, which can come after the results' refresh.
    qtbot.waitUntil(lambda: bool(window.search_view.selected_ids())
                    and chosen not in window.search_view.selected_ids(), timeout=5000)


def test_menus_offer_delete_except_for_roots_and_the_background(window, service):
    def texts(menu):
        return [a.text() for a in menu.actions()]

    window.open_folder(entry(service, "Artist", "Album"))
    assert "Delete\tDel" in texts(window.list_menu([entry(service, "Artist", "Album", "Track 2.flac")]))
    assert "Delete\tDel" in texts(window.list_menu([entry(service, "Artist", "Album", "CD 2")]))
    assert "Delete\tDel" not in texts(window.list_menu([]))  # the background: not the open folder
    sidebar = window.sidebar
    root_index = sidebar.tree.model().index(0, 0)
    assert "Delete\tDel" not in texts(sidebar.context_menu_for(root_index))
    assert "Remove from library" in texts(sidebar.context_menu_for(root_index))


def test_the_playlist_del_still_only_removes_from_the_playlist(qtbot, window, service, library_dir, recycle_bin):
    from test_playlist_dnd import drop, focus_list, names

    drop(window, [entry(service, "Artist", "Album")])
    focus_list(qtbot, window)
    window.playlist_panel.select_rows([0])
    QTest.keyClick(window.playlist_panel.view, Qt.Key.Key_Delete)
    assert len(names(window)) == 3 and recycle_bin.paths == []
    assert (album_dir(library_dir) / "track 1.flac").exists()
