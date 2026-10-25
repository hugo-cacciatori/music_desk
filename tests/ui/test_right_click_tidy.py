"""The right-click Tidy with MusicDesk open (SPEC §31, §35): one or several folders make one plan in the Tidy
window, beside the main window, one run (the split with the real bundled flac, ReplayGain with a fake scanner), one
report and one Redundant files window; the main window's index re-reads every tree."""

import shutil

import pytest
from entry_helpers import entry_window
from rsgain_fake import VALUES, FakeScanner

from music_desk.context_menu import request as menu
from music_desk.context_menu import tidy_entry
from music_desk.services.tidy_job import TidyRunner

TRACKS = {**VALUES, "01 One.flac": ("-3.00 dB", "0.5"), "02 Two.flac": ("-2.00 dB", "0.6")}


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "split" / "wav_image", lib / "Rips" / "EAC")
    shutil.copytree(fixtures_dir / "replaygain" / "Fixture Artist" / "Album B", lib / "Rips" / "Album B")
    return lib


@pytest.fixture
def rips(library_dir):
    return library_dir / "Rips"


@pytest.fixture
def fake(monkeypatch):
    def runner(locks, parent=None):
        made = TidyRunner(locks, parent)
        made.scanner = FakeScanner(TRACKS)
        return made
    monkeypatch.setattr(tidy_entry, "TidyRunner", runner)


def plan(qtbot, open_app, paths):
    window = entry_window(qtbot, open_app, menu.TIDY, paths)
    window.recycled = []
    window.recycle = window.recycled.append
    window.check_recycle = lambda _path: None
    qtbot.waitUntil(lambda: window.page.state in ("plan", "error"), timeout=20000)
    return window


def test_several_folders_one_plan_one_run(qtbot, open_app, rips, fake, monkeypatch):
    main = open_app.window
    requested = []
    monkeypatch.setattr(main, "refresh_after_tool", requested.append)
    eac, album_b = rips / "EAC", rips / "Album B"
    window = plan(qtbot, open_app, [eac, album_b])
    page = window.page
    assert window.parent() is None and main.center.currentWidget() is main.file_view  # its own window (SPEC §35)
    assert "2 folders in" in page.heading.text()
    rows = dict(page.rows())
    assert rows["EAC"].startswith("Split image.cue into 2 tracks")
    assert "Album B" in rows
    page.run_button.click()
    qtbot.waitUntil(lambda: window.offer is not None and window.offer.isVisible(), timeout=60000)
    assert (eac / "01 One.flac").exists()
    offer = window.offer  # one Redundant files window: the image and its sheet
    qtbot.addWidget(offer)
    assert {"image.wav", "image.cue"} <= {item.path.name for item in offer.items}
    assert sorted(requested) == sorted([eac, album_b])  # the main window's index re-reads every tree
    offer.close()


def test_a_folder_in_use_is_refused_at_run(qtbot, open_app, service, rips, fake):
    window = plan(qtbot, open_app, [rips / "EAC", rips / "Album B"])
    lock = service.locks.acquire([rips / "Album B"], "Split")
    window.page.run_button.click()
    assert "is busy: Split is running on it" in window.page.message.text() and window.page.state == "plan"
    service.locks.release(lock)


def test_the_entry_parses():
    assert menu.parse_arguments(["--tidy", r"D:\a", r"D:\b"]) == menu.StartRequest(menu.TIDY, (r"D:\a", r"D:\b"))
