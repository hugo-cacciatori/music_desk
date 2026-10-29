"""Trim (SPEC §23, §35): the Metadata tab's Trim… button (shown, greyed, absent) opening the Trim window, and that
window's page: fields and waveform kept in step, the validation that disables Trim, the remembered subfolder,
Preview in player through an injected opener (never a real player), and the trim with the real bundled flac on
copied fixtures: the lock, Stop and the report. A trim never touches the original."""

import shutil

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from music_desk.context_menu.tool_host import TrimWindow
from music_desk.core.flac import Cancelled
from music_desk.core.index.store import path_of
from music_desk.core.tags import TaggedFile
from music_desk.services import trim_job
from music_desk.settings import Settings
from music_desk.ui import theme
from music_desk.ui.side_panel.metadata_tab import TRIM_TIP
from music_desk.ui.tools.tool_cards import ToolCards

HIDDEN = "Fixture Artist - Split Album.flac"  # 12 s: a 3 s hidden track, then the songs


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    album = lib / "Album"
    shutil.copytree(fixtures_dir / "split" / "hidden_track", album)
    for name in ("tone.wav", "tone.aiff", "tone.mp3"):
        shutil.copy2(fixtures_dir / "formats" / name, album / name)
    return lib


@pytest.fixture
def album(library_dir):
    return library_dir / "Album"


@pytest.fixture
def host(qtbot, service):
    """A Trim window, on the library's locks (as app.Starter makes it)."""
    cards = ToolCards(Settings())
    window = TrimWindow(cards, service.locks)
    yield window
    window.shutdown()
    window.hide()
    window.deleteLater()
    cards.dispose()


def entry_id(service, path) -> int:
    conn = service.conn
    return next(i for (i,) in conn.execute("SELECT id FROM entries") if path_of(conn, i) == path)


def select(qtbot, window, service, *paths):
    """Select files in the file list; with one, wait for the Metadata tab to show it."""
    window.open_folder(entry_id(service, paths[0].parent))
    window.file_view.select_ids([entry_id(service, p) for p in paths])
    metadata = window.side_panel.metadata
    if len(paths) == 1:
        qtbot.waitUntil(lambda: metadata.snapshot is not None and metadata.snapshot.path == paths[0], timeout=10000)
    else:
        qtbot.waitUntil(lambda: metadata.multi and len(metadata.snapshots) == len(paths), timeout=10000)
    return metadata


def ready(qtbot, host):
    """The page shows the track, and its plan follows the latest edit."""
    trim = host.controller
    qtbot.waitUntil(lambda: trim.page.state == "editing" and trim.plan is not None and not trim._planning,
                    timeout=10000)


def open_page(qtbot, host, path):
    host.start([str(path)])
    ready(qtbot, host)
    return host.controller.page


def type_time(qtbot, host, edit, text):
    edit.setText(text)
    edit.textEdited.emit(text)
    ready(qtbot, host)


def run(qtbot, host):
    host.controller.page.trim_button.click()
    qtbot.waitUntil(lambda: host.controller.page.state in ("result", "error"), timeout=30000)
    return host.controller.page.report.toPlainText()


def mouse(widget, kind, x):
    held = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else Qt.MouseButton.LeftButton
    point = QPointF(x, widget.height() / 2)
    event = QMouseEvent(kind, point, widget.mapToGlobal(point), Qt.MouseButton.LeftButton, held,
                        Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)


# -- the button --

@pytest.mark.parametrize("name", [HIDDEN, "tone.wav", "tone.aiff"])
def test_the_button_on_one_flac_wav_or_aiff(qtbot, open_app, service, album, name):
    """Trim… opens the Trim window, as the right-click "Trim this track…" (SPEC §35)."""
    main = open_app.window
    metadata = select(qtbot, main, service, album / name)
    assert metadata.trim_button.isVisible() and metadata.trim_button.isEnabled()
    metadata.trim_button.click()
    window = open_app.entry_windows[-1]
    assert isinstance(window, TrimWindow) and window.parent() is None
    assert main.center.currentWidget() is main.file_view
    ready(qtbot, window)
    assert window.controller.path == album / name


def test_the_button_is_greyed_on_an_mp3_and_absent_on_several_files(qtbot, window, service, album):
    metadata = select(qtbot, window, service, album / "tone.mp3")
    assert metadata.trim_button.isVisible() and not metadata.trim_button.isEnabled()
    assert metadata.trim_button.toolTip() == "MP3 isn't supported: trimming reads FLAC, WAV and AIFF."
    select(qtbot, window, service, album / HIDDEN, album / "tone.wav")
    assert not metadata.trim_button.isVisible()


