"""The Metadata tab for one file (SPEC §7): load and save round trips for every fixture format, only the changed
fields written, the ID3 version kept, the dirty marker, revert, the leave prompt, and no tag I/O on the UI
thread."""

import shutil
import threading

import pytest
from browser_helpers import column, entry
from mutagen.id3 import ID3
from PySide6.QtCore import Qt

from music_desk.core import metadata
from music_desk.core.path_locks import PathLocks
from music_desk.core.tags import FIELD_NAMES, TaggedFile
from music_desk.services.tag_io import TagIO
from music_desk.services.tag_writer import TagWriter
from music_desk.ui.side_panel.panel import SelectedItem, SidePanel

FORMATS = ["tone.flac", "tone.ogg", "tone.oga", "tone.opus", "tone.mp3", "tone.mp2", "tone.wav", "tone.aiff",
           "tone_aac.m4a", "tone_alac.m4a", "tone.m4b", "tone.wv", "tone.tta", "tone.wma"]


def raw_keys(path) -> set[str]:
    """Every tag key in the file, as mutagen stores it (ignoring case)."""
    import mutagen

    tags = mutagen.File(path).tags
    return {str(key).casefold() for key in tags.keys()} if tags is not None else set()


@pytest.fixture
def locks():
    return PathLocks()


@pytest.fixture
def panel(qtbot, locks):
    io = TagIO()
    writer = TagWriter(locks)
    panel = SidePanel(io, writer)
    qtbot.addWidget(panel)
    panel.show()
    yield panel
    writer.close()
    io.close()


def load(qtbot, panel, path):
    tab = panel.metadata
    with qtbot.waitSignal(tab.io.loaded, timeout=10000):
        panel.show_selection([SelectedItem(path, False, True)])
    qtbot.waitUntil(lambda: tab.snapshot is not None, timeout=5000)
    return tab


def save(qtbot, tab):
    with qtbot.waitSignal(tab.fileSaved, timeout=10000):
        tab.save()
    qtbot.waitUntil(lambda: not tab.saving, timeout=5000)


@pytest.mark.parametrize("name", FORMATS)
def test_round_trip(qtbot, panel, fixture_copy, name):
    path = fixture_copy(f"formats/{name}")
    before_fields = TaggedFile(path).fields()
    before_keys = raw_keys(path)
    tab = load(qtbot, panel, path)
    assert tab.edits["title"].text() == before_fields.title
    assert tab.edits["artist"].text() == "Fixture Artist"
    assert tab.edits["track"].text() == "1"
    assert tab.name_label.text() == name

    tab.edits["title"].setText("New Title")
    tab.edits["title"].textEdited.emit("New Title")
    tab.lyrics.setPlainText("[00:01.00]First line\nSecond line")
    assert tab.dirty and panel.tabs.tabText(0) == "Metadata •"
    assert tab.changes() == {"title": "New Title", "lyrics": "[00:01.00]First line\nSecond line"}
    save(qtbot, tab)

    after = TaggedFile(path).fields()
    assert after.title == "New Title" and after.lyrics == "[00:01.00]First line\nSecond line"
    for field in FIELD_NAMES:
        if field not in ("title", "lyrics"):
            assert getattr(after, field) == getattr(before_fields, field), field
    assert not tab.dirty and panel.tabs.tabText(0) == "Metadata"
    assert tab.edits["title"].text() == "New Title"  # the tab shows the saved file
    assert raw_keys(path) >= before_keys  # no other tag lost


def test_id3v23_is_kept(qtbot, panel, fixture_copy):
    path = fixture_copy("replaygain/Fixture Artist/Album B/02 Quiet.mp3")
    assert ID3(path).version[:2] == (2, 3)
    tab = load(qtbot, panel, path)
    assert tab.details["tags"].text() == "ID3 (ID3v2.3)"
    tab.edits["genre"].setText("Ambient")
    tab.edits["genre"].textEdited.emit("Ambient")
    save(qtbot, tab)
    assert ID3(path).version[:2] == (2, 3)
    assert TaggedFile(path).fields().genre == "Ambient"


def test_clearing_and_numbers(qtbot, panel, fixture_copy):
    path = fixture_copy("formats/tone.flac")
    tab = load(qtbot, panel, path)
    tab.edits["genre"].setText("")
    tab.edits["genre"].textEdited.emit("")
    tab.edits["disc_total"].setText("")
    tab.edits["disc_total"].textEdited.emit("")
    qtbot.keyClicks(tab.edits["track"], "x2")  # letters are refused by the validator
    assert tab.edits["track"].text() == "12"
    assert tab.changes() == {"genre": None, "disc_total": None, "track": 12}
    save(qtbot, tab)
    fields = TaggedFile(path).fields()
    assert (fields.genre, fields.disc_total, fields.track) == (None, None, 12)


