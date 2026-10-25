"""The ReplayGain tool in the window: Run (no preview), the job, Stop, locks, the report, and the index
(SPEC §10 B; Tidy is in test_tidy_tool.py). A fake scanner tags files as rsgain would; three tests run the
real rsgain."""

import shutil

import pytest
from rsgain_fake import FakeScanner

from music_desk.core.index.store import path_of
from music_desk.core.replaygain import ALBUM_GAIN, TRACK_GAIN
from music_desk.core.rsgain import ScanResult
from music_desk.core.tags import TaggedFile
from music_desk.ui.tools.tool_cards import REPLAYGAIN


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "replaygain" / "Fixture Artist", lib / "Fixture Artist")
    return lib


@pytest.fixture
def fake(window):
    scanner = FakeScanner()
    window.tools.rg_runner.scanner = scanner
    return scanner


def folder_id(service, path) -> int:
    conn = service.conn
    return next(i for (i,) in conn.execute("SELECT id FROM entries WHERE is_dir = 1") if path_of(conn, i) == path)


def open_card(window, service, folder, *, discography=False, tool=REPLAYGAIN, recaps=None):
    """The toolbar's button on the open folder. Outside the habitual case the recap comes first: OK (`recaps`
    collects what it showed)."""
    window.open_folder(folder_id(service, folder))
    window.file_view.select_ids([])
    card = window.tools.cards.replaygain_card
    card.scope_group.buttons()[1 if discography else 0].setChecked(True)
    window.tools.confirm_replaygain = lambda recap: (recaps.append(recap) if recaps is not None else None) or True
    window.tools.start(tool)


def run(qtbot, window, service, folder, **kwargs):
    """The toolbar's ReplayGain: the job starts (no preview, maybe a recap first), and its report follows."""
    open_card(window, service, folder, **kwargs)
    qtbot.waitUntil(lambda: window.rg_view.state in ("result", "error") and window.tools.rg_job is None,
                    timeout=20000)
    return window.rg_view.finished


def rg(path, key):
    return TaggedFile(path).get_replaygain(key)


def test_album_run(qtbot, window, service, library_dir, fake):
    album = library_dir / "Fixture Artist" / "Album A"
    owners = []
    fake.before = lambda folder: owners.append(service.locks.owner(folder))
    finished = run(qtbot, window, service, album)
    view = window.rg_view
    assert "ReplayGain · This album" in view.heading.text()
    assert owners == ["ReplayGain"] and service.locks.owner(album) is None  # locked during the run only
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
    token = service.locks.acquire([album / "01 Loud.flac"], "Rename")
    recaps = []
    open_card(window, service, album, recaps=recaps)
    assert window.statusBar().currentMessage() == f"ReplayGain: {album.name} is busy: Rename is running on it."
    assert window.center.currentWidget() is window.file_view and fake.calls == []  # still on the file list
    assert recaps == []  # a blocked run shows no recap
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
    """No track here: not the habitual case, and the recap finds nothing to measure, so nothing starts."""
    scans = library_dir / "Fixture Artist" / "Album B" / "Scans"
    recaps = []
    open_card(window, service, scans, recaps=recaps)
    message = f"ReplayGain: there is nothing to measure in {scans}."
    qtbot.waitUntil(lambda: window.statusBar().currentMessage() == message, timeout=10000)
    assert recaps == [] and fake.calls == [] and window.tools.rg_job is None


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
    qtbot.waitUntil(lambda: window.tools._recap_token is None, timeout=10000)  # the recap was OKed
    assert window.center.currentWidget() is window.file_view and fake.calls == []
    metadata.revert()


def test_the_real_rsgain_through_the_window(qtbot, window, service, library_dir):
    """The bundled rsgain, end to end (fixtures copied; about a second per album)."""
    album = library_dir / "Fixture Artist" / "Album B"
    finished = run(qtbot, window, service, album)
    assert [log.result() for log in finished.album_logs] == ["OK"], window.rg_view.text.toPlainText()
    assert rg(album / "01 Loud.flac", ALBUM_GAIN) is not None


