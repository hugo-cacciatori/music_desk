"""The right-click Tidy with MusicDesk open (SPEC §31): one or several folders make one plan on the Tidy page, one
run (the app's Tidy job: the split with the real bundled flac, ReplayGain with a fake scanner), one report and one
Redundant files window."""

import shutil

import pytest
from rsgain_fake import VALUES, FakeScanner

from music_desk.app import Starter
from music_desk.context_menu import request as menu
from music_desk.settings import Settings

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
def tidy(window):
    controller = window.tidy
    controller.runner.scanner = FakeScanner(TRACKS)
    controller.recycled = []
    controller.recycle = controller.recycled.append
    controller.check_recycle = lambda _path: None
    return controller


def plan(qtbot, window, paths):
    assert window.tools.run_entry(menu.TIDY, [str(p) for p in paths])
    page = window.tidy.page
    assert window.center.currentWidget() is page
    qtbot.waitUntil(lambda: page.state in ("plan", "error"), timeout=20000)
    return page


def test_several_folders_one_plan_one_run(qtbot, window, rips, tidy):
    eac, album_b = rips / "EAC", rips / "Album B"
    page = plan(qtbot, window, [eac, album_b])
    assert "2 folders in" in page.heading.text()
    rows = dict(page.rows())
    assert rows["EAC"].startswith("Split image.cue into 2 tracks")
    assert "Album B" in rows
    page.run_button.click()
    qtbot.waitUntil(lambda: page.state in ("result", "error") and tidy.job is None, timeout=60000)
    assert (eac / "01 One.flac").exists()
    [job] = window.jobs.jobs
    assert job.kind == "Tidy" and job.state == "done"
    offer = tidy.offer
    assert offer is not None and offer.isVisible()  # one Redundant files window: the image and its sheet, the Scans
    names = {item.path.name for item in offer.items}
    assert {"image.wav", "image.cue"} <= names
    offer.close()


def test_one_folder_is_tidy_on_it(qtbot, window, rips, tidy):
    page = plan(qtbot, window, [rips / "EAC"])
    assert page.heading.text().endswith(str(rips / "EAC"))
    assert list(dict(page.rows())) == ["EAC"]


def test_a_folder_in_use_is_refused_at_run(qtbot, window, service, rips, tidy):
    page = plan(qtbot, window, [rips / "EAC", rips / "Album B"])
    lock = service.locks.acquire([rips / "Album B"], "the Split tool")
    page.run_button.click()
    assert "is in use by the Split tool" in window.toast.label.text() and tidy.job is None
    service.locks.release(lock)


def test_options_needing_a_fix_refuse_the_plan(qtbot, window, rips, tidy, monkeypatch):
    monkeypatch.setattr(window.tools.cards, "problems", lambda tool: ["a mask is empty"])
    assert not window.tools.run_entry(menu.TIDY, [str(rips)])
    assert "fix the File names, Rename or ReplayGain options first" in window.statusBar().currentMessage()


def test_the_starter_hands_tidy_to_the_open_window(qtbot, service, rips):
    starter = Starter(Settings(), lambda: service, check_tools=False)
    starter.open_folder(None)
    window = starter.window
    qtbot.addWidget(window)
    starter.run_entry(menu.TIDY, [str(rips / "Album B")])
    qtbot.waitUntil(lambda: window.tidy.page.state == "plan", timeout=20000)


def test_the_entry_parses():
    assert menu.parse_arguments(["--tidy", r"D:\a", r"D:\b"]) == menu.StartRequest(menu.TIDY, (r"D:\a", r"D:\b"))
