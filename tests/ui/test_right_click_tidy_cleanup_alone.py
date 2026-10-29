"""The right-click Tidy and Clean up with Refrain not open (SPEC §31): the Tidy page alone in its own window, then
the Redundant files window; Clean up's Redundant files window, or a small "nothing found" one, alone. No main
window; the process ends when the last entry window closes. The Recycle Bin is a recorder."""

import shutil
import threading

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from rsgain_fake import VALUES, FakeScanner

from refrain.app import Starter
from refrain.context_menu import request as menu
from refrain.context_menu import tidy_entry
from refrain.context_menu.cleanup_entry import CleanupEntry
from refrain.context_menu.tidy_entry import TidyWindow
from refrain.services.tidy_job import TidyRunner
from refrain.settings import Settings

TRACKS = {**VALUES, "01 One.flac": ("-3.00 dB", "0.5"), "02 Two.flac": ("-2.00 dB", "0.6")}
ALBUM = "Fixture Artist - Fixture Album"


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "split" / "wav_image", lib / "Rips" / "EAC")
    shutil.copytree(fixtures_dir / "replaygain" / "Fixture Artist" / "Album B", lib / "Rips" / "Album B")
    shutil.copytree(fixtures_dir / "cleanup", lib / "Cleanup")
    shutil.copytree(fixtures_dir / "album" / ALBUM, lib / "Artist" / ALBUM)
    return lib


@pytest.fixture
def rips(library_dir):
    return library_dir / "Rips"


@pytest.fixture
def quits(monkeypatch):
    calls = []
    monkeypatch.setattr("refrain.app.QApplication.quit", lambda: calls.append(True))
    return calls


@pytest.fixture
def fake(monkeypatch):
    scanner = FakeScanner(TRACKS)

    def runner(locks, parent=None):
        made = TidyRunner(locks, parent)
        made.scanner = scanner
        return made
    monkeypatch.setattr(tidy_entry, "TidyRunner", runner)
    return scanner


@pytest.fixture
def starter(qtbot, service):
    starter = Starter(Settings(), lambda: service, check_tools=False)
    yield starter
    starter.release()  # the entries' windows and the cards made without a main window, freed while Qt runs
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


# -- Tidy --

def tidy_window(qtbot, starter, paths) -> TidyWindow:
    starter.run_entry(menu.TIDY, [str(p) for p in paths])
    window = starter.entry_windows[-1]
    assert isinstance(window, TidyWindow)
    qtbot.addWidget(window)
    window.recycled = []
    window.recycle = window.recycled.append
    window.check_recycle = lambda _path: None
    qtbot.waitUntil(lambda: window.page.state in ("plan", "error"), timeout=20000)
    return window


def test_tidy_alone_plans_runs_then_offers(qtbot, starter, rips, fake, quits):
    window = tidy_window(qtbot, starter, [rips / "EAC", rips / "Album B"])
    page = window.page
    assert starter.window is None and window.isVisible()  # no main window
    assert page.back_button.text() == "Close" and "2 folders in" in page.heading.text()
    page.run_button.click()
    assert not page.back_button.isVisible()  # while it runs: Stop, then Done
    qtbot.waitUntil(lambda: page.state in ("result", "error"), timeout=60000)
    assert page.state == "result" and (rips / "EAC" / "01 One.flac").exists()
    offer = window.offer
    assert offer is not None and offer.isVisible()  # the Redundant files window follows
    assert {"image.wav", "image.cue"} <= {item.path.name for item in offer.marked()}
    page.done_button.click()
    assert quits == [] and starter.entry_windows  # the Redundant files window is still open
    offer.delete_button.click()
    qtbot.waitUntil(lambda: offer.state == "report", timeout=10000)
    assert {"image.wav", "image.cue"} <= {path.name for path in window.recycled}
    offer.close()
    assert starter.entry_windows == [] and quits == [True]


def test_tidy_alone_close_from_the_plan_changes_nothing(qtbot, starter, rips, fake, quits):
    window = tidy_window(qtbot, starter, [rips / "EAC"])
    window.page.back_button.click()
    assert not (rips / "EAC" / "01 One.flac").exists() and quits == [True]


