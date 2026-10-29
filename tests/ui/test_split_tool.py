"""Cue sheets in the app (SPEC §22, §35): the Play, Split and Edit… buttons on every .cue preview, which open the
right-click windows; the cue editor's window: its fields and table, edits, validation marks and the disabled
buttons, the source picker, the encoding picker, Save / Save as (a saved-over sheet goes to a recorder, never the
real Recycle Bin), the unsaved-edits questions, New cue sheet… and Preview in player; and the split, in the "Split
this cue sheet" recap window, with the real bundled flac on copied fixtures: the proof, the lock, Stop, and several
splits of one image. A split is just a split: it never touches the image or its sheet."""

import json
import shutil

import pytest

from music_desk.context_menu.split_recap import SplitRecapWindow
from music_desk.context_menu.tool_host import CueEditorWindow
from music_desk.core.flac import Cancelled
from music_desk.core.index.store import path_of
from music_desk.core.tags import TaggedFile
from music_desk.services import split_job
from music_desk.settings import Settings
from music_desk.ui import theme
from music_desk.ui.split_page import COLUMN
from music_desk.ui.tools.tool_cards import ToolCards

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
def editor(qtbot, service):
    """The cue editor's window, on the library's locks (as app.Starter makes it). Unsaved edits left by a test are
    discarded; tests that check the question answer it."""
    cards = ToolCards(Settings())
    window = CueEditorWindow(cards, service.locks)
    window.controller.ask_leave = lambda _name: "discard"
    window.show()
    yield window
    window.shutdown()
    window.hide()
    window.deleteLater()
    cards.dispose()


@pytest.fixture
def recycled(editor):
    """Every saved-over cue sheet sent to the Recycle Bin, recorded: nothing really moves."""
    paths = []
    editor.controller.recycle = paths.append
    editor.controller.check_recycle = lambda _path: None
    return paths


@pytest.fixture
def split(editor, recycled):
    """The editor's controller."""
    return editor.controller


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


def recap(qtbot, editor, service, cue) -> SplitRecapWindow:
    """"Split this cue sheet…"'s window on `cue`, on the recap (or its refusal)."""
    window = SplitRecapWindow(editor.tools.cards, service.locks)
    qtbot.addWidget(window)
    window.start([str(cue)])
    qtbot.waitUntil(lambda: window.state in ("recap", "error"), timeout=20000)
    return window


def run(qtbot, window: SplitRecapWindow) -> str:
    window.split_button.click()
    qtbot.waitUntil(lambda: window.state in ("result", "error"), timeout=30000)
    return window.report.toPlainText()


# -- the way in: the cue sheet's preview opens the right-click windows (SPEC §35) --

def show_cue(qtbot, window, service, cue):
    window.open_folder(folder_id(service, cue.parent))
    preview = window.side_panel.preview
    window.file_view.select_ids([file_id(service, cue)])
    qtbot.waitUntil(lambda: preview.split_button.isVisible(), timeout=10000)
    return preview


def opened(qtbot, open_app, kind):
    """The window the click opened. Not given to qtbot: the Starter frees it (test_right_click_split_trim_alone.py)."""
    window = open_app.entry_windows[-1]
    assert isinstance(window, kind) and window.parent() is None
    return window


def test_the_buttons_on_every_cue_preview(qtbot, open_app, service, library_dir):
    main = open_app.window
    albums = library_dir / "Albums"
    preview = show_cue(qtbot, main, service, albums / "sjis" / "album.cue")
    # Play, Split and Edit… (Hugo, M19): Play is Open, renamed for a cue sheet.
    assert [b.text() for b in (preview.open_button, preview.split_button, preview.edit_button)] == [
        "Play", "Split", "Edit…"]
    preview.edit_button.click()  # the cue editor's window, as "Edit this cue sheet…"
    editor = opened(qtbot, open_app, CueEditorWindow)
    ready(qtbot, editor.controller)
    assert editor.controller.path == albums / "sjis" / "album.cue"
    assert main.center.currentWidget() is main.file_view
    editor.controller.ask_leave = lambda _name: "discard"


