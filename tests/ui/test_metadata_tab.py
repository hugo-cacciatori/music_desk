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
    assert tab.dirty and panel.tabText(0) == "Metadata •"
    assert tab.changes() == {"title": "New Title", "lyrics": "[00:01.00]First line\nSecond line"}
    save(qtbot, tab)

    after = TaggedFile(path).fields()
    assert after.title == "New Title" and after.lyrics == "[00:01.00]First line\nSecond line"
    for field in FIELD_NAMES:
        if field not in ("title", "lyrics"):
            assert getattr(after, field) == getattr(before_fields, field), field
    assert not tab.dirty and panel.tabText(0) == "Metadata"
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
    panel.show_selection([SelectedItem(tmp_path / "a.flac", False, True), SelectedItem(tmp_path / "b.flac", False,
                                                                                         True)])
    assert "2 items selected" in panel.metadata.message.text()
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