def test_the_button_is_greyed_while_flac_is_unavailable_or_the_file_locked(qtbot, window, service, album):
    metadata = select(qtbot, window, service, album / HIDDEN)
    window.show_flac_version(None)
    assert not metadata.trim_button.isEnabled() and "flac couldn't run" in metadata.trim_button.toolTip()
    window.show_flac_version("flac 1.5.0")
    assert metadata.trim_button.isEnabled()
    assert metadata.trim_button.toolTip() == TRIM_TIP  # the reason gone, the hover text is back (SPEC §24)
    lock = window.library.locks.acquire([album], "ReplayGain")
    try:
        qtbot.waitUntil(lambda: not metadata.trim_button.isEnabled(), timeout=5000)
        assert metadata.trim_button.toolTip() == "Unavailable while ReplayGain is running."
    finally:
        window.library.locks.release(lock)
    qtbot.waitUntil(lambda: metadata.trim_button.isEnabled(), timeout=5000)


# -- the page --

def test_the_page_shows_the_track_and_its_waveform(qtbot, host, album):
    page = open_page(qtbot, host, album / HIDDEN)
    assert "FLAC 16-bit/44.1 kHz · 0:12.000" in page.format_label.text()
    assert (page.start_edit.text(), page.end_edit.text()) == ("0:00.000", "0:12.000")
    assert page.subfolder.text() == "Subfolder: lite (File > Options…, Trim)"  # set there only (SPEC §12)
    assert not page.trim_button.isEnabled() and page.trim_note.text() == "Nothing to trim: move a handle."
    assert page.preview_button.isEnabled()
    assert page.copy_path.text() == f"The copy: lite\\{HIDDEN}"
    qtbot.waitUntil(lambda: page.waveform.peaks is not None, timeout=20000)
    assert len(page.waveform.peaks) == 4096 and max(page.waveform.peaks.highs) > 0.2
    host.resize(1200, 1000)  # a tall window: the waveform keeps its 70 px, the spare height goes below
    host.show()
    qtbot.waitUntil(lambda: page.isVisible())
    assert page.waveform.height() == 70


def test_an_unreadable_track_says_why(qtbot, host, album):
    (album / "broken.flac").write_bytes(b"not a flac")
    host.start([str(album / "broken.flac")])
    qtbot.waitUntil(lambda: host.controller.page.state == "error", timeout=10000)
    assert "can't be read" in host.controller.page.message.text()


def test_handles_and_fields_stay_in_step(qtbot, host, album):
    page = open_page(qtbot, host, album / HIDDEN)
    view = page.waveform
    view.resize(1201, view.height())  # 10 ms a pixel over 12 s
    mouse(view, QEvent.Type.MouseButtonPress, 2)  # the start handle
    mouse(view, QEvent.Type.MouseMove, 300)
    mouse(view, QEvent.Type.MouseButtonRelease, 300)
    assert page.start_edit.text() == "0:03.000" and view.start_ms == 3000
    mouse(view, QEvent.Type.MouseButtonPress, 1199)  # the end handle can't pass the start
    mouse(view, QEvent.Type.MouseMove, 100)
    mouse(view, QEvent.Type.MouseButtonRelease, 100)
    assert page.end_edit.text() == "0:03.001"
    type_time(qtbot, host, page.end_edit, "0:09.500")
    assert view.end_ms == 9500
    assert page.summary.text() == "Keeps 0:06.500. Removes 0:03.000 at the start and 0:02.500 at the end."
    assert page.trim_button.isEnabled()


def test_keys_nudge_the_times(qtbot, host, album):
    page = open_page(qtbot, host, album / HIDDEN)
    page.start_edit.setFocus()
    qtbot.keyClick(page.start_edit, Qt.Key.Key_PageUp)
    qtbot.keyClick(page.start_edit, Qt.Key.Key_Up)
    qtbot.keyClick(page.start_edit, Qt.Key.Key_Down)
    qtbot.keyClick(page.start_edit, Qt.Key.Key_Up)
    assert page.start_edit.text() == "0:01.010" and page.waveform.start_ms == 1010
    qtbot.keyClick(page.end_edit, Qt.Key.Key_PageUp)  # not past the length
    assert page.end_edit.text() == "0:12.000"
    ready(qtbot, host)
    assert page.trim_button.isEnabled()


# One case per field the page marks; tests/core/test_trim.py checks every refusal and its message (SPEC §18).
@pytest.mark.parametrize(("field", "text", "reason"), [
    ("start", "0:0x", "Type the start as m:ss.mmm, for example 0:02.350."),
    ("end", "0:13.000", "The end is past the track's length (0:12.000)."),
    ("subfolder", "a:b", 'A folder name can\'t contain any of \\ / : * ? " < > |.'),
])
def test_bad_values_are_marked_and_disable_trim(qtbot, host, album, field, text, reason):
    page = open_page(qtbot, host, album / HIDDEN)
    type_time(qtbot, host, page.start_edit, "0:01.000")
    if field == "subfolder":  # File > Options…, Trim: it applies to the open track at once
        card = host.tools.cards.trim_card
        type_time(qtbot, host, card.subfolder, text)
        assert card.problems.text() == f"• {reason}"
        edit = page.subfolder
    else:
        edit = {"start": page.start_edit, "end": page.end_edit}[field]
        type_time(qtbot, host, edit, text)
    assert not page.trim_button.isEnabled() and reason in page.trim_note.text()
    assert theme.is_invalid(edit) and edit.toolTip() == reason
    assert page.preview_button.isEnabled() == (field == "subfolder")  # only valid times matter to the preview
    host.tools.cards.trim_card.reset()


