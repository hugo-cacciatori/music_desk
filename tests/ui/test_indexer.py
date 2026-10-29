"""The indexer service and its wiring: scans after adds and at startup, progress, priority folders (SPEC §4)."""

import shutil

import pytest

from refrain.core.index.store import DbWriter
from refrain.services.indexer import Indexer
from refrain.services.library import LibraryService
from refrain.ui.main_window import MainWindow

ALBUM = "Fixture Artist - Fixture Album"


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "album" / ALBUM, lib / "Fixture Artist" / ALBUM)
    return lib


@pytest.fixture
def db(tmp_path):
    return tmp_path / "db" / "library.db"


def count(service, table):
    return service.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def add_and_index(qtbot, service, path):
    with qtbot.waitSignal(service.indexer.idle, timeout=20000):
        with qtbot.waitSignal(service.addFinished, timeout=5000):
            service.add_folders([path])


def test_adding_a_root_indexes_it(qtbot, db, library_dir):
    service = LibraryService(db)
    try:
        add_and_index(qtbot, service, library_dir)
        files = [p for p in library_dir.rglob("*") if p.is_file()]
        audio = [p for p in files if p.suffix.lower() in {".flac", ".mp3"}]
        assert count(service, "entries") == len(list(library_dir.rglob("*"))) + 1  # + the top entry
        assert count(service, "tracks") == len(audio)
        assert service.roots()[0].last_scan is not None
    finally:
        service.close()


def test_startup_rescan_catches_up(qtbot, db, library_dir, fixtures_dir):
    service = LibraryService(db)
    add_and_index(qtbot, service, library_dir)
    tracks = count(service, "tracks")
    service.close()

    shutil.copy2(fixtures_dir / "formats" / "tone.opus", library_dir / "new.opus")
    service = LibraryService(db)
    try:
        qtbot.waitUntil(lambda: count(service, "tracks") == tracks + 1, timeout=20000)
    finally:
        service.close()


def test_window_shows_progress(qtbot, db, library_dir):
    service = LibraryService(db)
    window = MainWindow(service)
    qtbot.addWidget(window)
    try:
        texts = []
        service.indexer.progress.connect(lambda _p: texts.append(window.index_label.text()))
        add_and_index(qtbot, service, library_dir)
        assert texts and texts[-1].startswith("Indexed lib:")
        assert not window.index_label.isVisible()  # hidden once the indexer is idle
    finally:
        service.close()


def test_progress_is_throttled(qtbot, tmp_path, db):
    lib = tmp_path / "many"
    for n in range(300):
        (lib / f"folder {n}").mkdir(parents=True)
    service = LibraryService(db)
    try:
        received = []
        service.indexer.progress.connect(received.append)
        add_and_index(qtbot, service, lib)
        assert received[-1].phase == "done"
        assert len(received) < 50  # 300 folders, but at most 10 updates a second plus phase changes
    finally:
        service.close()


def test_request_folder_when_idle(qtbot, db, library_dir, fixtures_dir):
    service = LibraryService(db)
    try:
        add_and_index(qtbot, service, library_dir)
        album = service.conn.execute("SELECT id FROM entries WHERE name = ?", (ALBUM,)).fetchone()[0]
        shutil.copy2(fixtures_dir / "formats" / "tone.ogg", library_dir / "Fixture Artist" / ALBUM / "new.ogg")
        before = count(service, "tracks")
        with qtbot.waitSignal(service.indexer.folderUpdated, timeout=10000,
                              check_params_cb=lambda entry_id: entry_id == album):
            service.indexer.request_folder(album)
        assert count(service, "tracks") == before + 1
    finally:
        service.close()


def test_removing_a_root_while_it_is_indexed(qtbot, tmp_path, db):
    lib = tmp_path / "big"
    for n in range(2000):
        (lib / f"a{n // 100}" / f"folder {n}").mkdir(parents=True)
    service = LibraryService(db)
    failures = []
    service.indexer.failed.connect(failures.append)
    try:
        with qtbot.waitSignal(service.addFinished, timeout=5000) as blocker:
            service.add_folders([lib])
        root_id = blocker.args[0][0].root_id
        with qtbot.waitSignal(service.rootsChanged, timeout=5000):
            service.remove_root(root_id)
        qtbot.waitUntil(lambda: not service.indexer.busy, timeout=20000)
        assert service.roots() == []
        assert count(service, "entries") == 0
        assert failures == []
    finally:
        service.close()


def test_indexer_stops_cleanly(tmp_path):
    with DbWriter(tmp_path / "library.db") as writer:
        indexer = Indexer(writer)
        assert not indexer.busy
        indexer.stop()
        indexer.scan_root(1)  # ignored once stopped
        assert not indexer.busy
