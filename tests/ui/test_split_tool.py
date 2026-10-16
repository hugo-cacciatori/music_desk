"""The Split page in the window (SPEC §22): the button on every .cue preview, the page's fields and table, edits,
validation marks and the disabled buttons, ticks and options, the encoding picker, Save / Save as, the unsaved-edits
questions, and the job with the real bundled flac on copied fixtures: the lock, Stop, recycling through a recorder
(never the real Recycle Bin), ReplayGain afterwards with a fake rsgain, and the report."""

import json
import shutil

import pytest
from rsgain_fake import FakeScanner

from music_desk.core.flac import Cancelled
from music_desk.core.index.store import path_of
from music_desk.core.tags import TaggedFile
from music_desk.services import split_job
from music_desk.settings import Settings

HIDDEN = "hidden_track"
SOURCE = "Fixture Artist - Split Album.flac"
SHEET = "Split Album.cue"


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    for name in (HIDDEN, "wav_image", "name_clash"):
        shutil.copytree(fixtures_dir / "split" / name, lib / "Albums" / name)
    sjis = lib / "Albums" / "sjis"
    sjis.mkdir()
    shutil.copy2(fixtures_dir / "split" / "sheets" / "sjis.cue", sjis / "album.cue")
    shutil.copy2(fixtures_dir / "split" / HIDDEN / SOURCE, sjis / "x.flac")
    return lib


@pytest.fixture
def recycled(window):
    """Everything the split and the saves send to the Recycle Bin, recorded: nothing really moves."""
    paths = []
    window.split.runner.recycle = paths.append
    window.split.check_recycle = lambda _path: None
    return paths


@pytest.fixture
def split(window, recycled):
    """The window's split controller. Unsaved edits left by a test are discarded when the window closes
    (pytest-qt closes it before the fixtures' finalizers run); tests that check the question answer it."""
    window.split.ask_leave = lambda _name: "discard"
    return window.split


def folder_id(service, path) -> int:
    conn = service.conn
    return next(i for (i,) in conn.execute("SELECT id FROM entries WHERE is_dir = 1") if path_of(conn, i) == path)


def file_id(service, path) -> int:
    conn = service.conn
    return next(i for (i,) in conn.execute("SELECT id FROM entries WHERE is_dir = 0") if path_of(conn, i) == path)


def ready(qtbot, split):
    """The page shows the sheet, and its plan follows the latest edit (there is none while a value can't be
    parsed)."""
    qtbot.waitUntil(lambda: split.page.state == "editing" and not split._planning
                    and not split._plan_timer.isActive(), timeout=20000)


def open_sheet(qtbot, split, path):
    split.open(path)
    ready(qtbot, split)
    assert split.plan is not None
    return split.page


def edit_cell(qtbot, split, row, key, text):
    """Type into a table cell, as a user would (the row is the table's)."""
    split.page.cell(row, key).setText(text)
    ready(qtbot, split)


def run(qtbot, split):
    split.page.split_button.click()
    qtbot.waitUntil(lambda: split.page.state in ("result", "error"), timeout=30000)
    return split.page.report.toPlainText()


# -- the way in --

def test_the_button_on_every_cue_preview(qtbot, window, service, library_dir, split):
    albums = library_dir / "Albums"
    window.open_folder(folder_id(service, albums / "sjis"))
    preview = window.side_panel.preview
    window.file_view.select_ids([file_id(service, albums / "sjis" / "album.cue")])
    qtbot.waitUntil(lambda: preview.split_button.isVisible(), timeout=10000)
    preview.split_button.click()
    assert window.center.currentWidget() is split.page
    ready(qtbot, split)
    assert split.path == albums / "sjis" / "album.cue"
    # No menu entry: the side panel's preview is the one way in.
    texts = [a.text() for a in window.list_menu([file_id(service, albums / "sjis" / "x.flac")]).actions()]
    assert "Split into tracks…" not in texts


# -- the page --