def test_split_opens_the_recap_then_splits(qtbot, open_app, service, library_dir):
    folder = library_dir / "Albums" / "wav_image"
    preview = show_cue(qtbot, open_app.window, service, folder / "image.cue")
    preview.split_button.click()  # "Split this cue sheet…"'s recap: Split or Cancel
    window = opened(qtbot, open_app, SplitRecapWindow)
    qtbot.waitUntil(lambda: window.state == "recap", timeout=20000)
    report = run(qtbot, window)
    assert report.startswith("Done: 2 tracks split, all identical to the original."), report
    assert (folder / "01 One.flac").exists() and (folder / "02 Two.flac").exists()
    assert (folder / "image.wav").exists() and (folder / "image.cue").exists()  # a split touches nothing else


def test_a_sheet_that_needs_a_look_isnt_split(qtbot, open_app, service, library_dir):
    folder = library_dir / "Albums" / "sjis"
    before = sorted(p.name for p in folder.iterdir())
    preview = show_cue(qtbot, open_app.window, service, folder / "album.cue")
    preview.split_button.click()
    window = opened(qtbot, open_app, SplitRecapWindow)
    qtbot.waitUntil(lambda: window.state == "recap", timeout=20000)
    assert not window.split_button.isEnabled() and "Windows-1252" in window.message.text()
    assert sorted(p.name for p in folder.iterdir()) == before


def test_a_windows_1252_sheet_splits(qtbot, editor, service, library_dir):
    """What EAC writes on a Western Windows (no BOM, not UTF-8): Auto reads it as Windows-1252 and splits."""
    folder = library_dir / "Albums" / "wav_image"
    sheet = (folder / "image.cue").read_text(encoding="utf-8").replace('TITLE "One"', 'TITLE "Café"')
    (folder / "image.cue").write_bytes(sheet.encode("cp1252"))
    report = run(qtbot, recap(qtbot, editor, service, folder / "image.cue"))
    assert report.startswith("Done: 2 tracks split"), report
    assert (folder / "01 Café.flac").exists()


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

# -- the cue editor --

def test_the_page_shows_the_sheet_and_its_plan(qtbot, split, library_dir):
    page = open_sheet(qtbot, split, library_dir / "Albums" / HIDDEN / SHEET)
    assert page.source.currentData() == SOURCE and "FLAC, 44.1 kHz, 0:12" in page.source_note.text()
    assert page.album_edits["performer"].text() == "Fixture Artist"
    assert (page.album_edits["genre"].text(), page.album_edits["date"].text()) == ("Test Rock", "2024")
    assert page.album_edits["disc"].text() == "1" and page.album_edits["disc_total"].text() == "2"
    assert page.album_other.toPlainText() == 'REM DISCID 8C0B6A0B\nREM COMMENT "ExactAudioCopy v1.6"'
    assert page.column_texts("number") == ["0", "1", "2", "3"]  # the audible hidden track is row 0
    assert page.column_texts("name") == ["00 Hidden track.flac", "01 Opening.flac", "02 Second Song.flac",
                                         "03 Duet.flac"]
    assert page.column_texts("length") == ["00:03:00", "00:03:37", "00:02:38", "00:03:00"]
    assert page.column_texts("gap")[2] == "00:06:00"
    assert page.preview_button.isEnabled()
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
    assert not page.preview_button.isEnabled()
    assert "Track 2 must start after track 1." in page.problems.text()
    edit_cell(qtbot, split, 2, "start", "6:37")  # not a cue time
    assert "isn't a cue time" in page.cell(2, "start").toolTip()
    edit_cell(qtbot, split, 2, "start", "0:6:37")  # back where it was, typed differently
    assert page.cell(2, "start").text() == "00:06:37" and page.preview_button.isEnabled()
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