def test_revert_with_escape(qtbot, panel, fixture_copy):
    tab = load(qtbot, panel, fixture_copy("formats/tone.flac"))
    tab.edits["album"].setFocus()
    qtbot.keyClicks(tab.edits["album"], " (Deluxe)")
    assert tab.dirty
    qtbot.keyClick(tab.edits["album"], Qt.Key.Key_Escape)
    assert not tab.dirty and tab.edits["album"].text() == "Fixture Formats"


def test_unchanged_lyrics_with_crlf_are_not_rewritten(qtbot, panel, fixture_copy):
    path = fixture_copy("formats/tone.flac")
    tagged = TaggedFile(path)
    tagged.set_fields({"lyrics": "one\r\ntwo"})
    tagged.save()
    tab = load(qtbot, panel, path)
    assert tab.lyrics.toPlainText() == "one\ntwo" and tab.changes() == {}


def test_raw_aac_is_read_only(qtbot, panel, fixture_copy):
    tab = load(qtbot, panel, fixture_copy("formats/tone.aac"))
    assert not tab.edits["title"].isEnabled() and not tab.lyrics.isEnabled()
    assert "no tags" in tab.note.text() and tab.note.isVisibleTo(tab)
    assert tab.changes() == {}


def test_wma_has_no_track_total(qtbot, panel, fixture_copy):
    """SPEC §1: WMA has no standard track total, so the field is disabled with the reason."""
    tab = load(qtbot, panel, fixture_copy("formats/tone.wma"))
    assert not tab.edits["track_total"].isEnabled() and "WMA" in tab.edits["track_total"].toolTip()
    assert tab.edits["track"].isEnabled() and tab.edits["disc_total"].isEnabled()
    tab = load(qtbot, panel, fixture_copy("formats/tone.flac"))
    assert tab.edits["track_total"].isEnabled()


def test_details_and_folder_cover(qtbot, panel, fixture_copy, fixtures_dir):
    path = fixture_copy("formats/tone.flac")
    shutil.copy2(fixtures_dir / "cover.jpg", path.parent / "Folder.JPG")
    tab = load(qtbot, panel, path)
    assert tab.format_label.text().startswith("FLAC 16-bit/44.1 kHz · 0:01 · ")
    assert tab.cover_source.text() == "Cover from Folder.JPG" and not tab.cover.pixmap().isNull()
    assert tab.details["sample_rate"].text() == "44,100 Hz"
    assert tab.details["rg_track"].text() == "–"


def test_embedded_cover(qtbot, panel, fixture_copy):
    tab = load(qtbot, panel, fixture_copy("edge/edge.flac"))
    assert tab.cover_source.text() == "Embedded cover"


def test_unreadable_file(qtbot, panel, tmp_path):
    path = tmp_path / "broken.flac"
    path.write_bytes(b"not a flac")
    with qtbot.waitSignal(panel.metadata.io.loaded, timeout=5000):
        panel.show_selection([SelectedItem(path, False, True)])
    assert "couldn't be read" in panel.metadata.message.text()


def test_selection_messages(panel, tmp_path):
    panel.show_selection([])
    assert "Select a file" in panel.metadata.message.text()
    panel.show_selection([SelectedItem(tmp_path, True, False)])
    assert "folder" in panel.metadata.message.text()
    panel.show_selection([SelectedItem(tmp_path / "a.txt", False, False), SelectedItem(tmp_path, True, False)])
    assert "2 items selected, none of them audio files" in panel.metadata.message.text()
    panel.show_selection([SelectedItem(tmp_path / "notes.txt", False, False)])
    assert "isn't an audio file" in panel.metadata.message.text()


def test_save_failure_keeps_the_edits(qtbot, panel, fixture_copy, monkeypatch):
    tab = load(qtbot, panel, fixture_copy("formats/tone.flac"))

    def broken(*_args):
        raise PermissionError("the file is read-only")

    monkeypatch.setattr(metadata, "write_fields", broken)
    tab.edits["title"].setText("X")
    tab.edits["title"].textEdited.emit("X")
    with qtbot.waitSignal(tab.saveFailed, timeout=5000):
        tab.save()
    assert tab.dirty and tab.error_label.isVisibleTo(tab) and "read-only" in tab.error_label.text()
    assert tab.edits["title"].text() == "X" and tab.save_button.isEnabled()


