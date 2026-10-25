"""The Split page in the window (SPEC §22): the button on every .cue preview, the page's fields and table, edits,
validation marks and the disabled buttons, ticks, the source picker, the destination, the encoding picker, Save /
Save as (a saved-over sheet goes to a recorder, never the real Recycle Bin), the unsaved-edits questions, and the
job with the real bundled flac on copied fixtures: the lock, Stop, several splits of one image, and the report.
A split is just a split: it never touches the image or its sheet."""

import json
import shutil

import pytest

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
    """Every saved-over cue sheet sent to the Recycle Bin, recorded: nothing really moves."""
    paths = []
    window.split.recycle = paths.append
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

def show_cue(qtbot, window, service, cue):
    window.open_folder(folder_id(service, cue.parent))
    preview = window.side_panel.preview
    window.file_view.select_ids([file_id(service, cue)])
    qtbot.waitUntil(lambda: preview.split_button.isVisible(), timeout=10000)
    return preview


def test_the_buttons_on_every_cue_preview(qtbot, window, service, library_dir, split):
    albums = library_dir / "Albums"
    preview = show_cue(qtbot, window, service, albums / "sjis" / "album.cue")
    # Play, Split and Edit… (Hugo, M19): Play is Open, renamed for a cue sheet.
    assert [b.text() for b in (preview.open_button, preview.split_button, preview.edit_button)] == [
        "Play", "Split", "Edit…"]
    assert preview.edit_button.isVisible()
    preview.edit_button.click()
    assert window.center.currentWidget() is split.page
    ready(qtbot, split)
    assert split.path == albums / "sjis" / "album.cue"
    # No menu entry: the side panel's preview is the one way in.
    texts = [a.text() for a in window.list_menu([file_id(service, albums / "sjis" / "x.flac")]).actions()]
    assert "Split into tracks…" not in texts


def test_other_files_keep_open(qtbot, window, service, library_dir):
    folder = library_dir / "Albums" / "sjis"
    window.open_folder(folder_id(service, folder))
    preview = window.side_panel.preview
    window.file_view.select_ids([file_id(service, folder / "album.cue")])
    qtbot.waitUntil(lambda: preview.split_button.isVisible(), timeout=10000)
    (folder / "notes.txt").write_text("hello", encoding="utf-8")
    preview.show_file(folder / "notes.txt")
    assert preview.open_button.text() == "Open"
    assert not preview.split_button.isVisible() and not preview.edit_button.isVisible()


def test_split_splits_at_once_with_the_defaults(qtbot, window, service, library_dir, split):
    folder = library_dir / "Albums" / "wav_image"
    preview = show_cue(qtbot, window, service, folder / "image.cue")
    preview.split_button.click()
    assert window.center.currentWidget() is split.page
    qtbot.waitUntil(lambda: split.page.state in ("result", "error"), timeout=30000)
    # Failed once in a whole run (2026-10-25) with no message, and passed alone and in the next whole run: say why.
    assert split.page.report.toPlainText().startswith("Done: 2 tracks split, all identical to the original."), (
        f"state {split.page.state!r}: {split.page.report.toPlainText()!r}")
    assert (folder / "01 One.flac").exists() and (folder / "02 Two.flac").exists()
    assert (folder / "image.wav").exists() and (folder / "image.cue").exists()  # a split touches nothing else


def test_split_stops_on_a_sheet_that_needs_a_look(qtbot, window, service, library_dir, split):
    folder = library_dir / "Albums" / "sjis"
    before = sorted(p.name for p in folder.iterdir())
    preview = show_cue(qtbot, window, service, folder / "album.cue")
    preview.split_button.click()
    ready(qtbot, split)
    assert split.page.state == "editing" and split.job is None  # the page stays, nothing was split
    assert window.toast.label.text().startswith("Not split: The sheet isn't in Windows-1252")
    assert sorted(p.name for p in folder.iterdir()) == before