def test_the_page_shows_the_sheet_and_its_plan(qtbot, split, library_dir):
    page = open_sheet(qtbot, split, library_dir / "Albums" / HIDDEN / SHEET)
    assert page.file_edit.text() == SOURCE and "FLAC, 44.1 kHz, 0:12" in page.source_note.text()
    assert page.album_edits["performer"].text() == "Fixture Artist"
    assert (page.album_edits["genre"].text(), page.album_edits["date"].text()) == ("Test Rock", "2024")
    assert page.album_edits["disc"].text() == "1" and page.album_edits["disc_total"].text() == "2"
    assert page.album_other.toPlainText() == 'REM DISCID 8C0B6A0B\nREM COMMENT "ExactAudioCopy v1.6"'
    assert page.column_texts("number") == ["0", "1", "2", "3"]  # the audible hidden track is row 0
    assert page.column_texts("name") == ["00 Hidden track.flac", "01 Opening.flac", "02 Second Song.flac",
                                         "03 Duet.flac"]
    assert page.column_texts("length") == ["00:03:00", "00:03:37", "00:02:38", "00:03:00"]
    assert page.column_texts("gap")[2] == "00:06:00"
    assert page.split_button.text() == "Split 4 tracks" and page.split_button.isEnabled()
    assert not page.save_button.isEnabled() and page.save_as_button.isEnabled()  # nothing to save yet
    assert page.recycle.isEnabled() and not page.recycle.isChecked() and not page.replaygain.isEnabled()
    page.table.selectRow(2)  # track 2: FLAGS has its column, so no other lines
    assert page.track_other.isEnabled() and page.track_other.toPlainText() == ""
    page.table.selectRow(0)  # the hidden track isn't in the sheet
    assert not page.track_other.isEnabled()


def test_edits_marks_and_disabled_buttons(qtbot, split, library_dir):
    page = open_sheet(qtbot, split, library_dir / "Albums" / HIDDEN / SHEET)
    edit_cell(qtbot, split, 3, "title", "A Duet")
    assert page.cell(3, "name").text() == "03 A Duet.flac" and page.save_button.isEnabled()
    edit_cell(qtbot, split, 2, "start", "00:02:00")  # before track 1's start
    bad = page.cell(2, "start")
    assert bad.background().color().red() == 255 and "must start after track 1" in bad.toolTip()
    assert not page.save_button.isEnabled() and not page.save_as_button.isEnabled()
    assert not page.split_button.isEnabled() and "Fix the marked values first." in page.split_note.text()
    assert "Track 2 must start after track 1." in page.problems.text()
    edit_cell(qtbot, split, 2, "start", "6:37")  # not a cue time
    assert "isn't a cue time" in page.cell(2, "start").toolTip()
    edit_cell(qtbot, split, 2, "start", "0:6:37")  # back where it was, typed differently
    assert page.cell(2, "start").text() == "00:06:37" and page.split_button.isEnabled()
    page.album_edits["disc"].setText("one")
    page.album_edits["disc"].textEdited.emit("one")
    ready(qtbot, split)
    assert "isn't a number" in page.album_edits["disc"].toolTip() and not page.save_button.isEnabled()
    page.album_edits["disc"].setText("1")
    page.album_edits["disc"].textEdited.emit("1")
    ready(qtbot, split)
    assert page.save_button.isEnabled()


def test_add_and_delete_tracks(qtbot, split, library_dir):
    page = open_sheet(qtbot, split, library_dir / "Albums" / HIDDEN / SHEET)
    page.table.selectRow(1)  # track 1
    page.add_button.click()
    ready(qtbot, split)
    assert page.column_texts("number") == ["0", "1", "2", "3", "4"]
    assert page.column_texts("start")[2] == "00:04:56"  # halfway through track 1: frames 225 to 487 -> 356
    assert page.column_texts("name")[2] == "02 Track.flac"  # no title: named from its number
    page.table.selectRow(2)
    page.delete_button.click()
    ready(qtbot, split)
    assert page.column_texts("title") == ["Hidden track", "Opening", "Second Song", "Duet"]
    assert not split.doc.modified  # back to the sheet as it was, byte for byte