def test_no_tag_io_on_the_ui_thread(qtbot, panel, fixture_copy, monkeypatch):
    threads = []
    real_read, real_write = metadata.read_snapshot, metadata.write_fields

    def read(path):
        threads.append(threading.current_thread())
        return real_read(path)

    def write(path, changes):
        threads.append(threading.current_thread())
        return real_write(path, changes)

    monkeypatch.setattr(metadata, "read_snapshot", read)
    monkeypatch.setattr(metadata, "write_fields", write)
    tab = load(qtbot, panel, fixture_copy("formats/tone.flac"))
    tab.edits["title"].setText("Y")
    tab.edits["title"].textEdited.emit("Y")
    save(qtbot, tab)
    assert threads and all(t is not threading.main_thread() for t in threads)


def test_stale_loads_are_ignored(qtbot, panel, fixture_copy):
    first, second = fixture_copy("formats/tone.flac"), fixture_copy("formats/tone.mp3")
    panel.show_selection([SelectedItem(first, False, True)])
    panel.show_selection([SelectedItem(second, False, True)])
    qtbot.waitUntil(lambda: panel.metadata.snapshot is not None, timeout=5000)
    qtbot.wait(100)
    assert panel.metadata.name_label.text() == "tone.mp3"


# -- in the main window --

def select_file(qtbot, window, name):
    proxy = window.file_view.proxy
    window.file_view.view.setCurrentIndex(proxy.index(column(window, "name").index(name), 0))
    qtbot.waitUntil(lambda: window.side_panel.metadata.snapshot is not None
                    and window.side_panel.metadata.name_label.text() == name, timeout=10000)
    return window.side_panel.metadata


def test_window_follows_the_selection_and_saves(qtbot, window, service):
    window.open_folder(entry(service, "Artist", "Album"))
    tab = select_file(qtbot, window, "Track 2.flac")
    assert tab.cover_source.text() == "Cover from cover.jpg"
    tab.edits["title"].setText("Second")
    tab.edits["title"].textEdited.emit("Second")
    save(qtbot, tab)
    row = column(window, "name").index("Track 2.flac")
    qtbot.waitUntil(lambda: column(window, "title")[row] == "Second", timeout=10000)  # the index caught up


@pytest.mark.parametrize("answer", ["cancel", "discard", "save"])
def test_leaving_with_unsaved_edits(qtbot, window, service, library_dir, monkeypatch, answer):
    window.open_folder(entry(service, "Artist", "Album"))
    tab = select_file(qtbot, window, "Track 2.flac")
    monkeypatch.setattr(tab, "ask_leave", lambda: answer)
    tab.edits["title"].setText("Edited")
    tab.edits["title"].textEdited.emit("Edited")
    proxy = window.file_view.proxy
    window.file_view.view.setCurrentIndex(proxy.index(column(window, "name").index("Track 3.mp3"), 0))
    path = library_dir / "Artist" / "Album" / "Track 2.flac"
    if answer == "cancel":
        assert tab.dirty and tab.name_label.text() == "Track 2.flac"
        assert window.file_view.selected_ids() == [entry(service, "Artist", "Album", "Track 2.flac")]
        return
    qtbot.waitUntil(lambda: tab.name_label.text() == "Track 3.mp3" and tab.snapshot is not None, timeout=10000)
    if answer == "save":
        qtbot.waitUntil(lambda: TaggedFile(path).fields().title == "Edited", timeout=10000)
    else:
        assert TaggedFile(path).fields().title == "Tone tone.flac"


def test_cancel_keeps_the_folder(window, service, monkeypatch, qtbot):
    album = entry(service, "Artist", "Album")
    window.open_folder(album)
    tab = select_file(qtbot, window, "Track 2.flac")
    monkeypatch.setattr(tab, "ask_leave", lambda: "cancel")
    tab.edits["title"].setText("Edited")
    tab.edits["title"].textEdited.emit("Edited")
    window.open_folder(entry(service, "Other"))
    assert window.folder_id == album and tab.dirty


# -- TagWriter and locks --

def test_a_locked_file_is_read_only(qtbot, panel, locks, fixture_copy):
    path = fixture_copy("formats/tone.flac")
    tab = load(qtbot, panel, path)
    token = locks.acquire([path.parent], "ReplayGain (album)")
    qtbot.waitUntil(lambda: tab.locked_by == "ReplayGain (album)", timeout=3000)
    assert tab.edits["title"].isReadOnly() and tab.lyrics.isReadOnly()
    assert tab.edits["title"].toolTip() == "Read-only while ReplayGain (album) is running."
    assert tab.note.isVisibleTo(tab) and not tab.save_button.isEnabled()
    locks.release(token)
    qtbot.waitUntil(lambda: tab.locked_by is None, timeout=3000)
    assert not tab.edits["title"].isReadOnly() and not tab.note.isVisibleTo(tab)