def test_a_windows_1252_sheet_splits_at_once(qtbot, window, service, library_dir, split):
    """What EAC writes on a Western Windows (no BOM, not UTF-8): Auto reads it as Windows-1252 and splits."""
    folder = library_dir / "Albums" / "wav_image"
    sheet = (folder / "image.cue").read_text(encoding="utf-8").replace('TITLE "One"', 'TITLE "Café"')
    (folder / "image.cue").write_bytes(sheet.encode("cp1252"))
    preview = show_cue(qtbot, window, service, folder / "image.cue")
    preview.split_button.click()
    qtbot.waitUntil(lambda: split.page.state in ("result", "error"), timeout=30000)
    assert split.page.report.toPlainText().startswith("Done: 2 tracks split"), split.page.report.toPlainText()
    assert (folder / "01 Café.flac").exists()


# -- the page --

def test_the_page_shows_the_sheet_and_its_plan(qtbot, split, library_dir):
    page = open_sheet(qtbot, split, library_dir / "Albums" / HIDDEN / SHEET)
    assert page.source.currentData() == SOURCE and "FLAC, 44.1 kHz, 0:12" in page.source_note.text()
    assert page.destination.text() == str(library_dir / "Albums" / HIDDEN)
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


def test_ticks(qtbot, split, library_dir):
    page = open_sheet(qtbot, split, library_dir / "Albums" / HIDDEN / SHEET)
    page.cell(2, "tick").setCheckState(page.cell(2, "tick").checkState().Unchecked)
    ready(qtbot, split)
    assert page.split_button.text() == "Split 3 tracks"
    for row in range(4):
        page.cell(row, "tick").setCheckState(page.cell(row, "tick").checkState().Unchecked)
    ready(qtbot, split)
    assert not page.split_button.isEnabled() and "Tick the tracks" in page.split_note.text()


def test_the_source_picker(qtbot, split, library_dir):
    """Which audio file the sheet points to is chosen from the files beside it (Hugo, 2026-10-17)."""
    folder = library_dir / "Albums" / "sjis"
    shutil.copy2(folder / "x.flac", folder / "y.flac")
    (folder / "notes.txt").write_text("not audio", encoding="utf-8")
    page = open_sheet(qtbot, split, folder / "album.cue")
    assert [page.source.itemText(i) for i in range(page.source.count())] == ["x.flac", "y.flac"]
    page.source.setCurrentIndex(1)
    page.source.activated.emit(1)
    ready(qtbot, split)
    assert split.doc.file == "y.flac" and 'FILE "y.flac" WAVE' in split.doc.text()
    assert split.plan.source == folder / "y.flac"
    (folder / "x.flac").unlink()  # the test's own copy, gone: the sheet now points to nothing
    split.doc.set_file("x.flac")
    split._refresh()
    ready(qtbot, split)  # each plan lists the folder again
    assert page.source.itemText(0) == "x.flac (not found)" and page.source.currentIndex() == 0
    split.doc.set_file("x.ape")
    split._refresh()
    ready(qtbot, split)
    assert page.source.itemText(0) == "x.ape (not supported)"


def test_the_destination(qtbot, split, library_dir, tmp_path, monkeypatch):
    page = open_sheet(qtbot, split, library_dir / "Albums" / "wav_image" / "image.cue")
    page.destination.setText(str(tmp_path / "Nowhere"))
    page.destination.textEdited.emit(str(tmp_path / "Nowhere"))
    ready(qtbot, split)
    assert not page.split_button.isEnabled() and "doesn't exist" in page.split_note.text()
    elsewhere = tmp_path / "Elsewhere"
    elsewhere.mkdir()
    monkeypatch.setattr(split, "choose_destination_folder", lambda start: str(elsewhere))
    page.browse_button.click()
    ready(qtbot, split)
    assert page.destination.text() == str(elsewhere) and page.split_button.isEnabled()
    run(qtbot, split)
    assert sorted(p.name for p in elsewhere.iterdir()) == ["01 One.flac", "02 Two.flac"]
    assert sorted(p.name for p in (library_dir / "Albums" / "wav_image").iterdir()) == ["image.cue", "image.wav"]


def test_names_get_a_number_instead_of_a_refusal(qtbot, split, library_dir):
    page = open_sheet(qtbot, split, library_dir / "Albums" / "name_clash" / "image.cue")
    assert page.column_texts("name") == ["01 One.flac", "02 Two (2).flac"] and page.split_button.isEnabled()
    assert "02 Two.flac is taken (it already exists)" in page.notes.text()


