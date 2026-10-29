"""The toolbar's Tidy (SPEC §28, §35): the Tidy entry's window on the selection or the open folder, with the whole
plan; one Run Tidy button runs it album by album (the split with the real bundled flac, the renames, ReplayGain
with a fake scanner), then the Redundant files window (SPEC §32: every box checked, Delete selected recycles the
checked items on a worker and becomes the report, closing keeps all; the Recycle Bin is a recorder)."""

import shutil

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton
from rsgain_fake import VALUES, FakeScanner

from refrain.context_menu import tidy_entry
from refrain.context_menu.tidy_entry import TidyWindow
from refrain.core.cleanup import Item
from refrain.core.index.store import path_of
from refrain.core.path_locks import PathLocks
from refrain.core.replaygain import ALBUM_GAIN, TRACK_GAIN
from refrain.core.tags import TaggedFile
from refrain.services.tidy_job import OWNER, TidyRunner
from refrain.ui.recycle_window import RecycleWindow
from refrain.ui.tools.tool_cards import TIDY

TRACKS = {**VALUES, "01 One.flac": ("-3.00 dB", "0.5"), "02 Two.flac": ("-2.00 dB", "0.6")}


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "split" / "wav_image", lib / "Rips" / "EAC")
    shutil.copytree(fixtures_dir / "replaygain" / "Fixture Artist" / "Album B", lib / "Rips" / "Album B")
    shutil.copytree(fixtures_dir / "split" / "data_track", lib / "Odd")  # a sheet that needs a look
    return lib


@pytest.fixture
def fake(monkeypatch):
    """Every Tidy window's runner gets a fake scanner (a test may replace it before Run)."""
    def runner(locks, parent=None):
        made = TidyRunner(locks, parent)
        made.scanner = FakeScanner(TRACKS)
        return made
    monkeypatch.setattr(tidy_entry, "TidyRunner", runner)


def folder_id(service, path) -> int:
    conn = service.conn
    return next(i for (i,) in conn.execute("SELECT id FROM entries WHERE is_dir = 1") if path_of(conn, i) == path)


def open_plan(qtbot, open_app, service, folder) -> TidyWindow:
    """The toolbar's Tidy on `folder`, open with nothing selected: its window, on the plan."""
    main = open_app.window
    main.open_folder(folder_id(service, folder))
    main.file_view.select_ids([])
    before = list(open_app.entry_windows)
    main.tools.start(TIDY)
    [tidy] = [w for w in open_app.entry_windows if w not in before]
    assert isinstance(tidy, TidyWindow) and tidy.parent() is None  # its own window (SPEC §35)
    assert main.center.currentWidget() is main.file_view
    qtbot.addWidget(tidy)
    tidy.recycled = []
    tidy.recycle = tidy.recycled.append
    tidy.check_recycle = lambda _path: None
    qtbot.waitUntil(lambda: tidy.page.state in ("plan", "error"), timeout=20000)
    return tidy


def run(qtbot, tidy: TidyWindow):
    tidy.page.run_button.click()
    qtbot.waitUntil(lambda: tidy.page.state in ("result", "error"), timeout=60000)
    return tidy.page.log.toPlainText()


def test_the_plan_lists_every_album_and_one_run_button(qtbot, open_app, service, library_dir, fake):
    (library_dir / "Rips" / "Album B" / "01 Loud.flac").rename(library_dir / "Rips" / "Album B" / "loud.flac")
    page = open_plan(qtbot, open_app, service, library_dir / "Rips").page
    rows = dict(page.rows())
    assert rows["EAC"] == "Split image.cue into 2 tracks, then ReplayGain (after the split)"
    assert rows["Album B"] == "Rename 1 file, then ReplayGain (missing tags); offer 1 folder"  # Scans: a picture
    assert page.summary.text() == "2 albums: 1 to split, 1 to rename, 2 to ReplayGain, 1 with files to offer."
    # One action: Tidy says what it will do; there is no ReplayGain (or rename) button of its own. Close comes
    # after it (SPEC §36 task 4).
    assert page.run_button.isVisible() and page.run_button.text() == "Run Tidy"
    assert [b.text() for b in page.findChildren(type(page.run_button)) if b.isVisible()] == ["Run Tidy", "Close"]
    assert not list((library_dir / "Rips" / "EAC").glob("0*.flac"))  # nothing changed yet


def test_an_unsplit_album_is_split_then_replaygained(qtbot, open_app, service, library_dir, fake):
    eac = library_dir / "Rips" / "EAC"
    tidy = open_plan(qtbot, open_app, service, eac)
    report = run(qtbot, tidy)
    assert report.startswith("Done: 1 album tidied."), report
    assert "split into 2 tracks, each identical to its part of the original" in report
    assert (eac / "01 One.flac").exists() and (eac / "02 Two.flac").exists()
    files, folder = tidy.runner.scanner.calls[0][:2]
    assert folder == eac and sorted(files) == ["01 One.flac", "02 Two.flac"]  # the image isn't measured
    assert TaggedFile(eac / "01 One.flac").get_replaygain(ALBUM_GAIN) is not None
    assert (eac / "image.wav").exists() and (eac / "image.cue").exists()  # kept until the user chooses


