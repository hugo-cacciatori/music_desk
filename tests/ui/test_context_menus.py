"""Context menus, opening and playing through Windows, and the disabled drag-out (SPEC §5, §14; Ground rule 1).

Nothing is ever launched: the window's launcher and Explorer runner are replaced by recorders, and temporary
playlists go to tmp_path.
"""

import threading

import pytest
from browser_helpers import column, entry
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import QAbstractItemView, QApplication

from music_desk.core import fsops
from music_desk.ui.entry_drag import ENTRY_IDS_MIME, entry_mime


class Recorder:
    def __init__(self, error: OSError | None = None) -> None:
        self.calls: list[tuple] = []
        self.error = error
        self.lock = threading.Lock()

    def __call__(self, *args) -> None:
        with self.lock:
            self.calls.append(tuple(str(a) if not isinstance(a, bool) else a for a in args))
        if self.error:
            raise self.error


@pytest.fixture
def launched(window, tmp_path):
    recorder = Recorder()
    window.launch = recorder
    window.playlist_dir = tmp_path / "playlists"
    return recorder


@pytest.fixture
def explorer(window):
    recorder = Recorder()
    window.show_in_explorer = recorder
    return recorder


def texts(menu) -> list[str]:
    return [a.text() for a in menu.actions() if a.text()]


def action(menu, text):
    return next(a for a in menu.actions() if a.text() == text)


def select(window, *names):
    rows = column(window, "name")
    window.file_view.view.clearSelection()
    for name in names:
        window.file_view.view.selectionModel().select(
            window.file_view.proxy.index(rows.index(name), 0),
            window.file_view.view.selectionModel().SelectionFlag.Select
            | window.file_view.view.selectionModel().SelectionFlag.Rows)


# -- fsops (core, no Qt) --

def test_explorer_command_lines():
    assert fsops.explorer_command(r"C:\Music\A B\01.flac", False) == r'explorer /select,"C:\Music\A B\01.flac"'
    assert fsops.explorer_command("C:/Music/A B/", True) == r'explorer "C:\Music\A B"'
    commands = []
    fsops.open_in_explorer(r"C:\x.flac", False, run=commands.append)
    assert commands == [r'explorer /select,"C:\x.flac"']
    opened = []
    fsops.open_with_default_app(r"C:\x.flac", launcher=opened.append)
    assert opened == [r"C:\x.flac"]


def test_playlist_queries_read_the_index(service, library_dir):
    from music_desk.core.index import queries

    album = entry(service, "Artist", "Album")
    names = [e.path.name for e in queries.playlist_for_folder(service.conn, album)]
    assert names == ["track 1.flac", "Track 2.flac", "Track 3.mp3", "Track 10.flac"]  # no cover.jpg, natural
    deep = queries.playlist_for_folder(service.conn, entry(service, "Artist"), recursive=True)
    assert [e.path.name for e in deep] == names  # Artist has no files of its own; Scans has no audio
    first = queries.playlist_for_folder(service.conn, album)[0]
    assert first.path == library_dir / "Artist" / "Album" / "track 1.flac"
    assert first.title == "Tone tone.flac" and 0.5 < first.duration < 1.5
    picked = queries.playlist_for_ids(service.conn, [entry(service, "Artist", "Album", "Track 3.mp3"),
                                                      entry(service, "Artist", "Album", "cover.jpg"),
                                                      entry(service, "Artist", "Album", "track 1.flac")])
    assert [e.path.name for e in picked] == ["Track 3.mp3", "track 1.flac"]  # the given order, audio only


# -- menus --