def test_names_get_a_number_instead_of_a_refusal(qtbot, split, library_dir):
    page = open_sheet(qtbot, split, library_dir / "Albums" / "name_clash" / "image.cue")
    assert page.column_texts("name") == ["01 One.flac", "02 Two (2).flac"]
    assert "02 Two.flac is taken (it already exists)" in page.notes.text()


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


def test_a_save_in_a_locked_folder_is_refused(qtbot, service, split, library_dir):
    folder = library_dir / "Albums" / HIDDEN
    open_sheet(qtbot, split, folder / SHEET)
    edit_cell(qtbot, split, 3, "title", "A Duet")
    lock = service.locks.acquire([folder], "ReplayGain")
    try:
        assert not split.save() and b"A Duet" not in (folder / SHEET).read_bytes()
    finally:
        service.locks.release(lock)


def test_a_character_the_encoding_cant_hold(qtbot, split, library_dir, monkeypatch):
    sheet = library_dir / "Albums" / "sjis" / "album.cue"
    open_sheet(qtbot, split, sheet)  # read as Windows-1252
    edit_cell(qtbot, split, 0, "title", "\u65e5\u672c")
    asked = []
    monkeypatch.setattr(split, "confirm_utf8", lambda encoding, chars: asked.append((encoding, chars)) or True)
    assert split.save()
    assert asked == [("Windows-1252", ["\u65e5", "\u672c"])] and sheet.read_bytes().startswith(b"\xef\xbb\xbf")


# -- the split, in the recap window --


def test_the_split_proves_and_reports(qtbot, editor, service, library_dir):
    folder = library_dir / "Albums" / HIDDEN
    image, sheet = (folder / SOURCE).read_bytes(), (folder / SHEET).read_bytes()
    report = run(qtbot, recap(qtbot, editor, service, folder / SHEET))
    assert report.startswith("Done: 4 tracks split, all identical to the original.")
    assert "Together, the tracks have the original's MD5" in report
    assert f"Written to {folder}:" in report and "The image and its cue sheet are as they were." in report
    assert (folder / SOURCE).read_bytes() == image and (folder / SHEET).read_bytes() == sheet
    assert sorted(p.name for p in folder.iterdir()) == [
        "00 Hidden track.flac", "01 Opening.flac", "02 Second Song.flac", "03 Duet.flac", SOURCE, SHEET]
    assert TaggedFile(folder / "03 Duet.flac").fields().artist == "Fixture Artist & Guest"
    assert service.locks.held() == []


def test_stop_leaves_the_album_as_it_was(qtbot, editor, service, library_dir, monkeypatch):
    class Stopped(split_job.FlacRunner):
        def encode(self, *args, **kwargs):
            raise Cancelled()

    monkeypatch.setattr(split_job, "FlacRunner", Stopped)
    folder = library_dir / "Albums" / HIDDEN
    before = sorted(p.name for p in folder.iterdir())
    report = run(qtbot, recap(qtbot, editor, service, folder / SHEET))
    assert report.startswith("Stopped: nothing left half-done.")
    assert sorted(p.name for p in folder.iterdir()) == before


def test_one_image_split_twice_from_two_versions(qtbot, editor, service, split, recycled, library_dir,
                                                 monkeypatch):
    """Hugo's workflow (2026-10-17): the EAC set is kept, and each version of the sheet splits the same image."""
    folder = library_dir / "Albums" / "wav_image"
    run(qtbot, recap(qtbot, editor, service, folder / "image.cue"))
    open_sheet(qtbot, split, folder / "image.cue")
    edit_cell(qtbot, split, 1, "start", "00:02:00")  # other split points, in another version of the sheet
    monkeypatch.setattr(split, "choose_cue_file", lambda suggested: str(folder / "image.cue"))
    monkeypatch.setattr(split, "ask_replace_or_keep", lambda name: "keep")
    assert split.save_as() and split.path == folder / "image (2).cue"
    ready(qtbot, split)
    assert split.page.column_texts("name") == ["01 One (2).flac", "02 Two (2).flac"]
    run(qtbot, recap(qtbot, editor, service, folder / "image (2).cue"))
    assert sorted(p.name for p in folder.iterdir()) == [
        "01 One (2).flac", "01 One.flac", "02 Two (2).flac", "02 Two.flac", "image (2).cue", "image.cue",
        "image.wav"]
    assert recycled == []  # the original sheet was never replaced


