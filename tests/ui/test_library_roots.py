"""Library roots in the UI (SPEC §3): the service's signals, the sidebar, Add folder, drops, toasts and removal."""

import shutil

import pytest
from PySide6.QtCore import QMimeData, QModelIndex, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent

from music_desk.core.index import roots as roots_module
from music_desk.core.index.roots import AddStatus
from music_desk.services.library import LibraryService
from music_desk.ui.main_window import MainWindow


@pytest.fixture
def library_dir(tmp_path):
    for relative in ("Music/Artist/Album", "Rips"):
        (tmp_path / "lib" / relative).mkdir(parents=True)
    (tmp_path / "lib" / "Music" / "Artist" / "Album" / "01.flac").write_bytes(b"")
    return tmp_path / "lib"


@pytest.fixture
def service(tmp_path, qtbot):
    service = LibraryService(tmp_path / "db" / "library.db", online_check_ms=60_000)
    yield service
    service.close()


@pytest.fixture
def window(service, qtbot):
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    return window


def add(qtbot, service, *paths):
    with qtbot.waitSignal(service.addFinished, timeout=5000) as blocker:
        service.add_folders(list(paths))
    return blocker.args[0]


def sidebar_texts(window):
    return window.sidebar.root_texts()


_KEEP_ALIVE = []  # Qt events don't own their mime data: it must outlive the event


def drop_event(cls, paths):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    _KEEP_ALIVE.append(mime)
    return cls(QPoint(50, 50) if cls is QDragEnterEvent else QPointF(50, 50),
               Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)


# -- service --

def test_service_adds_and_lists(qtbot, service, library_dir):
    with qtbot.waitSignal(service.rootsChanged, timeout=5000):
        [result] = add(qtbot, service, library_dir / "Music")
    assert result.status is AddStatus.ADDED
    assert [r.path for r in service.roots()] == [str(library_dir / "Music")]


def test_service_reports_each_path(qtbot, service, library_dir):
    results = add(qtbot, service, library_dir / "Music", library_dir / "Music" / "Artist",
                  library_dir / "Music" / "Artist" / "Album" / "01.flac", library_dir / "missing")
    assert [r.status for r in results] == [AddStatus.ADDED, AddStatus.INSIDE_ROOT, AddStatus.IS_A_FILE,
                                           AddStatus.NOT_A_FOLDER]


def test_service_remove(qtbot, service, library_dir):
    [result] = add(qtbot, service, library_dir / "Music")
    with qtbot.waitSignal(service.rootsChanged, timeout=5000):
        service.remove_root(result.root_id)
    assert service.roots() == []
    assert (library_dir / "Music" / "Artist" / "Album" / "01.flac").exists()


def test_service_notices_offline_roots(qtbot, service, library_dir, tmp_path):
    add(qtbot, service, library_dir / "Rips")
    shutil.move(library_dir / "Rips", tmp_path / "away")
    with qtbot.waitSignal(service.rootsChanged, timeout=5000):
        service.refresh_online()
    assert [r.online for r in service.roots()] == [False]


def test_service_reports_failures(qtbot, service, library_dir, monkeypatch):
    def broken(*_args, **_kwargs):
        raise OSError("disk on fire")

    monkeypatch.setattr(roots_module, "add_root", broken)
    with qtbot.waitSignal(service.failed, timeout=5000) as blocker:
        service.add_folders([library_dir / "Music"])
    assert "disk on fire" in blocker.args[0]


def test_service_keeps_roots_across_restarts(qtbot, tmp_path, library_dir):
    db = tmp_path / "db" / "library.db"
    first = LibraryService(db)
    add(qtbot, first, library_dir / "Music")
    first.close()
    first.close()  # harmless
    second = LibraryService(db)
    assert [r.path for r in second.roots()] == [str(library_dir / "Music")]
    second.close()


# -- window --

def test_add_folder_through_the_picker(qtbot, window, service, library_dir, monkeypatch):
    monkeypatch.setattr(window, "choose_folder", lambda: str(library_dir / "Music"))
    with qtbot.waitSignal(service.addFinished, timeout=5000):
        window.add_folder_action.trigger()
    qtbot.waitUntil(lambda: sidebar_texts(window) == [str(library_dir / "Music")])
    assert window.sidebar.selected_root().path == str(library_dir / "Music")
    assert "Added" in window.statusBar().currentMessage()
    assert not window.sidebar.empty_hint.isVisibleTo(window)