def test_a_locked_write_is_refused(qtbot, locks, fixture_copy):
    path = fixture_copy("formats/tone.flac")
    writer = TagWriter(locks)
    try:
        with locks.lock([path.parent], "Tidy album"), qtbot.waitSignal(writer.written, timeout=5000) as blocker:
            writer.write(path, {"title": "Nope"})
        result = blocker.args[0]
        assert result.snapshot is None and "in use by Tidy album" in result.error
        assert TaggedFile(path).fields().title == "Tone tone.flac"
    finally:
        writer.close()


def test_writes_are_serial_and_in_order(qtbot, locks, fixture_copy):
    path = fixture_copy("formats/tone.flac")
    written = []
    writer = TagWriter(locks, on_written=written.append)
    try:
        threads = []
        writer.written.connect(lambda r: threads.append(r.token))
        for n in range(5):
            writer.write(path, {"title": f"Take {n}"})
        qtbot.waitUntil(lambda: len(threads) == 5, timeout=10000)
        assert threads == [1, 2, 3, 4, 5]
        assert TaggedFile(path).fields().title == "Take 4"
        assert written == [str(path)] * 5
        assert locks.held() == []  # each save held its file only while writing
    finally:
        writer.close()


def test_saving_updates_the_index_without_a_rescan(qtbot, window, service, monkeypatch):
    def no_rescan(*_args, **_kwargs):
        raise AssertionError("a save shouldn't rescan the folder")

    window.open_folder(entry(service, "Artist", "Album"))
    album = entry(service, "Artist", "Album")
    lyrics_before = service.conn.execute("SELECT n_with_lyrics_tag FROM folder_stats WHERE entry_id = ?",
                                         (album,)).fetchone()[0]
    tab = select_file(qtbot, window, "Track 3.mp3")
    monkeypatch.setattr(service.indexer, "scan_path", no_rescan)
    monkeypatch.setattr(service.indexer.scanner, "scan_folder", no_rescan)
    tab.edits["title"].setText("Third")
    tab.edits["title"].textEdited.emit("Third")
    tab.lyrics.setPlainText("words")
    save(qtbot, tab)
    row = column(window, "name").index("Track 3.mp3")
    qtbot.waitUntil(lambda: column(window, "title")[row] == "Third", timeout=10000)
    qtbot.waitUntil(lambda: column(window, "lyrics")[row] == "✓", timeout=10000)
    assert service.conn.execute("SELECT n_with_lyrics_tag FROM folder_stats WHERE entry_id = ?",
                                (album,)).fetchone()[0] == lyrics_before + 1


def test_a_job_lock_reaches_the_window(qtbot, window, service):
    window.open_folder(entry(service, "Artist", "Album"))
    tab = select_file(qtbot, window, "Track 2.flac")
    with service.locks.lock([window.folder_path], "ReplayGain (album)"):
        qtbot.waitUntil(lambda: tab.edits["title"].isReadOnly(), timeout=3000)
    qtbot.waitUntil(lambda: not tab.edits["title"].isReadOnly(), timeout=3000)


# -- several files (SPEC §7, Multi-selection) --

MULTI_ALBUM = "album/Fixture Artist - Fixture Album"
MULTI_FILES = ("01 First.flac", "track2.flac", "lowercase_rg.mp3")


@pytest.fixture
def album(fixture_copy):
    folder = fixture_copy(MULTI_ALBUM)
    tagged = TaggedFile(folder / "track2.flac")  # make some fields differ
    tagged.set_fields({"genre": "Jazz", "comment": "second"})
    tagged.save()
    return folder


def load_many(qtbot, panel, paths, items=None):
    tab = panel.metadata
    with qtbot.waitSignal(tab.io.loadedMany, timeout=10000):
        panel.show_selection(items or [SelectedItem(p, False, True) for p in paths])
    qtbot.waitUntil(lambda: bool(tab.snapshots), timeout=5000)
    return tab


def type_into(tab, key, text):
    tab.edits[key].setText(text)
    tab.edits[key].textEdited.emit(text)


def save_many(qtbot, tab):
    with qtbot.waitSignal(tab.writer.batchWritten, timeout=10000):
        tab.save()