# -- nothing remembered --


def test_no_split_state_is_saved(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"split_options": {"recycle": True}}), encoding="utf-8")
    settings = Settings.load(path)
    settings.save()
    assert not [key for key in json.loads(path.read_text(encoding="utf-8")) if key.startswith("split_")]


# -- M13: the source warning --


def test_a_missing_source_is_marked_until_one_is_chosen(qtbot, split, library_dir):
    folder = library_dir / "Albums" / "wav_image"
    (folder / "image.cue").write_text((folder / "image.cue").read_text(encoding="utf-8").replace(
        "image.wav", "Fixture Album.wav"), encoding="utf-8")
    page = open_sheet(qtbot, split, folder / "image.cue")
    assert split.source_warning() == ("This sheet points to Fixture Album.wav, which isn't in this folder. Choose "
                                      "its audio file.")
    assert "<b>This sheet points to Fixture Album.wav" in page.source_note.text()
    assert theme.is_invalid(page.source) and not page.preview_button.isEnabled() and page.save_as_button.isVisible()
    index = page.source.findData("image.wav")
    page.source.setCurrentIndex(index)
    page.source.activated.emit(index)
    ready(qtbot, split)
    assert split.source_warning() is None and not theme.is_invalid(page.source)
    assert "WAV, 44.1 kHz, 0:06" in page.source_note.text() and page.preview_button.isEnabled()


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
    assert split.source_warning() == warning and theme.is_invalid(page.source)
    assert not page.preview_button.isEnabled()


def test_a_source_that_cant_be_read_is_marked(qtbot, split, library_dir):
    folder = library_dir / "Albums" / "wav_image"
    (folder / "image.wav").write_bytes(b"RIFF....WAVEjunk")
    page = open_sheet(qtbot, split, folder / "image.cue")
    assert split.source_warning().endswith("Choose its audio file.") and theme.is_invalid(page.source)


# -- M13: New cue sheet… --

NEW_SHEET = "Fixture Artist - Split Album.cue"


def new_sheet(qtbot, split, audio):
    split.new_sheet(audio)
    ready(qtbot, split)
    return split.page


def test_the_page_edits_only(qtbot, split, library_dir):
    """The editor and New cue sheet… end in Save, Save as and Preview in player (Hugo, M20): no Split, ticks or
    destination; splitting is "Split this cue sheet…"'s window."""
    folder = library_dir / "Albums" / "wav_image"
    for page in (open_sheet(qtbot, split, folder / "image.cue"), new_sheet(qtbot, split, folder / "image.wav")):
        visible = [b.text() for b in (page.save_button, page.save_as_button, page.preview_button, page.done_button)
                   if b.isVisible()]
        assert visible == ["Save", "Save as…", "Preview in player"]
        assert "tick" not in COLUMN and not hasattr(page, "destination") and not hasattr(page, "split_button")