def test_the_tree_shows_while_the_library_is_empty(qtbot, window, service, library_dir):
    """SPEC §33: the tree is hidden by default, but an empty library shows it, with its Add folder… button."""
    assert window.sidebar.isVisibleTo(window) and window.folders_action.isChecked()
    assert not window.folders_action.isEnabled()
    add(qtbot, service, library_dir / "Music")
    qtbot.waitUntil(lambda: not window.sidebar.isVisibleTo(window))
    assert window.folders_action.isEnabled() and not window.folders_action.isChecked()


def test_cancelled_picker_adds_nothing(window, monkeypatch):
    monkeypatch.setattr(window, "choose_folder", lambda: "")
    window.add_folder()
    assert sidebar_texts(window) == []
    assert window.sidebar.empty_hint.isVisibleTo(window)


def test_offline_root_is_dimmed(qtbot, window, service, library_dir, tmp_path):
    add(qtbot, service, library_dir / "Rips")
    shutil.move(library_dir / "Rips", tmp_path / "away")
    with qtbot.waitSignal(service.rootsChanged, timeout=5000):
        service.refresh_online()
    qtbot.waitUntil(lambda: sidebar_texts(window) == [f"{library_dir / 'Rips'}  (offline)"])
    assert "browsable" in window.sidebar.model.index(0, 0).data(Qt.ItemDataRole.ToolTipRole)


def test_remove_asks_first(qtbot, window, service, library_dir, monkeypatch):
    add(qtbot, service, library_dir / "Music")
    qtbot.waitUntil(lambda: len(sidebar_texts(window)) == 1)
    root = service.roots()[0]

    monkeypatch.setattr(window, "confirm_remove", lambda _root: False)
    window.remove_root(root)
    assert len(service.roots()) == 1

    monkeypatch.setattr(window, "confirm_remove", lambda _root: True)
    with qtbot.waitSignal(service.rootsChanged, timeout=5000):
        window.remove_root(root)
    qtbot.waitUntil(lambda: sidebar_texts(window) == [])
    assert "Nothing on disk changed" in window.statusBar().currentMessage()


def test_context_menu_offers_remove(qtbot, window, service, library_dir):
    add(qtbot, service, library_dir / "Music")
    qtbot.waitUntil(lambda: len(sidebar_texts(window)) == 1)
    menu = window.sidebar.context_menu_for(window.sidebar.model.index(0, 0))
    texts = [a.text() for a in menu.actions() if a.text()]
    assert texts[0] == "ReplayGain as an album (−15 LUFS)"  # Explorer's entries first (SPEC §35)
    assert "Open" in texts and texts[-2:] == ["Remove from library", "Add folder…"]
    # "Hide folders without music" is in … > Options…, Browser only (SPEC §12).
    assert [a.text() for a in window.sidebar.context_menu_for(QModelIndex()).actions() if a.text()] == [
        "Add folder…"]


@pytest.mark.filterwarnings("ignore::DeprecationWarning")  # QDragEnterEvent has only the QPoint constructor
def test_drag_shows_the_overlay(window, library_dir):
    event = drop_event(QDragEnterEvent, [library_dir / "Music"])
    window.dragEnterEvent(event)
    assert event.isAccepted() and window.overlay.isVisible()
    window.dragLeaveEvent(None)
    assert not window.overlay.isVisible()


def test_drop_folders(qtbot, window, service, library_dir):
    with qtbot.waitSignal(service.addFinished, timeout=5000):
        window.dropEvent(drop_event(QDropEvent, [library_dir / "Music", library_dir / "Rips"]))
    qtbot.waitUntil(lambda: sidebar_texts(window) == [str(library_dir / "Music"), str(library_dir / "Rips")])
    assert not window.overlay.isVisible()


def test_dropped_files_offer_their_folder(qtbot, window, service, library_dir):
    track = library_dir / "Music" / "Artist" / "Album" / "01.flac"
    with qtbot.waitSignal(service.addFinished, timeout=5000):
        window.dropEvent(drop_event(QDropEvent, [track]))
    qtbot.waitUntil(window.toast.isVisible)
    assert window.toast.button.text() == "Add its folder"
    assert sidebar_texts(window) == []
    with qtbot.waitSignal(service.addFinished, timeout=5000):
        window.toast.button.click()
    qtbot.waitUntil(lambda: sidebar_texts(window) == [str(track.parent)])
    assert not window.toast.isVisible()


def test_window_without_a_library(qtbot):
    window = MainWindow(None)
    qtbot.addWidget(window)
    assert not window.acceptDrops()
    assert not window.add_folder_action.isEnabled()
    assert not window.sidebar.isEnabled()


def test_close_leaves_no_wal_files(tmp_path):
    db = tmp_path / "db" / "library.db"
    LibraryService(db).close()
    assert sorted(p.name for p in db.parent.iterdir()) == ["library.db"]
