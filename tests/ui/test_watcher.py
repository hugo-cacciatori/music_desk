"""Live updates (SPEC §4): the watcher's debounce and coalescing, its fallback, and end-to-end index updates."""

import os
import shutil
import threading
import time

import pytest

from music_desk.services.library import LibraryService
from music_desk.services.watcher import Watcher, _coalesce

FAST = {"debounce": 0.1, "max_delay": 1.0}


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []
        self.lock = threading.Lock()

    def __call__(self, path: str, recursive: bool) -> None:
        with self.lock:
            self.calls.append((path, recursive))

    def folders(self) -> set[str]:
        with self.lock:
            return {os.path.normcase(p) for p, _r in self.calls}


@pytest.fixture
def recorder():
    return Recorder()


@pytest.fixture
def watcher(qtbot, recorder):
    watcher = Watcher(recorder, **FAST)
    yield watcher
    watcher.stop()


def key(path) -> str:
    return os.path.normcase(str(path))


# -- unit --

def test_coalesce_drops_folders_under_a_recursive_scan():
    batch = [(r"C:\M\A\B", False), (r"C:\M\A", True), (r"C:\M", False), (r"C:\M\AB", False), (r"C:\M\a\c", False)]
    assert _coalesce(batch) == [(r"C:\M", False), (r"C:\M\A", True), (r"C:\M\AB", False)]


def test_changes_are_debounced_per_folder(qtbot, recorder, tmp_path):
    # Its own, longer debounce: at FAST's 0.1 s, a pause between two of the 40 writes on a busy machine (the whole
    # suite on 12 workers) split the burst into 3 flushes (measured 2026-10-24). The burst itself is the point here.
    watcher = Watcher(recorder, debounce=0.5, max_delay=5.0)
    root = tmp_path / "root"
    (root / "Album").mkdir(parents=True)
    watcher.sync({1: str(root)})
    assert watcher.watched == {1: str(root)}
    for n in range(40):
        (root / "Album" / f"{n:02d}.txt").write_text("x")
    qtbot.waitUntil(lambda: key(root / "Album") in recorder.folders(), timeout=10000)
    time.sleep(1.0)
    watcher.stop()
    album_calls = [c for c in recorder.calls if key(c[0]) == key(root / "Album")]
    assert len(album_calls) <= 2  # 40 files, one flush (two at most if the burst straddled a flush)