def test_tidy_alone_refuses_options_needing_a_fix(qtbot, starter, rips, fake, quits, monkeypatch):
    monkeypatch.setattr(starter.cards(), "problems", lambda tool: ["a mask is empty"])
    window = tidy_window(qtbot, starter, [rips])
    assert window.page.state == "error" and "Refrain > Settings" in window.page.message.text()


def test_tidy_alone_closing_while_running_asks(qtbot, starter, rips, quits, monkeypatch):
    gate = threading.Event()
    scanner = FakeScanner(TRACKS, before=lambda folder: gate.wait(20))

    def runner(locks, parent=None):
        made = TidyRunner(locks, parent)
        made.scanner = scanner
        return made
    monkeypatch.setattr(tidy_entry, "TidyRunner", runner)
    window = tidy_window(qtbot, starter, [rips / "Album B"])
    window.page.run_button.click()
    asked = []
    window.confirm_stop = lambda: asked.append(True) or False
    window.close()
    assert asked == [True] and window.isVisible()
    gate.set()
    qtbot.waitUntil(lambda: window.page.state == "result", timeout=60000)


# -- Clean up --

def cleanup_entry(qtbot, starter, paths) -> CleanupEntry:
    starter.run_entry(menu.CLEANUP, [str(p) for p in paths])
    entry = starter.entry_windows[-1]
    assert isinstance(entry, CleanupEntry)
    entry.recycled = []
    entry.recycle = entry.recycled.append
    entry.check_recycle = lambda _path: None
    qtbot.waitUntil(lambda: entry.offer is not None or entry.notice is not None, timeout=10000)
    for window in (entry.offer, entry.notice):
        if window is not None:
            qtbot.addWidget(window)
    return entry


def test_clean_up_alone_opens_the_redundant_files_window(qtbot, starter, library_dir, quits):
    lrc, cue = library_dir / "Cleanup" / "lrc", library_dir / "Cleanup" / "per_track_cue"
    entry = cleanup_entry(qtbot, starter, [lrc, cue])
    offer = entry.offer
    assert offer is not None and offer.isVisible() and starter.window is None
    assert {item.path.name for item in offer.marked()} == {"no_lyrics.lrc", "with_lyrics.lrc", "album.cue",
                                                          "missing.cue"}
    offer.close()  # closing keeps them all
    assert entry.recycled == [] and starter.entry_windows == [] and quits == [True]


def test_clean_up_alone_nothing_found(qtbot, starter, library_dir, quits):
    album = library_dir / "Artist" / ALBUM
    for junk in album.rglob("*"):
        if junk.suffix.lower() in (".cue", ".nfo", ".sfv", ".txt", ".lrc"):
            junk.unlink()
    entry = cleanup_entry(qtbot, starter, [album])
    assert entry.offer is None and entry.notice is not None and entry.notice.isVisible()
    assert entry.notice.message.text() == f"Nothing to clean up in {ALBUM}."
    entry.notice.close_button.click()
    assert starter.entry_windows == [] and quits == [True]


def test_clean_up_alone_refuses_options_needing_a_fix(qtbot, starter, library_dir, quits, monkeypatch):
    monkeypatch.setattr(starter.cards(), "problems", lambda tool: ["an extension is wrong"])
    entry = cleanup_entry(qtbot, starter, [library_dir / "Cleanup"])
    assert entry.notice is not None and "Refrain > Settings" in entry.notice.message.text()


def test_a_main_window_meanwhile_leaves_entries_their_own_windows(qtbot, starter, service, library_dir, quits):
    entry = cleanup_entry(qtbot, starter, [library_dir / "Cleanup" / "lrc"])
    starter.open_folder(None)
    main = starter.window
    qtbot.addWidget(main)
    assert entry.offer is not None
    entry.offer.close()
    assert quits == []  # the main window stays, as the app
    starter.run_entry(menu.TIDY, [str(library_dir / "Rips" / "Album B")])
    tidy = starter.entry_windows[-1]  # its own window still (SPEC §35)
    qtbot.addWidget(tidy)
    assert isinstance(tidy, TidyWindow) and main.center.currentWidget() is main.file_view
    qtbot.waitUntil(lambda: tidy.page.state == "plan", timeout=20000)