def test_a_file_named_like_the_subfolder_is_refused(qtbot, host, album):
    (album / "lite").write_text("a file, not a folder")
    page = open_page(qtbot, host, album / HIDDEN)
    assert page.trim_note.text() == "Nothing to trim: move a handle. lite is a file in this folder, not a folder."


# -- Preview in player --

def test_preview_opens_a_temporary_sheet(qtbot, host, album, tmp_path):
    launched = []
    host.launch = launched.append
    host.playlist_dir = tmp_path / "playlists"
    page = open_page(qtbot, host, album / HIDDEN)
    type_time(qtbot, host, page.start_edit, "0:03.000")
    type_time(qtbot, host, page.end_edit, "0:09.000")
    page.preview_button.click()
    qtbot.waitUntil(lambda: bool(launched), timeout=5000)
    sheet = launched[0]
    assert sheet.parent == tmp_path / "playlists" and sheet.name.startswith("preview-") and sheet.suffix == ".cue"
    text = sheet.read_bytes().decode("utf-8-sig")
    assert f'FILE "{album / HIDDEN}" WAVE' in text
    # The fixture has an album tag and no title: the kept part is named by the file name.
    assert [line.strip() for line in text.splitlines() if "TITLE" in line or "INDEX" in line] == [
        'TITLE "Source Album"', 'TITLE "Cut from the start"', "INDEX 01 00:00:00",
        'TITLE "Fixture Artist - Split Album (kept)"', "INDEX 01 00:03:00", 'TITLE "Cut from the end"',
        "INDEX 01 00:09:00"]
    assert sorted(p.name for p in album.iterdir()) == sorted([HIDDEN, "Split Album.cue", "tone.wav", "tone.aiff",
                                                              "tone.mp3"])  # nothing written beside the track


def test_preview_without_a_player_says_how_to_set_one(qtbot, host, album, tmp_path):
    def no_player(_path):
        raise OSError(1155, "No application is associated with the specified file")

    host.launch = no_player
    host.playlist_dir = tmp_path / "playlists"
    page = open_page(qtbot, host, album / HIDDEN)
    page.preview_button.click()
    qtbot.waitUntil(lambda: "No app is set to open .cue sheets" in host.toast.label.text(), timeout=5000)


# -- the job, with the real flac --

def test_a_trim_writes_a_proved_copy_and_leaves_the_original(qtbot, host, service, album):
    original = (album / HIDDEN).read_bytes()
    host.tools.cards.trim_card.set_value("songs")  # File > Options…, Trim
    page = open_page(qtbot, host, album / HIDDEN)
    type_time(qtbot, host, page.start_edit, "0:03.000")
    report = run(qtbot, host)
    assert report.startswith(f"Done: songs\\{HIDDEN}, 0:09.000, identical to its part of the original. The "
                             "original is unchanged.")
    copy = album / "songs" / HIDDEN
    assert TaggedFile(copy).stream_info().duration == pytest.approx(9.0, abs=0.001)
    assert TaggedFile(copy).fields().album == "Source Album"
    assert (album / HIDDEN).read_bytes() == original
    host.controller.open(album / HIDDEN)  # the same track again: the subfolder is remembered
    ready(qtbot, host)
    assert page.subfolder.text() == "Subfolder: songs (File > Options…, Trim)"
    assert page.copy_path.text() == (f"The copy: songs\\Fixture Artist - Split Album (2).flac\n{HIDDEN} is already "
                                     "in songs, so the copy is named Fixture Artist - Split Album (2).flac.")


def test_a_trim_holds_a_lock_on_the_folder(qtbot, host, album, monkeypatch):
    seen = []

    class Watching(trim_job.FlacRunner):
        def encode(self, source, start, end, output, cancel=None):
            seen.append(host_locks.owner(album))
            super().encode(source, start, end, output, cancel)

    monkeypatch.setattr(trim_job, "FlacRunner", Watching)
    host_locks = host.controller.runner.locks
    page = open_page(qtbot, host, album / HIDDEN)
    type_time(qtbot, host, page.end_edit, "0:03.000")
    run(qtbot, host)
    assert seen == ["Trim"] and host_locks.owner(album) is None


def test_stop_leaves_nothing(qtbot, host, album, monkeypatch):
    class Stopped(trim_job.FlacRunner):
        def encode(self, source, start, end, output, cancel=None):
            output.write_bytes(b"half")
            raise Cancelled()

    monkeypatch.setattr(trim_job, "FlacRunner", Stopped)
    before = sorted(p.name for p in album.iterdir())
    page = open_page(qtbot, host, album / HIDDEN)
    type_time(qtbot, host, page.end_edit, "0:03.000")
    report = run(qtbot, host)
    assert report.startswith("Stopped: nothing left half-done.")
    assert sorted(p.name for p in album.iterdir()) == before  # no .partial, no subfolder