def test_unusable_masks_or_flac_disable_split(qtbot, window, split, library_dir):
    page = open_sheet(qtbot, split, library_dir / "Albums" / HIDDEN / SHEET)
    window.tools.cards.names_card.masks.item(0).setText("{nope}")
    split._refresh()
    ready(qtbot, split)
    assert not page.split_button.isEnabled()
    assert "Fix the file names first (File > Options…, File names)." in page.split_note.text()
    window.tools.cards.names_card.reset()
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
    elsewhere = library_dir / "Albums" / "wav_image" / "version.cue"
    monkeypatch.setattr(split, "choose_cue_file", lambda suggested: str(elsewhere))
    assert not split.save_as() and not elsewhere.exists()  # a sheet sits beside its audio file


def test_a_save_in_a_locked_folder_is_refused(qtbot, window, split, library_dir):
    folder = library_dir / "Albums" / HIDDEN
    open_sheet(qtbot, split, folder / SHEET)
    edit_cell(qtbot, split, 3, "title", "A Duet")
    lock = window.library.locks.acquire([folder], "ReplayGain")
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
    assert recycled == [folder / "image.cue"]  # the save's old version, nothing else
    assert (folder / "02 Deux.flac").exists() and b"Deux" in (folder / "image.cue").read_bytes()


# -- the job --

def test_the_job_splits_proves_and_reports(qtbot, window, service, split, recycled, library_dir):
    folder = library_dir / "Albums" / HIDDEN
    page = open_sheet(qtbot, split, folder / SHEET)
    image, sheet = (folder / SOURCE).read_bytes(), (folder / SHEET).read_bytes()
    report = run(qtbot, split)
    assert report.startswith("Done: 4 tracks split, all identical to the original.")
    assert "Together, the tracks have the original's MD5" in report
    assert f"Written to {folder}:" in report and "The image and its cue sheet are as they were." in report
    assert recycled == [] and (folder / SOURCE).read_bytes() == image and (folder / SHEET).read_bytes() == sheet
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


def test_a_running_split_is_shown_instead_of_opening_another(qtbot, window, split, library_dir):
    split.job = window._start_job("Split", library_dir / "Albums" / HIDDEN)
    split.open(library_dir / "Albums" / "wav_image" / "image.cue")
    assert split.path is None and "still running" in window.toast.label.text()
    window.jobs.finish(split.job, "done", "")
    split.job = None


def test_back_while_a_split_runs(qtbot, window, split, library_dir):
    open_sheet(qtbot, split, library_dir / "Albums" / HIDDEN / SHEET)
    split.job = window._start_job("Split", library_dir / "Albums" / HIDDEN)
    split.page.show_running()
    assert split.page.back_button.isVisibleTo(split.page) and "goes on" in split.page.back_button.toolTip()
    split.page.back_button.click()
    assert window.center.currentWidget() is window.file_view and split.page.state == "running"
    window.jobs.finish(split.job, "done", "")
    split.job = None


# -- nothing remembered --

def test_no_split_state_is_saved(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"split_options": {"recycle": True}}), encoding="utf-8")
    settings = Settings.load(path)
    settings.save()
    assert not [key for key in json.loads(path.read_text(encoding="utf-8")) if key.startswith("split_")]


def test_one_image_split_twice_from_two_versions(qtbot, split, recycled, library_dir, monkeypatch):
    """Hugo's workflow (2026-10-17): the EAC set is kept, and each version of the sheet splits the same image."""
    folder = library_dir / "Albums" / "wav_image"
    open_sheet(qtbot, split, folder / "image.cue")
    run(qtbot, split)
    split.page.done_button.click()
    open_sheet(qtbot, split, folder / "image.cue")
    edit_cell(qtbot, split, 1, "start", "00:02:00")  # other split points
    monkeypatch.setattr(split, "choose_cue_file", lambda suggested: str(folder / "image.cue"))
    monkeypatch.setattr(split, "ask_replace_or_keep", lambda name: "keep")
    assert split.save_as() and split.path == folder / "image (2).cue"
    ready(qtbot, split)
    assert split.page.column_texts("name") == ["01 One (2).flac", "02 Two (2).flac"]
    run(qtbot, split)
    assert sorted(p.name for p in folder.iterdir()) == [
        "01 One (2).flac", "01 One.flac", "02 Two (2).flac", "02 Two.flac", "image (2).cue", "image.cue",
        "image.wav"]
    assert recycled == []  # the original sheet was never replaced


