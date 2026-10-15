"""The ReplayGain tool and Tidy in the window: Run (no preview), the job, Stop, locks, the report, and the index
(SPEC §10 B, C). A fake scanner tags files as rsgain would; the last test runs the real rsgain (slow)."""

import shutil

import pytest
from rsgain_fake import FakeScanner

from music_desk.core.index.store import path_of
from music_desk.core.replaygain import ALBUM_GAIN, TRACK_GAIN
from music_desk.core.rsgain import ScanResult
from music_desk.core.tags import TaggedFile
from music_desk.ui.tools.tools_page import REPLAYGAIN, TIDY


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "replaygain" / "Fixture Artist", lib / "Fixture Artist")
    return lib


@pytest.fixture
def fake(window):
    scanner = FakeScanner()
    window.rg_runner.scanner = scanner
    return scanner


def folder_id(service, path) -> int:
    conn = service.conn
    return next(i for (i,) in conn.execute("SELECT id FROM entries WHERE is_dir = 1") if path_of(conn, i) == path)


def open_card(window, service, folder, *, discography=False, tool=REPLAYGAIN):
    window.open_folder(folder_id(service, folder))
    window.open_tools([])
    card = window.tools_page.replaygain_card if tool == REPLAYGAIN else window.tools_page.tidy_card
    card.scope_group.buttons()[1 if discography else 0].setChecked(True)
    window.tools_page.request(tool)


def run(qtbot, window, service, folder, **kwargs):
    """The card's Run: the job starts at once (no preview), and its report follows."""
    open_card(window, service, folder, **kwargs)
    assert window.rg_view.state in ("running", "result", "error")
    qtbot.waitUntil(lambda: window.rg_view.state in ("result", "error"), timeout=20000)
    return window.rg_view.finished


def rg(path, key):
    return TaggedFile(path).get_replaygain(key)


def test_album_run(qtbot, window, service, library_dir, fake):
    album = library_dir / "Fixture Artist" / "Album A"
    assert window.tools_page.replaygain_card.run_button.text() == "Run ReplayGain"
    owners = []
    fake.before = lambda folder: owners.append(service.locks.owner(folder))
    finished = run(qtbot, window, service, album)
    view = window.rg_view
    assert "ReplayGain · This album" in view.heading.text()
    assert owners == ["the ReplayGain tool"] and service.locks.owner(album) is None  # locked during the run only
    assert [log.result() for log in finished.album_logs] == ["OK"]
    assert view.message.text() == "Done: 1 album OK."
    assert "Result: OK." in view.text.toPlainText()  # the album's report, as written to its log
    assert (album / "replaygain.log").read_text(encoding="utf-8").count("Result: OK.") == 1
    assert rg(album / "01 Tone.flac", ALBUM_GAIN) is not None
    assert rg(album / "lite" / "01 Tone.flac", TRACK_GAIN) is not None  # copied into the lite subfolder

    # the index re-reads the changed files: the RG column fills in
    tone = next(i for (i,) in service.conn.execute("SELECT id FROM entries WHERE name = '01 Tone.flac'")
                if path_of(service.conn, i) == album / "01 Tone.flac")
    qtbot.waitUntil(lambda: service.conn.execute("SELECT rg_album_gain FROM tracks WHERE entry_id = ?",
                                                 (tone,)).fetchone()[0] is not None, timeout=10000)
    # and the open folder's list shows the new replaygain.log, with nothing to press (Hugo's report, 2026-10-15)
    view.done_button.click()
    qtbot.waitUntil(lambda: "replaygain.log" in window.file_view.visible_names(), timeout=10000)


def test_discography(qtbot, window, service, library_dir, fake):
    artist = library_dir / "Fixture Artist"
    finished = run(qtbot, window, service, artist, discography=True)
    view = window.rg_view
    assert "ReplayGain · Discography" in view.heading.text()
    assert [row[0] for row in view.summary_rows()] == ["Album A", "Album B"]
    assert all(row[1] == "OK" and row[3] != "not leveled" for row in view.summary_rows())
    assert view.message.text().startswith("Done: 2 albums OK. Every album was leveled to ")
    assert finished.log_path == artist / "replaygain.log" and finished.log_path.exists()


def test_stop_reports_a_cancelled_album(qtbot, window, service, library_dir, fake):
    fake.result = ScanResult(1, "", "", 0.1, cancelled=True)
    finished = run(qtbot, window, service, library_dir / "Fixture Artist" / "Album B")
    assert finished.cancelled
    assert window.rg_view.message.text().startswith("Stopped.")
    assert "Scan: CANCELLED" in window.rg_view.text.toPlainText()


