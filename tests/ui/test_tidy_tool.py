"""Tidy in the window (SPEC §28): the Tidy button opens the whole plan, one Run Tidy button runs it album by album
(the split with the real bundled flac, the renames, ReplayGain with a fake scanner), one Tidy job, and the
Redundant files window (SPEC §32: every box checked, Delete selected recycles the checked items on a worker and
becomes the report, closing keeps all; the Recycle Bin is a recorder)."""

import shutil
import threading

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton
from rsgain_fake import VALUES, FakeScanner

from music_desk.core.cleanup import Item
from music_desk.core.index.store import path_of
from music_desk.core.replaygain import ALBUM_GAIN, TRACK_GAIN
from music_desk.core.tags import TaggedFile
from music_desk.ui.tools.tool_cards import TIDY

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
    return controller


def folder_id(service, path) -> int:
    conn = service.conn
    return next(i for (i,) in conn.execute("SELECT id FROM entries WHERE is_dir = 1") if path_of(conn, i) == path)


def open_plan(qtbot, window, service, folder):
    window.open_folder(folder_id(service, folder))
    window.file_view.select_ids([])
    window.tools.start(TIDY)
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
    assert rows["Album B"] == "Rename 1 file, then ReplayGain (missing tags); offer 1 folder"  # Scans: a picture
    assert page.summary.text() == "2 albums: 1 to split, 1 to rename, 2 to ReplayGain, 1 with files to offer."
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


def offer_after_run(qtbot, window, service, tidy, folder):
    open_plan(qtbot, window, service, folder)
    run(qtbot, window)
    qtbot.waitUntil(lambda: tidy.offer is not None and tidy.offer.isVisible())
    return tidy.offer


def test_the_redundant_files_window_checks_every_box(qtbot, window, service, library_dir, tidy):
    eac = library_dir / "Rips" / "EAC"
    offer = offer_after_run(qtbot, window, service, tidy, eac)
    assert offer.windowTitle() == "Redundant files: EAC"
    assert [(row.text(0), row.text(2)) for row in offer.rows()] == [
        ("image.wav", "image split into 2 tracks"), ("image.cue", "cue sheet of image.wav")]
    assert [item.path for item in offer.marked()] == [eac / "image.wav", eac / "image.cue"]  # all checked
    assert offer.delete_button.isEnabled()
    offer.uncheck_all.click()
    assert offer.marked() == [] and not offer.delete_button.isEnabled()
    offer.check_all.click()
    assert len(offer.marked()) == 2
    offer.close()  # closing keeps every file
    assert tidy.recycled == [] and (eac / "image.wav").exists()


def test_delete_selected_recycles_only_the_checked_items_then_reports(qtbot, window, service, library_dir, tidy):
    eac = library_dir / "Rips" / "EAC"
    offer = offer_after_run(qtbot, window, service, tidy, eac)
    sheet = next(row for row in offer.rows() if row.text(0) == "image.cue")
    sheet.setCheckState(0, Qt.CheckState.Unchecked)  # kept
    offer.delete_button.click()  # at once, no question; on a worker
    qtbot.waitUntil(lambda: offer.state == "report", timeout=10000)
    assert tidy.recycled == [eac / "image.wav"]
    assert offer.isVisible() and offer.report_view.isVisible() and not offer.list.isVisible()  # the report stays
    assert offer.report_view.toPlainText().startswith("Sent 1 item to the Recycle Bin.")
    assert [b.text() for b in offer.findChildren(QPushButton) if b.isVisible()] == ["Close"]
    offer.close_button.click()
    assert not offer.isVisible()


def test_clutter_is_offered_after_the_run(qtbot, window, service, library_dir, tidy):
    album = library_dir / "Rips" / "Album B"
    (album / "folder.jpg").write_bytes(b"x")
    offer = offer_after_run(qtbot, window, service, tidy, album)
    assert [(row.text(0), row.text(2)) for row in offer.rows()] == [
        ("Scans (folder, 1 picture)", "folder of 1 picture"), ("folder.jpg", "picture beside music")]
    offer.delete_button.click()
    qtbot.waitUntil(lambda: offer.state == "report", timeout=10000)
    assert tidy.recycled == [album / "Scans", album / "folder.jpg"]  # the folder whole
    offer.close()


def test_clutter_changed_before_the_window_opens_is_left_out(qtbot, window, service, library_dir, tidy):
    album = library_dir / "Rips" / "Album B"
    open_plan(qtbot, window, service, album)
    (album / "Scans" / "notes.txt").write_text("x")  # now Scans holds something else
    run(qtbot, window)
    assert tidy.offer is None  # nothing left to offer: no window


def test_a_drive_without_a_recycle_bin_is_kept_and_listed(qtbot, window, service, library_dir, tidy):
    tidy.check_recycle = lambda _path: "a network location"
    image = library_dir / "Rips" / "EAC" / "image.wav"
    tidy.show_offer([Item(image, "image split into 2 tracks")], library_dir / "Rips" / "EAC")
    offer = tidy.offer
    assert offer.kept_rows() == [("image.wav", "kept: a network location")]  # greyed, no checkbox
    assert offer.marked() == [] and not offer.delete_button.isEnabled()
    offer.close()


def test_an_item_gone_meanwhile_is_skipped(qtbot, window, service, library_dir, tidy):
    eac = library_dir / "Rips" / "EAC"
    tidy.show_offer([Item(eac / "image.wav", "image"), Item(eac / "image.cue", "sheet")], eac)
    offer = tidy.offer
    (eac / "image.cue").unlink()
    offer.delete_button.click()
    qtbot.waitUntil(lambda: offer.state == "report", timeout=10000)
    assert tidy.recycled == [eac / "image.wav"]
    assert f"{eac / 'image.cue'}  (gone)" in offer.report_view.toPlainText()
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
        assert "Tidy goes on" in page.back_button.toolTip() and "file list" in page.back_button.toolTip()
        page.back_button.click()
        assert window.center.currentWidget() is window.file_view and tidy.job is not None
        window.show_job(tidy.job)
        assert window.center.currentWidget() is page and page.state == "running"
    finally:
        release.set()
    qtbot.waitUntil(lambda: page.state == "result", timeout=60000)
    assert window.jobs.jobs[0].state == "done"