def test_new_cue_sheet_from_the_metadata_tab(qtbot, open_app, service, library_dir):
    """New cue sheet… opens the cue editor's window on a new sheet (SPEC §35)."""
    main = open_app.window
    folder = library_dir / "Albums" / HIDDEN
    main.open_folder(folder_id(service, folder))
    main.file_view.select_ids([file_id(service, folder / SOURCE)])
    metadata = main.side_panel.metadata
    qtbot.waitUntil(lambda: metadata.snapshot is not None and metadata.new_cue_button.isVisible(), timeout=10000)
    assert metadata.new_cue_button.isEnabled()
    metadata.new_cue_button.click()
    editor = opened(qtbot, open_app, CueEditorWindow)
    split = editor.controller
    split.ask_leave = lambda _name: "discard"
    ready(qtbot, split)
    assert main.center.currentWidget() is main.file_view
    assert split.new and split.path == folder / NEW_SHEET
    # The source's tags: album artist, album.
    assert split.doc.text() == ('PERFORMER "Source Artist"\r\nTITLE "Source Album"\r\n'
                                f'FILE "{SOURCE}" WAVE\r\n  TRACK 01 AUDIO\r\n    INDEX 01 00:00:00\r\n')
    page = split.page
    assert "(new, not saved yet)" in page.heading.text() and not page.encoding.isEnabled()
    assert not page.save_note.isVisible()  # the Split options' box is off: Save asks where
    cards = main.tools.cards  # the process's one copy of the settings
    cards.split_card.save_beside.setChecked(True)
    assert page.save_note.isVisible() and page.save_note.text() == f"Saves beside {SOURCE} (the Split options)"
    cards.split_card.reset()
    assert not page.save_note.isVisible()
    assert not page.save_button.isEnabled()  # one track: not a sheet that could be split yet


def test_a_new_sheet_saved_through_the_dialog_splits(qtbot, editor, service, split, library_dir, monkeypatch):
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
    assert split.plan.cue == target
    report = run(qtbot, recap(qtbot, editor, service, target))  # M13's "Done when": a new sheet, saved, splits
    assert report.startswith("Done: 2 tracks split, all identical to the original.")


def test_the_save_beside_setting_saves_at_once_and_never_replaces(qtbot, editor, split, library_dir,
                                                                   monkeypatch):
    folder = library_dir / "Albums" / HIDDEN
    taken = folder / NEW_SHEET
    taken.write_bytes(b"an earlier sheet")
    editor.tools.cards.split_card.save_beside.setChecked(True)  # the Split options
    assert editor.settings.save_cue_beside
    new_sheet(qtbot, split, folder / SOURCE)
    split.add_track(0)
    ready(qtbot, split)
    monkeypatch.setattr(split, "choose_cue_file", lambda suggested: pytest.fail("no dialog with the box"))
    assert split.save()
    assert taken.read_bytes() == b"an earlier sheet"
    assert split.path == folder / "Fixture Artist - Split Album (2).cue" and split.path.exists()
    new_sheet(qtbot, split, folder / SOURCE)
    assert split.page.save_note.isVisible()  # the setting stays
    editor.tools.cards.split_card.reset()


def test_save_as_on_a_new_sheet_always_asks(qtbot, editor, split, library_dir, monkeypatch):
    folder = library_dir / "Albums" / HIDDEN
    editor.tools.cards.split_card.save_beside.setChecked(True)
    new_sheet(qtbot, split, folder / SOURCE)
    split.add_track(0)
    ready(qtbot, split)
    asked = []
    monkeypatch.setattr(split, "choose_cue_file", lambda suggested: asked.append(suggested) or "")
    assert not split.save_as() and asked


def test_leaving_a_new_sheet_asks_only_after_a_change(qtbot, split, library_dir, monkeypatch):
    folder = library_dir / "Albums" / HIDDEN
    asked = []
    monkeypatch.setattr(split, "ask_leave", lambda name: asked.append(name) or "cancel")
    new_sheet(qtbot, split, folder / SOURCE)
    assert split.can_leave() and asked == []  # closing the window wouldn't ask
    split.add_track(0)
    ready(qtbot, split)
    assert not split.can_leave() and asked == [NEW_SHEET]
    monkeypatch.setattr(split, "ask_leave", lambda name: "discard")


# -- M13: Preview in player --


def test_preview_opens_the_current_sheet_with_its_hidden_track(qtbot, editor, split, library_dir, tmp_path):
    launched = []
    editor.launch = launched.append
    editor.playlist_dir = tmp_path / "playlists"
    folder = library_dir / "Albums" / HIDDEN
    page = open_sheet(qtbot, split, folder / SHEET)
    edit_cell(qtbot, split, 3, "title", "A Duet")  # unsaved edits are previewed
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
