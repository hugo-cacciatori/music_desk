"""Tidy in the window (SPEC §28): the Tidy card opens the whole plan, one Run Tidy button runs it album by album
(the split with the real bundled flac, the renames, ReplayGain with a fake scanner), one Tidy job, and the offer
window (Keep by default, Apply recycles only the files marked Delete, closing keeps all; the Recycle Bin is a
recorder)."""

import shutil
import threading

import pytest
from rsgain_fake import VALUES, FakeScanner

from music_desk.core.index.store import path_of
from music_desk.core.replaygain import ALBUM_GAIN, TRACK_GAIN
from music_desk.core.tags import TaggedFile
from music_desk.ui.tidy_offer import DELETE, KEEP
from music_desk.ui.tools.tools_page import TIDY

TRACKS = {**VALUES, "01 One.flac": ("-3.00 dB", "0.5"), "02 Two.flac": ("-2.00 dB", "0.6")}


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "split" / "wav_image", lib / "Rips" / "EAC")
    shutil.copytree(fixtures_dir / "replaygain" / "Fixture Artist" / "Album B", lib / "Rips" / "Album B")
    shutil.copytree(fixtures_dir / "split" / "data_track", lib / "Odd")  # a sheet that needs a look
    return lib


@pytest.fixture
def tidy(window):
    controller = window.tidy
    controller.runner.scanner = FakeScanner(TRACKS)
    controller.recycled = []
    controller.recycle = controller.recycled.append
    controller.check_recycle = lambda _path: None
    controller.runner.recycle = controller.recycled.append
    return controller


def folder_id(service, path) -> int:
    conn = service.conn
    return next(i for (i,) in conn.execute("SELECT id FROM entries WHERE is_dir = 1") if path_of(conn, i) == path)


def open_plan(qtbot, window, service, folder):
    window.open_folder(folder_id(service, folder))
    window.tools.open([])
    window.tools_page.request(TIDY)
    assert window.center.currentWidget() is window.tidy.page
    qtbot.waitUntil(lambda: window.tidy.page.state in ("plan", "error"), timeout=20000)
    return window.tidy.page


def run(qtbot, window):
    window.tidy.page.run_button.click()
    qtbot.waitUntil(lambda: window.tidy.page.state in ("result", "error"), timeout=60000)
    return window.tidy.page.log.toPlainText()


def test_the_plan_lists_every_album_and_one_run_button(qtbot, window, service, library_dir, tidy):
    (library_dir / "Rips" / "Album B" / "01 Loud.flac").rename(library_dir / "Rips" / "Album B" / "loud.flac")
    page = open_plan(qtbot, window, service, library_dir / "Rips")
    rows = dict(page.rows())
    assert rows["EAC"] == "Split image.cue into 2 tracks, then ReplayGain (after the split)"
    assert rows["Album B"] == "Rename 1 file, then ReplayGain (missing tags)"
    assert page.summary.text() == "2 albums: 1 to split, 1 to rename, 2 to ReplayGain."
    # One action: Tidy says what it will do; there is no ReplayGain (or rename) button of its own.
    assert page.run_button.isVisible() and page.run_button.text() == "Run Tidy"
    assert [b.text() for b in page.findChildren(type(page.run_button)) if b.isVisible()] == ["← Back", "Run Tidy"]
    assert not list((library_dir / "Rips" / "EAC").glob("0*.flac"))  # nothing changed yet


def test_an_unsplit_album_is_split_then_replaygained(qtbot, window, service, library_dir, tidy):
    eac = library_dir / "Rips" / "EAC"
    open_plan(qtbot, window, service, eac)
    report = run(qtbot, window)
    assert report.startswith("Done: 1 album tidied."), report
    assert "split into 2 tracks, each identical to its part of the original" in report
    assert (eac / "01 One.flac").exists() and (eac / "02 Two.flac").exists()
    files, folder = tidy.runner.scanner.calls[0][:2]
    assert folder == eac and sorted(files) == ["01 One.flac", "02 Two.flac"]  # the image isn't measured
    assert TaggedFile(eac / "01 One.flac").get_replaygain(ALBUM_GAIN) is not None
    assert (eac / "image.wav").exists() and (eac / "image.cue").exists()  # kept until the user chooses
    [job] = window.jobs.jobs
    assert job.kind == "Tidy" and set(job.reports) == {"tidy"} and job.state == "done"