# -- M13: the source warning --

def test_a_missing_source_is_marked_until_one_is_chosen(qtbot, split, library_dir):
    folder = library_dir / "Albums" / "wav_image"
    (folder / "image.cue").write_text((folder / "image.cue").read_text(encoding="utf-8").replace(
        "image.wav", "Fixture Album.wav"), encoding="utf-8")
    page = open_sheet(qtbot, split, folder / "image.cue")
    assert split.source_warning() == ("This sheet points to Fixture Album.wav, which isn't in this folder. Choose "
                                      "its audio file.")
    assert "<b>This sheet points to Fixture Album.wav" in page.source_note.text()
    assert page.source.styleSheet() and not page.split_button.isEnabled() and not page.preview_button.isEnabled()
    assert page.save_as_button.isVisible() and not page.split_button.isVisible()  # the page fixes; it never splits
    index = page.source.findData("image.wav")
    page.source.setCurrentIndex(index)
    page.source.activated.emit(index)
    ready(qtbot, split)
    assert split.source_warning() is None and not page.source.styleSheet()
    assert "WAV, 44.1 kHz, 0:06" in page.source_note.text() and page.split_button.isEnabled()


@pytest.mark.parametrize(("change", "warning"), [
    (lambda doc: doc.set_file("x.ape"), "This sheet points to x.ape, which the split can't read (it reads FLAC, "
                                        "WAV and AIFF). Choose its audio file."),
    (lambda doc: doc._insert(len(doc.lines), 'FILE "b.flac" WAVE'),
     "This sheet names several files: the split needs one audio file for the whole album."),
])
def test_an_unsupported_or_multi_file_source_is_marked(qtbot, split, library_dir, change, warning):
    page = open_sheet(qtbot, split, library_dir / "Albums" / "wav_image" / "image.cue")
    change(split.doc)
    split._refresh()
    ready(qtbot, split)
    assert split.source_warning() == warning and page.source.styleSheet()
    assert not page.split_button.isEnabled()


def test_a_source_that_cant_be_read_is_marked(qtbot, split, library_dir):
    folder = library_dir / "Albums" / "wav_image"
    (folder / "image.wav").write_bytes(b"RIFF....WAVEjunk")
    page = open_sheet(qtbot, split, folder / "image.cue")
    assert split.source_warning().endswith("Choose its audio file.") and page.source.styleSheet()


# -- M13: New cue sheet… --

NEW_SHEET = "Fixture Artist - Split Album.cue"


def new_sheet(qtbot, split, audio):
    split.new_sheet(audio)
    ready(qtbot, split)
    return split.page


def test_the_page_edits_only(qtbot, window, split, library_dir):
    """Edit… and New cue sheet… end in Save, Save as and Preview in player (Hugo, M20): no Split, ticks or
    destination; a split starts only from a cue sheet's preview."""
    folder = library_dir / "Albums" / "wav_image"
    for page in (open_sheet(qtbot, split, folder / "image.cue"), new_sheet(qtbot, split, folder / "image.wav")):
        visible = [b.text() for b in (page.save_button, page.save_as_button, page.preview_button,
                                      page.split_button) if b.isVisible()]
        assert "Split" not in " ".join(visible) and page.save_as_button.isVisible()
        assert page.preview_button.isVisible()
        assert page.table.isColumnHidden(0) and not page.destination.isVisible()
        assert not page.browse_button.isVisible() and not page.split_note.isVisible()