def test_shared_and_differing_values(qtbot, panel, album):
    tab = load_many(qtbot, panel, [album / n for n in MULTI_FILES])
    assert tab.multi and tab.name_label.text() == "3 files selected"
    assert tab.format_label.text() == "2 FLAC, 1 MP3"
    assert tab.edits["artist"].text() == "Fixture Artist" and tab.edits["artist"].placeholderText() == ""
    assert tab.edits["genre"].text() == "" and tab.edits["genre"].placeholderText() == "(multiple values)"
    for key in ("title", "track"):
        assert not tab.edits[key].isEnabled() and tab.edits[key].toolTip() == "Edited one file at a time."
    assert not tab.lyrics.isEnabled()
    assert all(tab.clear_actions[k].isVisible() for k in ("artist", "genre", "comment"))
    assert not tab.details_box.isVisibleTo(tab) and tab.changes() == {} and not tab.dirty


def test_editing_one_shared_field_leaves_the_rest(qtbot, panel, album):
    paths = [album / n for n in MULTI_FILES]
    before = {p: TaggedFile(p).fields() for p in paths}
    tab = load_many(qtbot, panel, paths)
    type_into(tab, "album", "Renamed Album")
    assert tab.changes() == {"album": "Renamed Album"} and tab.dirty
    save_many(qtbot, tab)
    for path in paths:
        after = TaggedFile(path).fields()
        assert after.album == "Renamed Album"
        for field in FIELD_NAMES:
            if field != "album":
                assert getattr(after, field) == getattr(before[path], field), (path.name, field)
    assert not tab.dirty and tab.edits["album"].text() == "Renamed Album"
    assert tab.edits["genre"].placeholderText() == "(multiple values)"  # reloaded, still different


def test_clear_on_all_versus_leave_alone(qtbot, panel, album):
    paths = [album / n for n in MULTI_FILES]
    tab = load_many(qtbot, panel, paths)
    type_into(tab, "genre", "")  # a "(multiple values)" field emptied by hand: left alone
    assert tab.changes() == {}
    tab.clear_actions["comment"].trigger()  # the clear button: removed from every file
    assert tab.edits["comment"].placeholderText() == "(cleared on all files)"
    type_into(tab, "year", "")  # a common value deleted: cleared, as in single-file editing
    assert tab.changes() == {"comment": None, "year": None}
    save_many(qtbot, tab)
    for path in paths:
        fields = TaggedFile(path).fields()
        assert fields.comment is None and fields.year is None
    assert TaggedFile(album / "track2.flac").fields().genre == "Jazz"


def test_typing_after_clear_sets_the_value(qtbot, panel, album):
    tab = load_many(qtbot, panel, [album / n for n in MULTI_FILES])
    tab.clear_actions["genre"].trigger()
    type_into(tab, "genre", "Rock")
    assert tab.changes() == {"genre": "Rock"}
    assert tab.edits["genre"].placeholderText() == "(multiple values)"


def test_numbers_in_multi(qtbot, panel, album):
    paths = [album / n for n in MULTI_FILES]
    tab = load_many(qtbot, panel, paths)
    type_into(tab, "disc", "2")
    type_into(tab, "disc_total", "2")
    save_many(qtbot, tab)
    assert {(TaggedFile(p).fields().disc, TaggedFile(p).fields().disc_total) for p in paths} == {(2, 2)}
    assert {TaggedFile(p).fields().track for p in paths} != {None}  # per-track numbers kept


def test_a_locked_file_in_the_batch(qtbot, panel, locks, album):
    paths = [album / n for n in MULTI_FILES]
    tab = load_many(qtbot, panel, paths)
    type_into(tab, "genre", "Blues")
    token = locks.acquire([paths[1]], "a tag save")  # a short save lock: the panel stays editable
    try:
        with qtbot.waitSignal(tab.saveFailed, timeout=10000):
            tab.save()
    finally:
        locks.release(token)
    assert "Saved 2 of 3 files. Not saved:" in tab.error_label.text() and "track2.flac" in tab.error_label.text()
    assert tab.dirty and tab.edits["genre"].text() == "Blues"
    assert [TaggedFile(p).fields().genre for p in paths] == ["Blues", "Jazz", "Blues"]
    save_many(qtbot, tab)  # retry: now every file has it
    assert {TaggedFile(p).fields().genre for p in paths} == {"Blues"} and not tab.dirty


def test_a_job_lock_makes_the_multi_edit_read_only(qtbot, panel, locks, album):
    tab = load_many(qtbot, panel, [album / n for n in MULTI_FILES])
    with locks.lock([album], "ReplayGain (album)"):
        qtbot.waitUntil(lambda: tab.edits["artist"].isReadOnly(), timeout=3000)
        assert not tab.clear_actions["artist"].isEnabled()
        assert "Read-only while ReplayGain (album) is running." in tab.note.text()
    qtbot.waitUntil(lambda: not tab.edits["artist"].isReadOnly(), timeout=3000)