def test_the_real_rsgain_while_the_library_is_scanned(qtbot, window, service, library_dir, monkeypatch):
    """Hugo's report (2026-10-15): ReplayGain never completed while the library was still being indexed."""
    import threading

    from music_desk.core.index.scanner import Scanner

    gate = threading.Event()
    real = Scanner._reconcile

    def reconcile(self, ctx, ref, path, batch):
        if threading.current_thread() is service.indexer._thread and not gate.is_set():
            gate.wait(0.05)
        return real(self, ctx, ref, path, batch)

    monkeypatch.setattr(Scanner, "_reconcile", reconcile)
    for n in range(200):
        (library_dir / "Aaa Many" / f"Folder {n}").mkdir(parents=True)
    service.indexer.scan_root(service.roots()[0].id)
    qtbot.waitUntil(lambda: service.indexer.busy, timeout=5000)
    album = library_dir / "Fixture Artist" / "Album B"
    open_card(window, service, album)
    qtbot.waitUntil(lambda: window.rg_view.state in ("result", "error"), timeout=30000)
    assert service.indexer.busy  # the scan was still running all along
    finished = window.rg_view.finished
    gate.set()
    assert [log.result() for log in finished.album_logs] == ["OK"], window.rg_view.text.toPlainText()


def test_the_real_rsgain_while_tags_are_read(qtbot, window, service, library_dir, fixtures_dir, monkeypatch):
    """The same, in the scan's second phase: hundreds of audio files having their tags read meanwhile."""
    import time

    from music_desk.core.index import scanner as scanner_module

    real = scanner_module.read_file

    def slow_read(path, size, mtime):
        time.sleep(0.02)
        return real(path, size, mtime)

    monkeypatch.setattr(scanner_module, "read_file", slow_read)
    many = library_dir / "Aaa Many"
    many.mkdir()
    for n in range(600):
        shutil.copy2(fixtures_dir / "formats" / "tone.flac", many / f"{n:03d}.flac")
    service.indexer.scan_root(service.roots()[0].id)
    qtbot.waitUntil(lambda: service.indexer.busy, timeout=5000)
    qtbot.wait(1500)  # well into reading tags
    album = library_dir / "Fixture Artist" / "Album B"
    started = time.monotonic()
    open_card(window, service, album)
    qtbot.waitUntil(lambda: window.rg_view.state in ("result", "error"), timeout=60000)
    elapsed = time.monotonic() - started
    assert service.indexer.busy, "the tag reading should still be running"
    finished = window.rg_view.finished
    assert [log.result() for log in finished.album_logs] == ["OK"], window.rg_view.text.toPlainText()
    assert elapsed < 20, elapsed


def test_indexing_pauses_while_replaygain_runs(qtbot, window, service, library_dir, fake):
    paused = []
    fake.before = lambda folder: paused.append(service.indexer.paused)
    run(qtbot, window, service, library_dir / "Fixture Artist" / "Album B")
    assert paused == [True]  # rsgain had the drive to itself
    assert not service.indexer.paused  # and indexing resumed after the job
    window.show_index_progress(type("P", (), {"root_id": -1, "phase": "tags", "tags_done": 1, "tags_total": 2})())
    assert "paused" not in window.index_label.text()


def test_elapsed_time_while_an_album_is_scanned(qtbot, window, service, library_dir, fake):
    """rsgain's output only arrives when it exits, so the page shows the album's elapsed time meanwhile."""
    import threading

    release = threading.Event()
    fake.before = lambda folder: release.wait(10)  # the "rsgain" run lasts until the test releases it
    open_card(window, service, library_dir / "Fixture Artist" / "Album B")
    view = window.rg_view
    qtbot.waitUntil(lambda: view.message.text().startswith("Album 1 of 1: Album B · 0:0"), timeout=5000)
    first = view.message.text()
    assert "rsgain reports each album when it is done" in first
    qtbot.waitUntil(lambda: view.message.text() != first, timeout=3000)  # it ticks
    release.set()
    qtbot.waitUntil(lambda: view.state == "result", timeout=10000)
    assert not view._clock.isActive() and view.message.text().startswith("Done")