def test_new_folders_are_scanned_recursively(qtbot, watcher, recorder, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    watcher.sync({1: str(root)})
    (root / "New").mkdir()
    qtbot.waitUntil(lambda: (str(root / "New"), True) in recorder.calls, timeout=5000)


def test_moves_touch_both_folders(qtbot, watcher, recorder, tmp_path):
    root = tmp_path / "root"
    (root / "A").mkdir(parents=True)
    (root / "B").mkdir()
    (root / "A" / "x.txt").write_text("x")
    watcher.sync({1: str(root)})
    os.rename(root / "A" / "x.txt", root / "B" / "x.txt")
    qtbot.waitUntil(lambda: {key(root / "A"), key(root / "B")} <= recorder.folders(), timeout=5000)


def test_sync_unwatches(qtbot, watcher, recorder, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    watcher.sync({1: str(root)})
    watcher.sync({})
    assert watcher.watched == {}
    (root / "after.txt").write_text("x")
    time.sleep(0.5)
    assert recorder.calls == []


def test_watched_folders_can_still_be_renamed(watcher, tmp_path):
    """The watch must not lock the library: Explorer and the app's Rename tool rename folders under it."""
    root = tmp_path / "root"
    (root / "Album").mkdir(parents=True)
    watcher.sync({1: str(root)})
    os.rename(root / "Album", root / "Album (2024)")
    assert (root / "Album (2024)").is_dir()


def test_watch_failure_falls_back(qtbot, recorder, tmp_path):
    class BrokenObserver:
        def start(self): ...
        def stop(self): ...
        def join(self, _timeout=None): ...

        def schedule(self, *_args, **_kwargs):
            raise OSError("not supported on this share")

    watcher = Watcher(recorder, observer_factory=BrokenObserver, **FAST)
    try:
        with qtbot.waitSignal(watcher.unwatched, timeout=2000) as blocker:
            watcher.sync({7: str(tmp_path)})
        assert blocker.args == [7, "not supported on this share"]
        assert watcher.watched == {}
    finally:
        watcher.stop()


# -- end to end: file changes reach the index --

@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    (lib / "Album").mkdir(parents=True)
    shutil.copy2(fixtures_dir / "formats" / "tone.flac", lib / "Album" / "01 One.flac")
    return lib


@pytest.fixture
def service(qtbot, tmp_path, library_dir):
    service = LibraryService(tmp_path / "db" / "library.db", watcher_options=FAST)
    with qtbot.waitSignal(service.indexer.idle, timeout=20000):
        with qtbot.waitSignal(service.addFinished, timeout=5000):
            service.add_folders([library_dir])
    qtbot.waitUntil(lambda: bool(service.watcher.watched), timeout=5000)
    yield service
    service.close()


def names(service) -> set[str]:
    return {row[0] for row in service.conn.execute("SELECT name FROM entries WHERE parent_id IS NOT NULL")}


def rename_retrying(src, dst, timeout: float = 2.0) -> None:
    """os.rename, retried while something briefly holds a file under the folder (see test_live_folder_changes)."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.rename(src, dst)
            return
        except PermissionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.01)


def test_live_file_changes(qtbot, service, library_dir, fixtures_dir):
    shutil.copy2(fixtures_dir / "formats" / "tone.mp3", library_dir / "Album" / "02 Two.mp3")
    qtbot.waitUntil(lambda: service.conn.execute(
        "SELECT count(*) FROM tracks t JOIN entries e ON e.id = t.entry_id WHERE e.name = '02 Two.mp3'"
    ).fetchone()[0] == 1, timeout=10000)

    os.rename(library_dir / "Album" / "01 One.flac", library_dir / "Album" / "01 First.flac")
    qtbot.waitUntil(lambda: "01 First.flac" in names(service) and "01 One.flac" not in names(service),
                    timeout=10000)

    (library_dir / "Album" / "02 Two.mp3").unlink()
    qtbot.waitUntil(lambda: "02 Two.mp3" not in names(service), timeout=10000)


def test_live_folder_changes(qtbot, service, library_dir, fixtures_dir):
    new = library_dir / "Artist" / "New Album"
    new.mkdir(parents=True)
    shutil.copy2(fixtures_dir / "formats" / "tone.ogg", new / "01.ogg")
    qtbot.waitUntil(lambda: {"Artist", "New Album", "01.ogg"} <= names(service), timeout=10000)
    # The names reach the index before the tags are read, and Windows refuses to rename a folder while a file
    # under it is open, even with FILE_SHARE_DELETE (SPEC §7). Measured 2026-10-16: 7 runs in 100 hit the app's
    # own tag read of 01.ogg here (a 4-5 ms lock, ending as the read closed); 0 in 100 with tag reading
    # stubbed out. So wait for the read, and retry briefly in case a later rescan reads it again.
    qtbot.waitUntil(lambda: service.conn.execute(
        "SELECT count(*) FROM tracks t JOIN entries e ON e.id = t.entry_id WHERE e.name = '01.ogg'"
    ).fetchone()[0] == 1, timeout=10000)
    rename_retrying(library_dir / "Artist", library_dir / "Artist (moved)")
    # An outside rename looks like a delete plus a create: the parent is reconciled, then the moved folder's
    # subtree is scanned again (renames made in the app will update the index directly).
    qtbot.waitUntil(lambda: {"Artist (moved)", "New Album", "01.ogg"} <= names(service)
                    and "Artist" not in names(service), timeout=10000)

    shutil.rmtree(library_dir / "Artist (moved)")
    qtbot.waitUntil(lambda: not ({"Artist (moved)", "New Album", "01.ogg"} & names(service)), timeout=10000)


def test_revalidation_on_open_without_a_watcher(qtbot, tmp_path, library_dir):
    class BrokenObserver:
        def start(self): ...
        def stop(self): ...
        def join(self, _timeout=None): ...

        def schedule(self, *_args, **_kwargs):
            raise OSError("no change notifications here")

    service = LibraryService(tmp_path / "db" / "library.db",
                             watcher_options={**FAST, "observer_factory": BrokenObserver})
    messages = []
    service.failed.connect(messages.append)
    try:
        with qtbot.waitSignal(service.indexer.idle, timeout=20000):
            service.add_folders([library_dir])
        qtbot.waitUntil(lambda: any("Live updates are off" in m for m in messages), timeout=5000)
        album = service.conn.execute("SELECT id FROM entries WHERE name = 'Album'").fetchone()[0]
        (library_dir / "Album" / "cover.jpg").write_bytes(b"jpg")
        time.sleep(0.3)
        assert "cover.jpg" not in names(service)  # nobody is watching
        with qtbot.waitSignal(service.indexer.folderUpdated, timeout=10000,
                              check_params_cb=lambda entry_id: entry_id == album):
            service.folder_opened(album)
        assert "cover.jpg" in names(service)
    finally:
        service.close()