def test_mixed_selection_edits_the_audio_files(qtbot, panel, album, fixture_copy):
    aac = fixture_copy("formats/tone.aac")
    items = [SelectedItem(album / "01 First.flac", False, True), SelectedItem(aac, False, True),
             SelectedItem(album / "lite", True, False), SelectedItem(album / "notes.txt", False, False)]
    tab = load_many(qtbot, panel, None, items)
    assert [s.path.name for s in tab.snapshots] == ["01 First.flac", "tone.aac"]
    assert "1 of these files have no tags" in tab.note.text()
    assert "2 other selected items aren't audio files" in tab.note.text()
    type_into(tab, "genre", "Folk")
    save_many(qtbot, tab)
    assert TaggedFile(album / "01 First.flac").fields().genre == "Folk"


def test_revert_in_multi(qtbot, panel, album):
    tab = load_many(qtbot, panel, [album / n for n in MULTI_FILES])
    type_into(tab, "artist", "Someone")
    tab.clear_actions["comment"].trigger()
    tab.revert()
    assert not tab.dirty and tab.edits["artist"].text() == "Fixture Artist"
    assert tab.edits["comment"].placeholderText() == "(multiple values)"


def test_multi_selection_in_the_window(qtbot, window, service, monkeypatch):
    window.open_folder(entry(service, "Artist", "Album"))
    ids = [entry(service, "Artist", "Album", n) for n in ("Track 2.flac", "Track 3.mp3")]
    window.file_view.select_ids(ids)
    tab = window.side_panel.metadata
    qtbot.waitUntil(lambda: tab.multi and len(tab.snapshots) == 2, timeout=10000)
    monkeypatch.setattr(tab, "ask_leave", lambda: "cancel")
    type_into(tab, "genre", "Ambient")
    window.file_view.select_ids([entry(service, "Artist", "Album", "track 1.flac")])
    assert window.file_view.selected_ids() == ids and tab.dirty  # Cancel kept the two files
    monkeypatch.setattr(tab, "ask_leave", lambda: "save")
    with qtbot.waitSignal(tab.writer.batchWritten, timeout=10000):
        window.file_view.select_ids([entry(service, "Artist", "Album", "track 1.flac")])
    qtbot.waitUntil(lambda: "Saved the tags of 2 files" in window.statusBar().currentMessage(), timeout=5000)


# -- the cover (SPEC §7, cover editing) --

def pictures(path) -> int:
    """How many pictures the file holds, whatever their type."""
    import mutagen

    audio = mutagen.File(path)
    if hasattr(audio, "pictures"):
        return len(audio.pictures)
    tags = audio.tags
    if hasattr(tags, "getall"):
        return len(tags.getall("APIC"))
    return 0


@pytest.fixture
def new_cover(tmp_path, fixtures_dir):
    path = tmp_path / "new cover.png"
    from PySide6.QtGui import QImage

    image = QImage(64, 64, QImage.Format.Format_RGB32)
    image.fill(0x3366CC)
    assert image.save(str(path), "PNG")
    return path


def pick(qtbot, tab, monkeypatch, path):
    monkeypatch.setattr(tab, "choose_cover_file", lambda: str(path))
    with qtbot.waitSignal(tab.io.imageRead, timeout=5000):
        tab.replace_cover_button.click()


@pytest.mark.parametrize("name", FORMATS)
def test_replace_cover_every_format(qtbot, panel, fixture_copy, new_cover, monkeypatch, name):
    path = fixture_copy(f"formats/{name}")
    tab = load(qtbot, panel, path)
    pick(qtbot, tab, monkeypatch, new_cover)
    assert tab.dirty and tab.cover_source.text() == "New cover (not saved yet)"
    assert tab.changes() == {"cover": new_cover.read_bytes()}
    save(qtbot, tab)
    assert TaggedFile(path).cover().data == new_cover.read_bytes()
    assert tab.cover_source.text() == "Embedded cover" and not tab.dirty


def test_replace_keeps_other_pictures(qtbot, panel, fixture_copy, new_cover, monkeypatch):
    path = fixture_copy("edge/edge.flac")  # a front and a back cover
    count = pictures(path)
    tab = load(qtbot, panel, path)
    pick(qtbot, tab, monkeypatch, new_cover)
    save(qtbot, tab)
    assert pictures(path) == count and TaggedFile(path).cover().data == new_cover.read_bytes()