# -- at once, or after the recap (SPEC §30, Hugo 2026-10-22) --

def no_recap(recap):
    raise AssertionError("the habitual case runs at once, without a recap")


def test_the_habitual_case_runs_at_once(qtbot, window, service, library_dir, fake):
    album = library_dir / "Fixture Artist" / "Album C"
    shutil.copytree(library_dir / "Fixture Artist" / "Album A", album, ignore=shutil.ignore_patterns("CD2", "bonus",
                                                                                                    "lite"))
    service.indexer.scan_path(album, recursive=True)
    qtbot.waitUntil(lambda: any(True for _ in service.conn.execute(
        "SELECT 1 FROM entries WHERE name = 'Album C'")), timeout=10000)
    window.open_folder(folder_id(service, album))
    qtbot.waitUntil(lambda: len(window.file_view.model.rows) == 3, timeout=10000)
    window.file_view.select_ids([])
    window.tools.confirm_replaygain = no_recap
    window.tools.start(REPLAYGAIN)
    assert window.tools.rg_job is not None  # started on the click
    qtbot.waitUntil(lambda: window.rg_view.state in ("result", "error"), timeout=20000)


@pytest.mark.parametrize("case", ["subfolders", "selection", "discography"])
def test_other_cases_show_the_recap_first(qtbot, window, service, library_dir, fake, case):
    album = library_dir / "Fixture Artist" / ("Album A" if case != "discography" else "")
    window.open_folder(folder_id(service, album))
    window.file_view.select_ids([])
    if case == "selection":
        ids = {r.name: r.id for r in window.file_view.model.rows}
        window.file_view.select_ids([ids["01 Tone.flac"]])
    window.tools.cards.replaygain_card.scope_group.buttons()[1 if case == "discography" else 0].setChecked(True)
    recaps = []
    window.tools.confirm_replaygain = lambda recap: recaps.append(recap) or False  # Cancel
    window.tools.start(REPLAYGAIN)
    assert window.tools.rg_job is None  # not yet: the recap is read on the worker
    qtbot.waitUntil(lambda: bool(recaps), timeout=10000)
    [recap] = recaps
    assert recap.folder == album and recap.discography is (case == "discography")
    assert window.tools.rg_job is None and fake.calls == []  # Cancel starts nothing
    if case != "discography":
        rows = {(each.folder.name, name): what for each in recap.folders for name, what in each.rows}
        assert rows[("Album A", "01 Tone.flac")] == "measured: track and album gain"  # the whole folder
        assert rows[("lite", "02 Spike.flac")] == "album and track values copied"
        assert rows[("CD2", "01 Disc Two.flac")] == "album values copied"
        window.tools.confirm_replaygain = lambda recap: True  # OK runs
        window.tools.start(REPLAYGAIN)
        qtbot.waitUntil(lambda: window.rg_view.state in ("result", "error") and window.tools.rg_job is None,
                        timeout=20000)
        assert [log.result() for log in window.rg_view.finished.album_logs] == ["OK"]
    window.tools.cards.replaygain_card.scope_group.buttons()[0].setChecked(True)


def test_the_recap_window_lists_every_file(qtbot, library_dir):
    from music_desk.core import replaygain
    from music_desk.ui.tools.rg_recap import RecapDialog

    album = library_dir / "Fixture Artist" / "Album A"
    dialog = RecapDialog(replaygain.recap(album, replaygain.ReplayGainOptions(), False))
    qtbot.addWidget(dialog)
    rows = dialog.rows()
    assert (str(album), "01 Tone.flac", "measured: track and album gain") in rows
    assert (str(album / "bonus"), "01 Tone.flac", "album values copied") in rows
    assert (str(album), "replaygain.log", "written (replaces the last one)") in rows
    assert [b.text() for b in dialog.buttons.buttons()] == ["OK", "Cancel"]
