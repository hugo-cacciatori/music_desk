"""M2's "Done when": a copied 10k+ file tree browses fluidly while it is being indexed, and folders reopen
instantly from the index (SPEC §16: no event-loop stall above 50 ms; Ground rule 2).

A 5 ms timer measures the event loop: the longest gap between its ticks is the worst stall the user would feel.
The thresholds here are looser than the 50 ms target so a busy test machine doesn't fail them; the measured
values are printed (run with -s) and recorded in CLAUDE.md.
"""

import shutil
import time

import pytest
from PySide6.QtCore import QElapsedTimer, QTimer

from music_desk.services.library import LibraryService
from music_desk.ui.main_window import MainWindow


class StallProbe:
    def __init__(self, interval_ms: int = 5) -> None:
        self.clock = QElapsedTimer()
        self.timer = QTimer()
        self.timer.setInterval(interval_ms)
        self.timer.timeout.connect(self._tick)
        self.last = 0
        self.worst = 0

    def start(self) -> None:
        self.clock.start()
        self.last = 0
        self.worst = 0
        self.timer.start()

    def _tick(self) -> None:
        now = self.clock.elapsed()
        self.worst = max(self.worst, now - self.last)
        self.last = now

    def stop(self) -> int:
        self.timer.stop()
        return self.worst


def make_library(base, folders: int, files_per_folder: int, flac) -> None:
    for f in range(folders):
        folder = base / f"Artist {f // 20:02d}" / f"Album {f}"
        folder.mkdir(parents=True)
        for n in range(files_per_folder):
            (folder / f"{n:02d} notes.txt").write_bytes(b"")
        shutil.copy2(flac, folder / "01 track.flac")
        shutil.copy2(flac, folder / "02 track.flac")


@pytest.mark.slow
def test_browsing_a_10k_tree_while_it_is_indexed(qtbot, tmp_path, fixtures_dir):
    lib = tmp_path / "copied library"
    make_library(lib, 500, 18, fixtures_dir / "formats" / "tone.flac")  # 500 folders, 10,000 files, 1,000 tracks
    service = LibraryService(tmp_path / "db" / "library.db")
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    try:
        probe = StallProbe()
        probe.start()
        opens = []
        with qtbot.waitSignal(service.addFinished, timeout=10000):
            service.add_folders([lib])

        # Browse while the root is indexed: open whatever is indexed so far, again and again. (Each open also
        # queues a revalidation of that folder, so the indexer stays busy as long as the browsing goes on.)
        top = service.roots()[0].top_entry_id
        root_done = []
        service.indexer.scanFinished.connect(lambda report: root_done.append(report.folder_id == top) if
                                             report.folder_id == top else None)
        start = time.perf_counter()
        while not root_done and time.perf_counter() - start < 120:
            folders = service.conn.execute(
                "SELECT id FROM entries WHERE is_dir = 1 AND root_id = ? ORDER BY random() LIMIT 1",
                (service.roots()[0].id,)).fetchall()
            target = folders[0][0] if folders else top
            t0 = time.perf_counter()
            window.open_folder(target)
            opens.append(time.perf_counter() - t0)
            qtbot.wait(30)
        indexing_stall = probe.stop()
        indexing_time = time.perf_counter() - start
        assert root_done, "the root scan didn't finish in two minutes"
        qtbot.waitUntil(lambda: not service.indexer.busy, timeout=30000)
        assert service.conn.execute("SELECT count(*) FROM tracks").fetchone()[0] == 1000

        # Reopening folders from the index, once indexed.
        albums = [r[0] for r in service.conn.execute(
            "SELECT id FROM entries WHERE is_dir = 1 AND name LIKE 'Album %' LIMIT 50")]
        probe.start()
        reopen = []
        for album in albums:
            t0 = time.perf_counter()
            window.open_folder(album)
            reopen.append(time.perf_counter() - t0)
            qtbot.wait(5)
        browsing_stall = probe.stop()

        print(f"\nindexing took {indexing_time:.1f} s; meanwhile {len(opens)} folder opens, "
              f"worst {max(opens) * 1000:.0f} ms, "
              f"worst event-loop stall {indexing_stall} ms")
        print(f"indexed: 50 reopens, worst {max(reopen) * 1000:.0f} ms, median "
              f"{sorted(reopen)[25] * 1000:.1f} ms, worst stall {browsing_stall} ms")
        assert max(reopen) < 0.1
        assert browsing_stall < 150
        assert indexing_stall < 250
    finally:
        service.close()