def test_ticks_and_options(qtbot, split, library_dir):
    page = open_sheet(qtbot, split, library_dir / "Albums" / HIDDEN / SHEET)
    page.recycle.setChecked(True)
    assert page.replaygain.isEnabled()
    page.cell(2, "tick").setCheckState(page.cell(2, "tick").checkState().Unchecked)
    ready(qtbot, split)
    assert page.split_button.text() == "Split 3 tracks"
    assert not page.recycle.isEnabled() and not page.recycle.isChecked() and not page.replaygain.isEnabled()
    assert "Only when every track is ticked" in page.recycle.toolTip()
    for row in range(4):
        page.cell(row, "tick").setCheckState(page.cell(row, "tick").checkState().Unchecked)
    ready(qtbot, split)
    assert not page.split_button.isEnabled() and "Tick the tracks" in page.split_note.text()


def test_names_get_a_number_instead_of_a_refusal(qtbot, split, library_dir):
    page = open_sheet(qtbot, split, library_dir / "Albums" / "name_clash" / "image.cue")
    assert page.column_texts("name") == ["01 One.flac", "02 Two (2).flac"] and page.split_button.isEnabled()
    assert "02 Two.flac is taken (it already exists)" in page.notes.text()


def test_unusable_masks_or_flac_disable_split(qtbot, window, split, library_dir):
    page = open_sheet(qtbot, split, library_dir / "Albums" / HIDDEN / SHEET)
    window.tools_page.names_card.masks.item(0).setText("{nope}")
    split._refresh()
    ready(qtbot, split)
    assert not page.split_button.isEnabled() and "Fix the file names first (Tools page)." in page.split_note.text()
    window.tools_page.names_card.reset()
    window.show_flac_version(None)
    assert not page.split_button.isEnabled() and "flac couldn't run" in page.split_note.text()
    window.show_flac_version("flac 1.5.0")
    assert page.split_button.isEnabled()


def test_the_encoding_picker(qtbot, split, library_dir, monkeypatch):
    page = open_sheet(qtbot, split, library_dir / "Albums" / "sjis" / "album.cue")
    assert page.encoding.itemText(0) == "Auto (Windows-1252)" and page.encoding_note.isVisible()
    split.pick_encoding("shift_jis")
    ready(qtbot, split)
    assert page.album_edits["title"].text() == "\u521d\u604b" and not page.encoding_note.isVisible()
    assert page.encoding.currentData() == "shift_jis"
    edit_cell(qtbot, split, 0, "title", "Edited")
    asked = []
    monkeypatch.setattr(split, "confirm_discard_for_encoding", lambda: asked.append(1) or False)
    split.pick_encoding(None)
    assert asked and split.codec == "shift_jis" and page.encoding.currentData() == "shift_jis"


# -- saving --

def test_save_recycles_the_old_sheet(qtbot, split, recycled, library_dir):
    sheet = library_dir / "Albums" / HIDDEN / SHEET
    before = sheet.read_bytes()
    page = open_sheet(qtbot, split, sheet)
    edit_cell(qtbot, split, 3, "title", "A Duet")
    page.save_button.click()
    assert recycled == [sheet] and sheet.read_bytes() == before.replace(b'"Duet"', b'"A Duet"')
    ready(qtbot, split)
    assert not page.save_button.isEnabled() and not split.doc.modified


def test_save_where_windows_cant_recycle_asks_first(qtbot, split, recycled, library_dir, monkeypatch):
    sheet = library_dir / "Albums" / HIDDEN / SHEET
    before = sheet.read_bytes()
    split.check_recycle = lambda _path: "D:\\ is a network location, which has no Recycle Bin"
    open_sheet(qtbot, split, sheet)
    edit_cell(qtbot, split, 3, "title", "A Duet")
    answers = [False, True]
    monkeypatch.setattr(split, "confirm_overwrite_for_good", lambda name, reason: answers.pop(0))
    assert not split.save() and sheet.read_bytes() == before
    assert split.save() and b"A Duet" in sheet.read_bytes() and recycled == []


