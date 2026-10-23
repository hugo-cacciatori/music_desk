"""Refreshes the user asks for come first, even while a long background scan runs (Hugo's report, 2026-10-15):
F5, a tool's results, renames and tag saves used to wait behind the startup rescan of every root."""

import threading
import time

import pytest
from browser_helpers import column, entry

from music_desk.core.index.scanner import Scanner


@pytest.fixture
def slow_root_scan(service, monkeypatch, library_dir):
    """Holds a root rescan running: every folder's reconcile waits on a gate, so the scan can't finish until the
    test releases it. Serving a folder the user asks for doesn't wait (it reconciles one folder on request)."""
    gate = threading.Event()
    real = Scanner._reconcile

    def reconcile(self, ctx, ref, path, batch):
        if getattr(threading.current_thread(), "_slow", False) and not getattr(ctx, "_served", False):
            gate.wait(0.05)
        return real(self, ctx, ref, path, batch)

    real_serve = Scanner._serve_folder

    def serve(self, ctx, entry_id, notify=False):
        ctx._served = True
        try:
            return real_serve(self, ctx, entry_id, notify)
        finally:
            ctx._served = False

    monkeypatch.setattr(Scanner, "_reconcile", reconcile)
    monkeypatch.setattr(Scanner, "_serve_folder", serve)
    for n in range(80):  # scanned before Artist's subfolders (breadth first, "Aaa" sorts first): seconds of work
        (library_dir / "Aaa Many" / f"Folder {n}").mkdir(parents=True)
    service.indexer._thread._slow = True
    root = service.roots()[0].id
    service.indexer.scan_root(root)
    deadline = time.monotonic() + 5
    while service.indexer._current != ("root", root) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert service.indexer._current == ("root", root)
    yield
    gate.set()
    service.indexer._thread._slow = False


def test_f5_is_served_during_a_root_scan(qtbot, window, service, library_dir, slow_root_scan):
    album = entry(service, "Artist", "Album")
    window.open_folder(album)
    qtbot.wait(500)  # the revalidation on open is served first; the file appears only after it
    (library_dir / "Artist" / "Album" / "replaygain.log").write_text("log", encoding="utf-8")
    window.rescan_folder()
    qtbot.waitUntil(lambda: "replaygain.log" in column(window, "name"), timeout=3000)
    assert service.indexer.busy  # the root scan is still running: F5 didn't wait for it


def test_a_tool_refresh_covers_the_subtree_during_a_root_scan(qtbot, window, service, library_dir,
                                                              slow_root_scan):
    window.open_folder(entry(service, "Artist"))
    qtbot.wait(500)
    (library_dir / "Artist" / "Album" / "CD 2" / "replaygain.log").write_text("log", encoding="utf-8")
    window.refresh_after_tool(library_dir / "Artist")
    cd2 = entry(service, "Artist", "Album", "CD 2")
    qtbot.waitUntil(lambda: service.conn.execute(
        "SELECT 1 FROM entries WHERE parent_id = ? AND name = 'replaygain.log'", (cd2,)).fetchone() is not None,
        timeout=3000)
    assert service.indexer.busy