def test_new_cue_sheet_from_the_metadata_tab(qtbot, window, service, library_dir, split):
    folder = library_dir / "Albums" / HIDDEN
    window.open_folder(folder_id(service, folder))
    window.file_view.select_ids([file_id(service, folder / SOURCE)])
    metadata = window.side_panel.metadata
    qtbot.waitUntil(lambda: metadata.snapshot is not None and metadata.new_cue_button.isVisible(), timeout=10000)
    assert metadata.new_cue_button.isEnabled()
    metadata.new_cue_button.click()
    ready(qtbot, split)
    assert window.center.currentWidget() is split.page and split.new and split.path == folder / NEW_SHEET
    # The source's tags: album artist, album.
    assert split.doc.text() == ('PERFORMER "Source Artist"\r\nTITLE "Source Album"\r\n'
                                f'FILE "{SOURCE}" WAVE\r\n  TRACK 01 AUDIO\r\n    INDEX 01 00:00:00\r\n')
    page = split.page
    assert "(new, not saved yet)" in page.heading.text() and not page.encoding.isEnabled()
    assert not page.save_note.isVisible()  # the Options window's Split box is off: Save asks where
    window.tools.cards.split_card.save_beside.setChecked(True)  # File > Options…, Split (SPEC §12)
    assert page.save_note.isVisible() and page.save_note.text() == f"Saves beside {SOURCE} (File > Options…, Split)"
    window.tools.cards.split_card.reset()
    assert not page.save_note.isVisible()
    assert not page.save_button.isEnabled()  # one track: not a sheet that could be split yet
    assert not page.split_button.isEnabled()


def test_a_new_sheet_saved_through_the_dialog(qtbot, window, split, library_dir, monkeypatch):
    folder = library_dir / "Albums" / HIDDEN
    page = new_sheet(qtbot, split, folder / SOURCE)
    split.add_track(0)
    ready(qtbot, split)
    assert page.save_button.isEnabled() and page.preview_button.isEnabled()
    proposed = []
    monkeypatch.setattr(split, "choose_cue_file", lambda suggested: proposed.append(suggested) or suggested)
    page.save_button.click()
    target = folder / NEW_SHEET
    assert proposed == [str(target)] and target.read_bytes().startswith(b"\xef\xbb\xbfPERFORMER")
    assert not split.new and split.path == target and not page.save_note.isVisible() and page.encoding.isEnabled()
    ready(qtbot, split)
    assert split.plan.cue == target and page.split_button.isEnabled()
    report = run(qtbot, split)  # M13's "Done when": a new sheet saved from the page splits
    assert report.startswith("Done: 2 tracks split, all identical to the original.")


def test_the_save_beside_setting_saves_at_once_and_never_replaces(qtbot, window, split, library_dir,
                                                                   monkeypatch):
    folder = library_dir / "Albums" / HIDDEN
    taken = folder / NEW_SHEET
    taken.write_bytes(b"an earlier sheet")
    window.tools.cards.split_card.save_beside.setChecked(True)  # File > Options…, Split
    assert window.settings.save_cue_beside
    new_sheet(qtbot, split, folder / SOURCE)
    split.add_track(0)
    ready(qtbot, split)
    monkeypatch.setattr(split, "choose_cue_file", lambda suggested: pytest.fail("no dialog with the box"))
    assert split.save()
    assert taken.read_bytes() == b"an earlier sheet"
    assert split.path == folder / "Fixture Artist - Split Album (2).cue" and split.path.exists()
    split.page.back_button.click()
    new_sheet(qtbot, split, folder / SOURCE)
    assert split.page.save_note.isVisible()  # the setting stays
    window.tools.cards.split_card.reset()


def test_save_as_on_a_new_sheet_always_asks(qtbot, window, split, library_dir, monkeypatch):
    folder = library_dir / "Albums" / HIDDEN
    window.tools.cards.split_card.save_beside.setChecked(True)
    new_sheet(qtbot, split, folder / SOURCE)
    split.add_track(0)
    ready(qtbot, split)
    asked = []
    monkeypatch.setattr(split, "choose_cue_file", lambda suggested: asked.append(suggested) or "")
    assert not split.save_as() and asked


def test_leaving_a_new_sheet_asks_only_after_a_change(qtbot, window, split, library_dir, monkeypatch):
    folder = library_dir / "Albums" / HIDDEN
    asked = []
    monkeypatch.setattr(split, "ask_leave", lambda name: asked.append(name) or "cancel")
    page = new_sheet(qtbot, split, folder / SOURCE)
    page.back_button.click()
    assert asked == [] and window.center.currentWidget() is window.file_view
    page = new_sheet(qtbot, split, folder / SOURCE)
    split.add_track(0)
    ready(qtbot, split)
    page.back_button.click()
    assert asked == [NEW_SHEET] and window.center.currentWidget() is page
    monkeypatch.setattr(split, "ask_leave", lambda name: "discard")