def test_save_as_replace_or_keep_both(qtbot, split, recycled, library_dir, monkeypatch):
    folder = library_dir / "Albums" / HIDDEN
    open_sheet(qtbot, split, folder / SHEET)
    edit_cell(qtbot, split, 3, "title", "A Duet")
    other = folder / "other.cue"
    other.write_bytes(b"old")
    monkeypatch.setattr(split, "choose_cue_file", lambda suggested: str(other))
    monkeypatch.setattr(split, "ask_replace_or_keep", lambda name: "keep")
    assert split.save_as()
    assert other.read_bytes() == b"old" and split.path == folder / "other (2).cue" and recycled == []
    assert b"A Duet" in (folder / "other (2).cue").read_bytes() and b"A Duet" not in (folder / SHEET).read_bytes()
    monkeypatch.setattr(split, "ask_replace_or_keep", lambda name: "replace")
    assert split.save_as() and recycled == [other] and b"A Duet" in other.read_bytes()


def test_a_save_in_a_locked_folder_is_refused(qtbot, window, split, library_dir):
    folder = library_dir / "Albums" / HIDDEN
    open_sheet(qtbot, split, folder / SHEET)
    edit_cell(qtbot, split, 3, "title", "A Duet")
    lock = window.library.locks.acquire([folder], "the ReplayGain tool")
    try:
        assert not split.save() and b"A Duet" not in (folder / SHEET).read_bytes()
    finally:
        window.library.locks.release(lock)


def test_a_character_the_encoding_cant_hold(qtbot, split, library_dir, monkeypatch):
    sheet = library_dir / "Albums" / "sjis" / "album.cue"
    open_sheet(qtbot, split, sheet)  # read as Windows-1252
    edit_cell(qtbot, split, 0, "title", "\u65e5\u672c")
    asked = []
    monkeypatch.setattr(split, "confirm_utf8", lambda encoding, chars: asked.append((encoding, chars)) or True)
    assert split.save()
    assert asked == [("Windows-1252", ["\u65e5", "\u672c"])] and sheet.read_bytes().startswith(b"\xef\xbb\xbf")


# -- unsaved edits --

def test_unsaved_edits_ask_before_they_are_lost(qtbot, window, split, library_dir, monkeypatch):
    folder = library_dir / "Albums" / HIDDEN
    page = open_sheet(qtbot, split, folder / SHEET)
    edit_cell(qtbot, split, 3, "title", "A Duet")
    asked = []
    monkeypatch.setattr(split, "ask_leave", lambda name: asked.append(name) or "cancel")
    page.back_button.click()
    assert window.center.currentWidget() is page and asked == [SHEET]
    split.open(library_dir / "Albums" / "wav_image" / "image.cue")  # another sheet
    assert split.path == folder / SHEET and len(asked) == 2
    window.close()  # quitting
    assert window.isVisible() and len(asked) == 3

    window.close_tools()  # browsing elsewhere keeps the page as it was
    split.open(folder / SHEET)
    assert window.center.currentWidget() is page and split.doc.modified and len(asked) == 3

    monkeypatch.setattr(split, "ask_leave", lambda name: "discard")
    page.back_button.click()
    assert window.center.currentWidget() is not page and b"A Duet" not in (folder / SHEET).read_bytes()


def test_split_with_unsaved_edits_saves_first(qtbot, split, recycled, library_dir, monkeypatch):
    folder = library_dir / "Albums" / "wav_image"
    open_sheet(qtbot, split, folder / "image.cue")
    edit_cell(qtbot, split, 1, "title", "Deux")
    monkeypatch.setattr(split, "ask_save_first", lambda name: "cancel")
    split.page.split_button.click()
    assert split.page.state == "editing" and not (folder / "01 One.flac").exists()
    monkeypatch.setattr(split, "ask_save_first", lambda name: "save")
    report = run(qtbot, split)
    assert report.startswith("Done: 2 tracks split, all identical to the original.")
    assert recycled == [folder / "image.cue"]  # the save's old version; the originals stay
    assert (folder / "02 Deux.flac").exists() and b"Deux" in (folder / "image.cue").read_bytes()


# -- the job --