def offer_after_run(qtbot, open_app, service, folder):
    tidy = open_plan(qtbot, open_app, service, folder)
    run(qtbot, tidy)
    qtbot.waitUntil(lambda: tidy.offer is not None and tidy.offer.isVisible())
    qtbot.addWidget(tidy.offer)
    return tidy, tidy.offer


def offer_of(qtbot, folder, items, check_recycle=lambda _path: None):
    """The Redundant files window Tidy opens, on given items (what the run would offer)."""
    recycled = []
    offer = RecycleWindow(None, folder, items, locks=PathLocks(), owner=OWNER, intro="", recycle=recycled.append,
                          check_recycle=check_recycle)
    qtbot.addWidget(offer)
    offer.show()
    return offer, recycled


def test_the_redundant_files_window_checks_every_box(qtbot, open_app, service, library_dir, fake):
    eac = library_dir / "Rips" / "EAC"
    tidy, offer = offer_after_run(qtbot, open_app, service, eac)
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


def test_delete_selected_recycles_only_the_checked_items_then_reports(qtbot, open_app, service, library_dir, fake):
    eac = library_dir / "Rips" / "EAC"
    tidy, offer = offer_after_run(qtbot, open_app, service, eac)
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


def test_clutter_is_offered_after_the_run(qtbot, open_app, service, library_dir, fake):
    album = library_dir / "Rips" / "Album B"
    (album / "folder.jpg").write_bytes(b"x")
    tidy, offer = offer_after_run(qtbot, open_app, service, album)
    assert [(row.text(0), row.text(2)) for row in offer.rows()] == [
        ("Scans (folder, 1 picture)", "folder of 1 picture"), ("folder.jpg", "picture beside music")]
    offer.delete_button.click()
    qtbot.waitUntil(lambda: offer.state == "report", timeout=10000)
    assert tidy.recycled == [album / "Scans", album / "folder.jpg"]  # the folder whole
    offer.close()


def test_clutter_changed_before_the_window_opens_is_left_out(qtbot, open_app, service, library_dir, fake):
    album = library_dir / "Rips" / "Album B"
    tidy = open_plan(qtbot, open_app, service, album)
    (album / "Scans" / "notes.txt").write_text("x")  # now Scans holds something else
    run(qtbot, tidy)
    assert tidy.offer is None  # nothing left to offer: no window


def test_a_drive_without_a_recycle_bin_is_kept_and_listed(qtbot, open_app, service, library_dir, fake):
    image = library_dir / "Rips" / "EAC" / "image.wav"
    offer, _recycled = offer_of(qtbot, library_dir / "Rips" / "EAC", [Item(image, "image split into 2 tracks")],
                                check_recycle=lambda _path: "a network location")
    assert offer.kept_rows() == [("image.wav", "kept: a network location")]  # greyed, no checkbox
    assert offer.marked() == [] and not offer.delete_button.isEnabled()
    offer.close()


def test_an_item_gone_meanwhile_is_skipped(qtbot, open_app, service, library_dir, fake):
    eac = library_dir / "Rips" / "EAC"
    offer, recycled = offer_of(qtbot, eac, [Item(eac / "image.wav", "image"), Item(eac / "image.cue", "sheet")])
    (eac / "image.cue").unlink()
    offer.delete_button.click()
    qtbot.waitUntil(lambda: offer.state == "report", timeout=10000)
    assert recycled == [eac / "image.wav"]
    assert f"{eac / 'image.cue'}  (gone)" in offer.report_view.toPlainText()
    offer.close()


def test_a_sheet_that_needs_a_look_is_skipped(qtbot, open_app, service, library_dir, fake):
    page = open_plan(qtbot, open_app, service, library_dir / "Odd").page
    [(name, text)] = page.rows()
    assert name == "Odd" and text.startswith("Skipped: ") and "open its .cue to split it by hand" in text
    assert not page.run_button.isEnabled() and page.summary.text().endswith("Nothing to do.")


def test_stop_ends_the_run(qtbot, open_app, service, library_dir, fake):
    tidy = open_plan(qtbot, open_app, service, library_dir / "Rips")

    def stop_first(files, folder, target, exe, cancel=None, on_line=None):
        tidy.runner.cancel()
        return FakeScanner(TRACKS)(files, folder, target, exe, cancel, on_line)

    tidy.runner.scanner = stop_first
    report = run(qtbot, tidy)
    assert report.startswith("Stopped after 1 album."), report
    assert TaggedFile(library_dir / "Rips" / "Album B" / "01 Loud.flac").get_replaygain(TRACK_GAIN) is not None
    assert not (library_dir / "Rips" / "EAC" / "01 One.flac").exists()  # the next album never started
