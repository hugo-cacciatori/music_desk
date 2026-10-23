"""Shared UI fixtures: a small copied library, indexed, and a main window on it. Test modules that need another
library define their own `library_dir`, `service` or `window`, which take precedence."""

import shutil

import pytest

from music_desk.services.library import LibraryService
from music_desk.ui.main_window import MainWindow

ALBUM = "Fixture Artist - Fixture Album"


@pytest.fixture(autouse=True)
def lyrics_offline(monkeypatch):
    """Libraries built by UI tests make no LRCLIB lookups (tests/ui/test_lyrics_service.py scripts its own)."""
    monkeypatch.setattr(LibraryService, "lyrics_offline_default", True)


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    album = lib / "Artist" / "Album"
    album.mkdir(parents=True)
    for name in ("Track 10.flac", "Track 2.flac", "track 1.flac"):
        shutil.copy2(fixtures_dir / "formats" / "tone.flac", album / name)
    shutil.copy2(fixtures_dir / "formats" / "tone.mp3", album / "Track 3.mp3")
    (album / "cover.jpg").write_bytes(b"jpg")
    (album / "CD 10").mkdir()
    (album / "CD 2").mkdir()
    (lib / "Artist" / "Scans").mkdir()
    (lib / "Artist" / "Scans" / "back.jpg").write_bytes(b"jpg")
    shutil.copytree(fixtures_dir / "album" / ALBUM, lib / "Other" / ALBUM)
    return lib


@pytest.fixture
def service(qtbot, tmp_path, library_dir):
    service = LibraryService(tmp_path / "db" / "library.db", watcher_options={"debounce": 0.1, "max_delay": 1})
    with qtbot.waitSignal(service.indexer.idle, timeout=20000):
        with qtbot.waitSignal(service.addFinished, timeout=5000):
            service.add_folders([library_dir])
    yield service
    service.close()


@pytest.fixture
def window(qtbot, service):
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    return window