def test_remove_cover(qtbot, panel, fixture_copy):
    path = fixture_copy("edge/edge.flac")
    count = pictures(path)
    tab = load(qtbot, panel, path)
    assert tab.remove_cover_button.isEnabled()
    tab.remove_cover_button.click()
    assert tab.cover.text() == "Cover removed" and tab.changes() == {"cover": None}
    assert not tab.remove_cover_button.isEnabled()
    save(qtbot, tab)
    assert pictures(path) == count - 1  # the back cover stays
    front = TaggedFile(path).cover()
    assert front is None or front.data != (fixture_copy("edge/front.jpg")).read_bytes()


def test_no_embedded_cover_means_nothing_to_remove(qtbot, panel, fixture_copy, fixtures_dir):
    path = fixture_copy("formats/tone.mp3")
    shutil.copy2(fixtures_dir / "cover.jpg", path.parent / "folder.jpg")
    tab = load(qtbot, panel, path)
    assert tab.cover_source.text() == "Cover from folder.jpg"  # shown, never written
    assert not tab.remove_cover_button.isEnabled()


@pytest.mark.filterwarnings("ignore::DeprecationWarning")  # QDragEnterEvent has only the QPoint constructor
def test_dropping_a_picture(qtbot, panel, fixture_copy, new_cover):
    from PySide6.QtCore import QMimeData, QPoint, QUrl
    from PySide6.QtGui import QDragEnterEvent, QDropEvent

    tab = load(qtbot, panel, fixture_copy("formats/tone.flac"))
    keep = []

    def event(cls, path):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        keep.append(mime)
        position = QPoint(5, 5) if cls is QDragEnterEvent else QPoint(5, 5).toPointF()
        return cls(position, Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                   Qt.KeyboardModifier.NoModifier)

    text_file = new_cover.with_suffix(".txt")
    text_file.write_text("x")
    refused = event(QDragEnterEvent, text_file)
    tab.cover.dragEnterEvent(refused)
    assert not refused.isAccepted()
    accepted = event(QDragEnterEvent, new_cover)
    tab.cover.dragEnterEvent(accepted)
    assert accepted.isAccepted()
    with qtbot.waitSignal(tab.io.imageRead, timeout=5000):
        tab.cover.dropEvent(event(QDropEvent, new_cover))
    assert tab.changes() == {"cover": new_cover.read_bytes()}


def test_a_broken_picture(qtbot, panel, fixture_copy, tmp_path, monkeypatch):
    fake = tmp_path / "fake.jpg"
    fake.write_text("not a picture")
    tab = load(qtbot, panel, fixture_copy("formats/tone.flac"))
    pick(qtbot, tab, monkeypatch, fake)
    assert "can't be used" in tab.error_label.text() and not tab.dirty


@pytest.mark.parametrize("accept", [False, True])
def test_large_cover_warning(qtbot, panel, fixture_copy, fixtures_dir, tmp_path, monkeypatch, accept):
    big = tmp_path / "big.jpg"
    big.write_bytes((fixtures_dir / "cover.jpg").read_bytes() + bytes(1024 * 1024 + 10))  # still decodes
    tab = load(qtbot, panel, fixture_copy("formats/tone.flac"))
    asked = []
    monkeypatch.setattr(tab, "confirm_large_cover", lambda size: asked.append(size) or accept)
    pick(qtbot, tab, monkeypatch, big)
    assert asked == [big.stat().st_size]
    assert tab.dirty is accept


def test_revert_drops_the_new_cover(qtbot, panel, fixture_copy, new_cover, monkeypatch):
    tab = load(qtbot, panel, fixture_copy("edge/edge.flac"))
    pick(qtbot, tab, monkeypatch, new_cover)
    tab.revert()
    assert not tab.dirty and tab.cover_source.text() == "Embedded cover"


def test_locked_cover(qtbot, panel, locks, fixture_copy):
    path = fixture_copy("edge/edge.flac")
    tab = load(qtbot, panel, path)
    with locks.lock([path], "Tidy album"):
        qtbot.waitUntil(lambda: not tab.replace_cover_button.isEnabled(), timeout=3000)
        assert not tab.remove_cover_button.isEnabled() and not tab.cover.isEnabled()
    qtbot.waitUntil(lambda: tab.replace_cover_button.isEnabled(), timeout=3000)


def test_multi_cover(qtbot, panel, album, new_cover, monkeypatch):
    paths = [album / n for n in MULTI_FILES]
    tab = load_many(qtbot, panel, paths)
    assert tab.cover.text() in ("No cover", "(multiple covers)")
    pick(qtbot, tab, monkeypatch, new_cover)
    assert tab.changes() == {"cover": new_cover.read_bytes()}
    save_many(qtbot, tab)
    qtbot.waitUntil(lambda: tab.cover_source.text() == "The same embedded cover on every file", timeout=10000)
    assert {TaggedFile(p).cover().data for p in paths} == {new_cover.read_bytes()}
    first = TaggedFile(paths[0])
    first.remove_cover()
    first.save()
    load_many(qtbot, panel, paths[1:])  # another selection, then back: a fresh read
    tab = load_many(qtbot, panel, paths)
    assert tab.cover.text() == "(multiple covers)"
    tab.remove_cover_button.click()
    save_many(qtbot, tab)
    assert all(TaggedFile(p).cover() is None for p in paths)