def test_splitting_a_new_sheet_asks_to_save_it_first(qtbot, split, library_dir, monkeypatch):
    folder = library_dir / "Albums" / HIDDEN
    page = new_sheet(qtbot, split, folder / SOURCE)
    split.add_track(0)
    ready(qtbot, split)
    asked = []
    monkeypatch.setattr(split, "ask_save_first", lambda name: asked.append(name) or "cancel")
    split._split_after_plan = False
    page.split_button.setEnabled(True)  # the page enables Split from the plan; the question comes first
    page.split_button.click()
    assert asked == [NEW_SHEET] and page.state == "editing"


# -- M13: Preview in player --

def test_preview_opens_the_current_sheet_with_its_hidden_track(qtbot, window, split, library_dir, tmp_path):
    launched = []
    window.launch = launched.append
    window.playlist_dir = tmp_path / "playlists"
    folder = library_dir / "Albums" / HIDDEN
    page = open_sheet(qtbot, split, folder / SHEET)
    edit_cell(qtbot, split, 3, "title", "A Duet")  # unsaved edits are previewed
    page.cell(1, "tick").setCheckState(page.cell(1, "tick").checkState().Unchecked)  # unticked tracks too
    ready(qtbot, split)
    page.preview_button.click()
    qtbot.waitUntil(lambda: bool(launched), timeout=5000)
    text = launched[0].read_bytes().decode("utf-8-sig")
    assert f'FILE "{folder / SOURCE}" WAVE' in text
    titles = [line.strip() for line in text.splitlines() if line.startswith("    TITLE")]
    assert titles == ['TITLE "Hidden track"', 'TITLE "Opening"', 'TITLE "Second Song"', 'TITLE "A Duet"']
    assert [line.strip() for line in text.splitlines() if "INDEX" in line] == [
        "INDEX 01 00:00:00", "INDEX 01 00:03:00", "INDEX 01 00:06:37", "INDEX 01 00:09:00"]  # a gap stays before
    assert b"A Duet" not in (folder / SHEET).read_bytes()


# -- M13: Fill from track lengths… --

def test_fill_from_track_lengths(qtbot, split, library_dir, monkeypatch):
    folder = library_dir / "Albums" / HIDDEN
    page = open_sheet(qtbot, split, folder / SHEET)
    asked = []

    def answer(lengths, first, audio):
        asked.append((lengths, first, audio))
        return [75, 150, 225, 300]  # five tracks of 1, 2, 3 and 4 s, then the rest

    monkeypatch.setattr(split, "ask_track_lengths", answer)
    assert page.fill_button.isEnabled()
    page.fill_button.click()
    ready(qtbot, split)
    assert asked == [([3 * 75 + 37, 2 * 75 + 38], 3 * 75, 12 * 75)]
    assert [v.values["start"] for v in split.doc.tracks()] == ["00:03:00", "00:04:00", "00:06:00", "00:09:00",
                                                               "00:13:00"]
    assert split.doc.tracks()[1].values["gap"] == "" and split.doc.tracks()[1].values["title"] == "Second Song"
    assert split.doc.modified
    monkeypatch.setattr(split, "ask_track_lengths", lambda *args: None)  # Cancel changes nothing
    before = split.doc.text()
    page.fill_button.click()
    assert split.doc.text() == before


def test_the_lengths_dialog(qtbot):
    from music_desk.ui.track_lengths import TrackLengthsDialog

    dialog = TrackLengthsDialog([75 * 60, None, 75 * 30], 0, 75 * 600)
    qtbot.addWidget(dialog)
    ok = dialog.buttons.button(dialog.buttons.StandardButton.Ok)
    assert dialog.count.value() == 4 and dialog.values() == ["1:00", "", "0:30"]
    assert not ok.isEnabled() and dialog.reason.text() == "Track 2's length isn't m:ss or m:ss:ff."
    dialog.edits[1].setText("2:00:30")
    assert ok.isEnabled() and dialog.rest.text() == "Track 4: the rest, 6:29:45."
    dialog.count.setValue(2)
    assert dialog.deleted.text() == "Tracks 3–4 will be deleted." and dialog.values() == ["1:00"]
    dialog.count.setValue(5)
    assert dialog.values() == ["1:00", "2:00:30", "0:30", ""] and dialog.deleted.isHidden()
    dialog.edits[3].setText("9:00")
    assert not ok.isEnabled() and "past the end of the audio" in dialog.reason.text()