def test_the_job_splits_proves_and_reports(qtbot, window, service, split, recycled, library_dir):
    folder = library_dir / "Albums" / HIDDEN
    page = open_sheet(qtbot, split, folder / SHEET)
    page.recycle.setChecked(True)
    report = run(qtbot, split)
    assert report.startswith("Done: 4 tracks split, all identical to the original.")
    assert "Together, the tracks have the original's MD5" in report
    assert "The album file and its .cue are in the Recycle Bin." in report
    assert recycled == [folder / SOURCE, folder / SHEET]
    assert sorted(p.name for p in folder.iterdir()) == [
        "00 Hidden track.flac", "01 Opening.flac", "02 Second Song.flac", "03 Duet.flac", SOURCE, SHEET]
    assert TaggedFile(folder / "03 Duet.flac").fields().artist == "Fixture Artist & Guest"
    job = window.jobs.jobs[-1]
    assert job.kind == "Split" and job.state == "done" and job.status == "4 tracks split"
    assert service.locks.held() == [] and split.job is None
    page.done_button.click()
    assert window.center.currentWidget() is window.file_view
    window.show_job(job.id)  # the report again, from the jobs panel
    assert window.center.currentWidget() is page and page.report.toPlainText() == report


def test_only_the_ticked_track(qtbot, split, recycled, library_dir):
    folder = library_dir / "Albums" / HIDDEN
    page = open_sheet(qtbot, split, folder / SHEET)
    for row in (0, 1, 2):
        page.cell(row, "tick").setCheckState(page.cell(row, "tick").checkState().Unchecked)
    ready(qtbot, split)
    report = run(qtbot, split)
    assert report.startswith("Done: 1 of 4 tracks split, each identical to its part of the original.")
    assert (folder / "03 Duet.flac").exists() and not (folder / "01 Opening.flac").exists()
    assert TaggedFile(folder / "03 Duet.flac").fields().track == 3 and recycled == []


def test_stop_leaves_the_album_as_it_was(qtbot, window, split, library_dir, monkeypatch):
    class Stopped(split_job.FlacRunner):
        def encode(self, *args, **kwargs):
            raise Cancelled()

    monkeypatch.setattr(split_job, "FlacRunner", Stopped)
    folder = library_dir / "Albums" / HIDDEN
    before = sorted(p.name for p in folder.iterdir())
    open_sheet(qtbot, split, folder / SHEET)
    report = run(qtbot, split)
    assert report.startswith("Stopped: nothing left half-done.")
    assert sorted(p.name for p in folder.iterdir()) == before
    job = window.jobs.jobs[-1]
    assert job.state == "stopped" and job.status == "nothing left half-done"


def test_recycling_then_replaygain(qtbot, window, split, recycled, library_dir):
    scanner = FakeScanner(values={})
    split.runner.scanner = scanner
    folder = library_dir / "Albums" / "wav_image"
    page = open_sheet(qtbot, split, folder / "image.cue")
    page.recycle.setChecked(True)
    page.replaygain.setChecked(True)
    report = run(qtbot, split)
    assert recycled == [folder / "image.wav", folder / "image.cue"]
    assert scanner.calls and scanner.calls[0][1] == folder and "ReplayGain:" in report


def test_a_running_split_is_shown_instead_of_opening_another(qtbot, window, split, library_dir):
    split.job = window._start_job("Split", library_dir / "Albums" / HIDDEN)
    split.open(library_dir / "Albums" / "wav_image" / "image.cue")
    assert split.path is None and "still running" in window.toast.label.text()
    window.jobs.finish(split.job, "done", "")
    split.job = None


# -- nothing remembered --

def test_no_split_state_is_saved(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"split_options": {"recycle": True}}), encoding="utf-8")
    settings = Settings.load(path)
    settings.save()
    assert not [key for key in json.loads(path.read_text(encoding="utf-8")) if key.startswith("split_")]


def test_options_start_unticked_each_time(qtbot, split, library_dir):
    page = open_sheet(qtbot, split, library_dir / "Albums" / HIDDEN / SHEET)
    page.recycle.setChecked(True)
    open_sheet(qtbot, split, library_dir / "Albums" / "wav_image" / "image.cue")
    assert not page.recycle.isChecked() and not page.replaygain.isChecked()