def test_file_menu(window, service):
    window.open_folder(entry(service, "Artist", "Album"))
    one = window.list_menu([entry(service, "Artist", "Album", "Track 2.flac")])
    assert texts(one) == ["Open", "Play selection", "Open in File Explorer", "Copy path", "Rename	F2", "Tools…",
                          "Remove ReplayGain tags…", "Delete	Del"]
    two = window.list_menu([entry(service, "Artist", "Album", "Track 2.flac"),
                            entry(service, "Artist", "Album", "cover.jpg")])
    assert texts(two)[-4:] == ["Copy paths", "Tools…", "Remove ReplayGain tags…", "Delete	Del"]  # no Rename
    assert not action(two, "Open").isEnabled() and action(two, "Play selection").isEnabled()
    images = window.list_menu([entry(service, "Artist", "Album", "cover.jpg")])
    assert not action(images, "Play selection").isEnabled()
    assert not action(images, "Tools…").isEnabled()  # no music file in the selection
    assert not action(images, "Remove ReplayGain tags…").isEnabled()


def test_folder_menus(window, service):
    window.open_folder(entry(service, "Artist"))
    folder = window.list_menu([entry(service, "Artist", "Album")])
    assert texts(folder) == ["Open", "Play folder", "Play folder and subfolders", "Open in File Explorer",
                             "Copy path", "Rename	F2", "Rescan", "Tools…", "Remove ReplayGain tags…",
                             "Delete	Del"]
    background = window.list_menu([])
    assert texts(background) == ["Play folder", "Play folder and subfolders", "Open in File Explorer",
                                 "Copy path", "Rescan", "Tools…", "Remove ReplayGain tags…"]
    tree = window.sidebar.context_menu_for(window.sidebar.tree.currentIndex())
    assert texts(tree)[:10] == texts(folder) and "Remove from library" not in texts(tree)  # not a root
    for absent in ("Rename", "Cut", "Copy", "Paste", "New folder"):
        assert absent not in texts(folder) + texts(tree)
    assert texts(window.sidebar.context_menu_for(QModelIndex())) == ["Add folder…"]


def test_right_click_on_an_unselected_row_selects_it(qtbot, window, service):
    window.open_folder(entry(service, "Artist", "Album"))
    select(window, "Track 2.flac")
    requested = []
    window.file_view.menuRequested.disconnect()
    window.file_view.menuRequested.connect(lambda ids, _pos: requested.append(ids))
    view = window.file_view.view
    row = column(window, "name").index("Track 3.mp3")
    window.file_view._menu_requested(view.visualRect(window.file_view.proxy.index(row, 0)).center())
    assert requested == [[entry(service, "Artist", "Album", "Track 3.mp3")]]


def test_selection_is_in_view_order(window, service):
    window.open_folder(entry(service, "Artist", "Album"))
    window.file_view.view.sortByColumn(0, Qt.SortOrder.DescendingOrder)
    select(window, "track 1.flac", "Track 10.flac")
    assert window.file_view.selected_ids() == [entry(service, "Artist", "Album", "Track 10.flac"),
                                               entry(service, "Artist", "Album", "track 1.flac")]


# -- actions --

def test_double_click_on_a_file_opens_it(qtbot, window, service, library_dir, launched):
    window.open_folder(entry(service, "Artist", "Album"))
    row = column(window, "name").index("cover.jpg")
    window.file_view.view.activated.emit(window.file_view.proxy.index(row, 0))
    qtbot.waitUntil(lambda: launched.calls == [(str(library_dir / "Artist" / "Album" / "cover.jpg"),)],
                    timeout=3000)
    assert window.folder_id == entry(service, "Artist", "Album")  # a file doesn't navigate


def test_open_in_explorer(qtbot, window, service, library_dir, explorer):
    window.open_folder(entry(service, "Artist", "Album"))
    action(window.list_menu([entry(service, "Artist", "Album", "Track 2.flac")]), "Open in File Explorer").trigger()
    action(window.list_menu([entry(service, "Artist", "Album", "CD 2")]), "Open in File Explorer").trigger()
    album = library_dir / "Artist" / "Album"
    qtbot.waitUntil(lambda: explorer.calls == [(str(album / "Track 2.flac"), False), (str(album / "CD 2"), True)],
                    timeout=3000)


def test_copy_paths(window, service, library_dir):
    window.open_folder(entry(service, "Artist", "Album"))
    ids = [entry(service, "Artist", "Album", "Track 2.flac"), entry(service, "Artist", "Album", "Track 3.mp3")]
    action(window.list_menu(ids), "Copy paths").trigger()
    album = library_dir / "Artist" / "Album"
    assert QApplication.clipboard().text() == f"{album / 'Track 2.flac'}\n{album / 'Track 3.mp3'}"
    assert "2 paths copied" in window.statusBar().currentMessage()