def test_the_offer_window_keeps_by_default(qtbot, window, service, library_dir, tidy):
    eac = library_dir / "Rips" / "EAC"
    open_plan(qtbot, window, service, eac)
    run(qtbot, window)
    qtbot.waitUntil(lambda: tidy.offer is not None and tidy.offer.isVisible())
    offer = tidy.offer
    assert offer.choices() == {str(eac / "image.wav"): KEEP, str(eac / "image.cue"): KEEP}
    assert not offer.apply_button.isEnabled()
    offer.close()  # closing keeps every file
    assert tidy.recycled == [] and (eac / "image.wav").exists()


def test_apply_recycles_only_the_files_marked_delete(qtbot, window, service, library_dir, tidy):
    eac = library_dir / "Rips" / "EAC"
    open_plan(qtbot, window, service, eac)
    run(qtbot, window)
    qtbot.waitUntil(lambda: tidy.offer is not None and tidy.offer.isVisible())
    offer = tidy.offer
    image = next(item for item in offer.items() if item.text(0) == "image.wav")
    box = offer.choice_box(image)
    assert box is not None and [box.itemText(i) for i in range(box.count())] == [KEEP, DELETE]
    box.setCurrentText(DELETE)  # each row's drop-down
    assert offer.apply_button.text() == "Apply (1 file to the Recycle Bin)"
    assert offer.choices()[str(eac / "image.wav")] == DELETE and offer.choices()[str(eac / "image.cue")] == KEEP
    offer.apply_button.click()
    assert tidy.recycled == [eac / "image.wav"] and not offer.isVisible()  # no question first


def test_the_buttons_set_the_selected_rows(qtbot, window, service, library_dir, tidy):
    eac = library_dir / "Rips" / "EAC"
    tidy.show_offer([eac / "image.wav", eac / "image.cue"])
    offer = tidy.offer
    offer.list.selectAll()
    offer.delete_button.click()
    assert set(offer.choices().values()) == {DELETE} and offer.apply_button.isEnabled()
    offer.list.clearSelection()
    offer.items()[1].setSelected(True)
    offer.keep_button.click()
    assert offer.marked() == [eac / "image.wav"]
    offer.close()


def test_a_drive_without_a_recycle_bin_cant_be_marked_delete(qtbot, window, service, library_dir, tidy):
    tidy.check_recycle = lambda _path: "network drive"
    tidy.show_offer([library_dir / "Rips" / "EAC" / "image.wav"])
    offer = tidy.offer
    offer.list.selectAll()
    offer.delete_button.click()
    assert offer.marked() == [] and "no Recycle Bin: network drive" in offer.items()[0].text(2)
    assert offer.choice_box(offer.items()[0]) is None  # no drop-down to choose Delete with
    offer.close()


def test_a_sheet_that_needs_a_look_is_skipped(qtbot, window, service, library_dir, tidy):
    page = open_plan(qtbot, window, service, library_dir / "Odd")
    [(name, text)] = page.rows()
    assert name == "Odd" and text.startswith("Skipped: ") and "open its .cue to split it by hand" in text
    assert not page.run_button.isEnabled() and page.summary.text().endswith("Nothing to do.")


def test_stop_ends_the_run(qtbot, window, service, library_dir, tidy):
    def stop_first(files, folder, target, exe, cancel=None, on_line=None):
        tidy.runner.cancel()
        return FakeScanner(TRACKS)(files, folder, target, exe, cancel, on_line)

    tidy.runner.scanner = stop_first
    open_plan(qtbot, window, service, library_dir / "Rips")
    report = run(qtbot, window)
    assert report.startswith("Stopped after 1 album."), report
    assert window.jobs.jobs[0].state == "stopped"
    assert TaggedFile(library_dir / "Rips" / "Album B" / "01 Loud.flac").get_replaygain(TRACK_GAIN) is not None
    assert not (library_dir / "Rips" / "EAC" / "01 One.flac").exists()  # the next album never started


def test_back_leaves_a_running_tidy_and_the_jobs_panel_shows_it_again(qtbot, window, service, library_dir, tidy):
    release = threading.Event()

    def held(files, folder, target, exe, cancel=None, on_line=None):
        release.wait(30)
        return FakeScanner(TRACKS)(files, folder, target, exe, cancel, on_line)

    tidy.runner.scanner = held
    page = open_plan(qtbot, window, service, library_dir / "Rips" / "Album B")
    page.run_button.click()
    try:
        assert page.state == "running" and page.back_button.isVisible()
        assert "Tidy goes on" in page.back_button.toolTip()
        page.back_button.click()
        assert window.center.currentWidget() is window.tools_page and tidy.job is not None
        window.show_job(tidy.job)
        assert window.center.currentWidget() is page and page.state == "running"
    finally:
        release.set()
    qtbot.waitUntil(lambda: page.state == "result", timeout=60000)
    assert window.jobs.jobs[0].state == "done"