def test_index_follows_cover_changes(qtbot, window, service, new_cover, monkeypatch):
    window.open_folder(entry(service, "Artist", "Album"))
    tab = select_file(qtbot, window, "Track 2.flac")
    track = entry(service, "Artist", "Album", "Track 2.flac")
    assert service.conn.execute("SELECT has_cover FROM tracks WHERE entry_id = ?", (track,)).fetchone()[0] == 0
    pick(qtbot, tab, monkeypatch, new_cover)
    save(qtbot, tab)
    qtbot.waitUntil(lambda: service.conn.execute("SELECT has_cover FROM tracks WHERE entry_id = ?",
                                                 (track,)).fetchone()[0] == 1, timeout=10000)


def test_blank_text_in_a_file_isnt_an_edit(qtbot, panel, fixture_copy):
    tab = load(qtbot, panel, fixture_copy("edge/edge.flac"))  # its artist is blank padding
    assert tab.changes() == {} and not tab.dirty


# -- lyrics from a .lrc sidecar (SPEC §7) --

def test_import_from_lrc(qtbot, panel, fixture_copy):
    folder = fixture_copy("cleanup/lrc")
    lrc = folder / "no_lyrics.lrc"
    lrc_bytes = lrc.read_bytes()
    tab = load(qtbot, panel, folder / "no_lyrics.flac")
    assert tab.import_link.isVisibleTo(tab) and "Import from no_lyrics.lrc" in tab.import_link.text()
    tab.import_link.linkActivated.emit("import")
    expected = lrc_bytes.decode("utf-8").replace("\r\n", "\n").strip("\n")
    assert tab.lyrics.toPlainText() == expected and tab.dirty
    assert not tab.import_link.isVisibleTo(tab)  # the field isn't empty any more
    save(qtbot, tab)
    assert TaggedFile(folder / "no_lyrics.flac").fields().lyrics == expected
    assert lrc.read_bytes() == lrc_bytes  # the .lrc file itself is never touched


def test_no_link_when_the_tag_has_lyrics(qtbot, panel, fixture_copy):
    folder = fixture_copy("cleanup/lrc")
    tab = load(qtbot, panel, folder / "with_lyrics.flac")
    assert not tab.import_link.isVisibleTo(tab)
    tab.lyrics.setPlainText("")  # emptied by hand: the sidecar is offered again
    assert tab.import_link.isVisibleTo(tab)


def test_no_link_without_a_sidecar(qtbot, panel, fixture_copy):
    tab = load(qtbot, panel, fixture_copy("formats/tone.flac"))
    assert not tab.import_link.isVisibleTo(tab)


def test_cp1252_sidecar(qtbot, panel, fixture_copy):
    path = fixture_copy("formats/tone.mp3")
    path.with_suffix(".LRC").write_bytes("[00:01.00]Café crème".encode("cp1252"))
    tab = load(qtbot, panel, path)
    assert "Import from tone.LRC" in tab.import_link.text()
    tab.import_lrc()
    assert tab.lyrics.toPlainText() == "[00:01.00]Café crème"


def test_no_link_in_multi_or_when_locked(qtbot, panel, locks, fixture_copy):
    folder = fixture_copy("cleanup/lrc")
    tab = load_many(qtbot, panel, [folder / "no_lyrics.flac", folder / "with_lyrics.flac"])
    assert not tab.import_link.isVisibleTo(tab)
    tab = load(qtbot, panel, folder / "no_lyrics.flac")
    assert tab.import_link.isVisibleTo(tab)
    with locks.lock([folder], "Tidy album"):
        qtbot.waitUntil(lambda: not tab.import_link.isVisibleTo(tab), timeout=3000)
    qtbot.waitUntil(lambda: tab.import_link.isVisibleTo(tab), timeout=3000)


def test_revert_after_import(qtbot, panel, fixture_copy):
    folder = fixture_copy("cleanup/lrc")
    tab = load(qtbot, panel, folder / "no_lyrics.flac")
    tab.import_lrc()
    tab.revert()
    assert tab.lyrics.toPlainText() == "" and not tab.dirty and tab.import_link.isVisibleTo(tab)