def test_a_job_lock_refuses_the_run(qtbot, window, service, library_dir, fake):
    album = library_dir / "Fixture Artist" / "Album B"
    token = service.locks.acquire([album / "01 Loud.flac"], "the Rename tool")
    open_card(window, service, album)
    assert window.toast.label.text().endswith("is in use by the Rename tool. Try again when it has finished.")
    assert window.center.currentWidget() is window.tools_page and fake.calls == []  # still on the Tools page
    service.locks.release(token)


def test_report_buttons_open_the_log_and_the_folder(qtbot, window, service, library_dir, fake):
    launched, shown = [], []
    window.launch = launched.append
    window.show_in_explorer = lambda path, is_dir: shown.append(path)
    album = library_dir / "Fixture Artist" / "Album B"
    run(qtbot, window, service, album)
    window.rg_view.open_log_button.click()
    window.rg_view.reveal_button.click()
    qtbot.waitUntil(lambda: bool(launched and shown))
    assert launched == [str(album / "replaygain.log")] and shown == [str(album)]
    window.rg_view.done_button.click()
    assert not window.showing_tools


def test_nothing_to_do(qtbot, window, service, library_dir, fake):
    finished = run(qtbot, window, service, library_dir / "Fixture Artist" / "Album B" / "Scans")
    assert finished.nothing and fake.calls == []
    assert window.rg_view.message.text() == "Nothing was done: no audio file rsgain can scan here."


def test_tidy_renames_then_runs_replaygain(qtbot, window, service, library_dir, fake):
    album = library_dir / "Fixture Artist" / "Album B"
    (album / "01 Loud.flac").rename(album / "loud.flac")
    open_card(window, service, album, tool=TIDY)
    rename_view = window.rename_preview
    qtbot.waitUntil(lambda: rename_view.state == "preview", timeout=10000)
    assert "Tidy album, step 1 of 2" in rename_view.heading.text()
    assert rename_view.apply_button.text() == "Apply, then run ReplayGain"
    rename_view.apply_button.click()
    qtbot.waitUntil(lambda: window.rg_view.state == "result", timeout=20000)
    assert "Tidy album, step 2 of 2: ReplayGain" in window.rg_view.heading.text()
    assert (album / "01 Loud.flac").exists() and fake.calls[0][0].count("01 Loud.flac") == 1  # the final names
    assert rg(album / "01 Loud.flac", ALBUM_GAIN) is not None


def test_tidy_with_nothing_to_rename_still_runs_replaygain(qtbot, window, service, library_dir, fake):
    open_card(window, service, library_dir / "Fixture Artist" / "Album B", tool=TIDY)
    qtbot.waitUntil(lambda: window.rename_preview.state == "preview", timeout=10000)
    assert window.rename_preview.message.text() == "Nothing to rename. Apply runs ReplayGain."
    window.rename_preview.apply_button.click()
    qtbot.waitUntil(lambda: window.rg_view.state == "result", timeout=20000)
    assert len(fake.calls) == 1


def test_unsaved_edits_are_asked_before_the_run(qtbot, window, service, library_dir, fake):
    album = library_dir / "Fixture Artist" / "Album B"
    window.open_folder(folder_id(service, album))
    ids = {r.name: r.id for r in window.file_view.model.rows}
    window.file_view.select_ids([ids["01 Loud.flac"]])
    metadata = window.side_panel.metadata
    qtbot.waitUntil(lambda: metadata.snapshot is not None, timeout=5000)
    metadata.edits["comment"].setText("mine")
    metadata.edits["comment"].textEdited.emit("mine")
    metadata.ask_leave = lambda: "cancel"
    window.file_view.select_ids([])
    open_card(window, service, album)
    assert window.center.currentWidget() is window.tools_page and fake.calls == []
    metadata.revert()


@pytest.mark.slow
def test_the_real_rsgain_through_the_window(qtbot, window, service, library_dir):
    """The bundled rsgain, end to end (fixtures copied; about a second per album)."""
    album = library_dir / "Fixture Artist" / "Album B"
    finished = run(qtbot, window, service, album)
    assert [log.result() for log in finished.album_logs] == ["OK"], window.rg_view.text.toPlainText()
    assert rg(album / "01 Loud.flac", ALBUM_GAIN) is not None