def test_play_folder_writes_a_playlist_and_opens_it(qtbot, window, service, library_dir, launched, tmp_path):
    window.open_folder(entry(service, "Artist"))
    action(window.list_menu([entry(service, "Artist", "Album")]), "Play folder").trigger()
    qtbot.waitUntil(lambda: len(launched.calls) == 1, timeout=3000)
    playlist = launched.calls[0][0]
    assert playlist.startswith(str(tmp_path / "playlists")) and playlist.endswith(".m3u8")
    lines = open(playlist, encoding="utf-8").read().splitlines()
    album = library_dir / "Artist" / "Album"
    assert lines[0] == "#EXTM3U"
    assert lines[2::2] == [str(album / n) for n in ("track 1.flac", "Track 2.flac", "Track 3.mp3", "Track 10.flac")]
    assert lines[1].startswith("#EXTINF:1,Fixture Artist - Tone tone.flac")


def test_play_selection_follows_the_view(qtbot, window, service, launched):
    window.open_folder(entry(service, "Artist", "Album"))
    window.file_view.view.sortByColumn(0, Qt.SortOrder.DescendingOrder)
    select(window, "Track 2.flac", "Track 10.flac", "cover.jpg")
    action(window.list_menu(window.file_view.selected_ids()), "Play selection").trigger()
    qtbot.waitUntil(lambda: len(launched.calls) == 1, timeout=3000)
    lines = open(launched.calls[0][0], encoding="utf-8").read().splitlines()
    assert [line.rsplit("\\", 1)[-1] for line in lines[2::2]] == ["Track 10.flac", "Track 2.flac"]


def test_nothing_to_play(window, service, launched):
    window.play_folder(entry(service, "Artist", "Scans"), recursive=True)
    assert "no audio files" in window.statusBar().currentMessage()
    assert launched.calls == []


def test_no_app_for_playlists(qtbot, window, service, library_dir, tmp_path, explorer):
    window.launch = Recorder(OSError(1155, "No application is associated with the specified file"))
    window.playlist_dir = tmp_path / "playlists"
    window.play_folder(entry(service, "Artist", "Album"), recursive=False)
    qtbot.waitUntil(window.toast.isVisible, timeout=3000)
    assert ".m3u8" in window.toast.label.text() and window.toast.button.text() == "Open in File Explorer"
    window.toast.button.click()
    qtbot.waitUntil(lambda: explorer.calls == [(str(library_dir / "Artist" / "Album"), True)], timeout=3000)


def test_rescan_from_the_menu(window, service, monkeypatch):
    scanned = []
    window.open_folder(entry(service, "Artist"))
    monkeypatch.setattr(service.indexer, "request_folder", scanned.append)  # first in the queue
    action(window.list_menu([entry(service, "Artist", "Album")]), "Rescan").trigger()
    assert scanned == [entry(service, "Artist", "Album")]


# -- drag-out stays disabled (a drop in Explorer would move the files) --

def test_no_drag_out(window, service):
    """Ground rule 1: the views drag entry ids for the playlist builder, never files, and only as a copy, so a
    drop in File Explorer does nothing (tests/ui/test_playlist_dnd.py checks the drag's content)."""
    window.open_folder(entry(service, "Artist", "Album"))
    window.file_view.view.selectAll()
    for view in (window.file_view.view, window.sidebar.tree):
        assert view.dragDropMode() == QAbstractItemView.DragDropMode.DragOnly  # nothing can be dropped on them
        assert view.defaultDropAction() == Qt.DropAction.CopyAction
        model = view.model()
        index = model.index(0, 0)
        assert index.isValid()
        assert model.mimeData([index]) is None or not model.mimeData([index]).hasUrls()
    ids = window.file_view.view.drag_ids()
    assert ids and entry_mime(ids).formats() == [ENTRY_IDS_MIME]
