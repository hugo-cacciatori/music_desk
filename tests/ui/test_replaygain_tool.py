"""The toolbar's ReplayGain (SPEC §10 B, §30, §35): its list of the right-click entries for the selection or the
open folder; each opens the ReplayGain window, which runs at once (no preview, no recap), with its Stop, locks and
report, and the main window's index follows. A fake scanner tags files as rsgain would; three tests run the real
rsgain."""

import shutil
from functools import partial

import pytest
from PySide6.QtWidgets import QMenu
from rsgain_fake import FakeScanner

from music_desk.context_menu import replaygain_entry
from music_desk.core import fsops
from music_desk.core.index.store import path_of
from music_desk.core.replaygain import ALBUM_GAIN, TRACK_GAIN
from music_desk.core.rsgain import ScanResult
from music_desk.core.tags import TaggedFile
from music_desk.services.replaygain_job import ReplayGainRunner


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "replaygain" / "Fixture Artist", lib / "Fixture Artist")
    return lib


@pytest.fixture
def fake(monkeypatch):
    scanner = FakeScanner()
    monkeypatch.setattr(replaygain_entry, "ReplayGainRunner", partial(ReplayGainRunner, scanner=scanner))
    return scanner


def folder_id(service, path) -> int:
    conn = service.conn
    return next(i for (i,) in conn.execute("SELECT id FROM entries WHERE is_dir = 1") if path_of(conn, i) == path)


def entries(open_app, service, folder, names=()) -> list:
    """The ReplayGain button's list on `folder` (open), or on the named files selected in it."""
    main = open_app.window
    main.open_folder(folder_id(service, folder))
    ids = {r.name: r.id for r in main.file_view.model.rows}
    main.file_view.select_ids([ids[name] for name in names])
    menu = QMenu(main)
    main.tools.fill_replaygain_menu(menu)
    return menu.actions()


def start(qtbot, open_app, service, folder, entry="ReplayGain as an album", names=()):
    """Choose `entry` in the button's list: its window opens and runs."""
    action = next(a for a in entries(open_app, service, folder, names) if a.text().startswith(entry))
    before = list(open_app.entry_windows)
    action.trigger()
    [window] = [w for w in open_app.entry_windows if w not in before]
    qtbot.addWidget(window)
    return window


def run(qtbot, open_app, service, folder, **kwargs):
    window = start(qtbot, open_app, service, folder, **kwargs)
    qtbot.waitUntil(lambda: window.view.state in ("result", "error"), timeout=20000)
    return window


def rg(path, key):
    return TaggedFile(path).get_replaygain(key)


def test_the_button_lists_the_right_click_entries(qtbot, open_app, service, library_dir, fake):
    """Hugo, 2026-10-25: the button opens the entries the right-click would show, with the target."""
    artist = library_dir / "Fixture Artist"
    texts = [a.text() for a in entries(open_app, service, artist)]
    assert texts == ["ReplayGain as an album (−15 LUFS)", "ReplayGain as a discography (−15 LUFS)"]
    texts = [a.text() for a in entries(open_app, service, artist / "Album A", ["01 Tone.flac", "03 Spike.mp3"])]
    assert texts == ["ReplayGain: these 2 tracks as an album (−15 LUFS)",
                     "ReplayGain: each of these 2 tracks on its own (−15 LUFS)"]
    [none] = entries(open_app, service, artist / "Album A", ["lite", "01 Tone.flac"])  # folders and files together
    assert not none.isEnabled() and none.text() == (
        "ReplayGain: select folders, or tracks it measures, or nothing for the open folder.")
    button = open_app.window.tools_bar.widgetForAction(open_app.window.tool_actions["replaygain"])
    assert button.popupMode().name == "InstantPopup" and not open_app.window.tool_actions["replaygain"].isCheckable()


def test_album_run(qtbot, open_app, service, library_dir, fake):
    album = library_dir / "Fixture Artist" / "Album A"
    owners = []
    fake.before = lambda folder: owners.append(service.locks.owner(folder))
    main = open_app.window
    window = run(qtbot, open_app, service, album)
    view, finished = window.view, window.view.finished
    assert window.parent() is None and main.center.currentWidget() is main.file_view  # its own window (SPEC §35)
    assert "ReplayGain · This album" in view.heading.text()
    assert owners == ["ReplayGain"] and service.locks.owner(album) is None  # locked during the run only
    assert [log.result() for log in finished.album_logs] == ["OK"]
    assert view.message.text() == "Done: 1 album OK."
    assert "Result: OK." in view.text.toPlainText()  # the album's report, as written to its log
    assert (album / "replaygain.log").read_text(encoding="utf-8").count("Result: OK.") == 1
    assert rg(album / "01 Tone.flac", ALBUM_GAIN) is not None
    assert rg(album / "lite" / "01 Tone.flac", TRACK_GAIN) is not None  # copied into the lite subfolder

    # the main window's index re-reads the changed files: the RG column fills in
    tone = next(i for (i,) in service.conn.execute("SELECT id FROM entries WHERE name = '01 Tone.flac'")
                if path_of(service.conn, i) == album / "01 Tone.flac")
    qtbot.waitUntil(lambda: service.conn.execute("SELECT rg_album_gain FROM tracks WHERE entry_id = ?",
                                                 (tone,)).fetchone()[0] is not None, timeout=10000)
    # and the open folder's list shows the new replaygain.log, with nothing to press (Hugo's report, 2026-10-15)
    qtbot.waitUntil(lambda: "replaygain.log" in main.file_view.visible_names(), timeout=10000)
    view.done_button.click()
    assert open_app.entry_windows == []


def test_discography(qtbot, open_app, service, library_dir, fake):
    artist = library_dir / "Fixture Artist"
    window = run(qtbot, open_app, service, artist, entry="ReplayGain as a discography")
    view, finished = window.view, window.view.finished
    assert "ReplayGain · Discography" in view.heading.text()
    assert [row[0] for row in view.summary_rows()] == ["Album A", "Album B"]
    assert all(row[1] == "OK" and row[3] != "not leveled" for row in view.summary_rows())
    assert view.message.text().startswith("Done: 2 albums OK. Every album was leveled to ")
    assert finished.log_path == artist / "replaygain.log" and finished.log_path.exists()


def test_stop_reports_a_cancelled_album(qtbot, open_app, service, library_dir, fake):
    fake.result = ScanResult(1, "", "", 0.1, cancelled=True)
    window = run(qtbot, open_app, service, library_dir / "Fixture Artist" / "Album B")
    assert window.view.finished.cancelled
    assert window.view.message.text().startswith("Stopped.")
    assert "Scan: CANCELLED" in window.view.text.toPlainText()


def test_a_busy_folder_refuses_the_run(qtbot, open_app, service, library_dir, fake):
    album = library_dir / "Fixture Artist" / "Album B"
    token = service.locks.acquire([album / "01 Loud.flac"], "Rename")
    window = run(qtbot, open_app, service, album)
    assert window.view.message.text() == "ReplayGain didn't start: Album B is busy: Rename is running on it."
    assert fake.calls == []
    service.locks.release(token)


def test_report_buttons_open_the_log_and_the_folder(qtbot, open_app, service, library_dir, fake, monkeypatch):
    launched, shown = [], []
    monkeypatch.setattr(fsops, "open_with_default_app", launched.append)
    monkeypatch.setattr(fsops, "open_in_explorer", lambda path, is_dir: shown.append(path))
    album = library_dir / "Fixture Artist" / "Album B"
    window = run(qtbot, open_app, service, album)
    window.view.open_log_button.click()
    window.view.reveal_button.click()
    qtbot.waitUntil(lambda: bool(launched and shown))
    assert launched == [str(album / "replaygain.log")] and shown == [str(album)]


def test_nothing_to_do(qtbot, open_app, service, library_dir, fake):
    """No track here: the run does nothing and says so."""
    window = run(qtbot, open_app, service, library_dir / "Fixture Artist" / "Album B" / "Scans")
    assert window.view.message.text() == "Nothing was done: no audio file rsgain can scan here."
    assert fake.calls == []


def test_unsaved_edits_are_asked_before_the_run(qtbot, open_app, service, library_dir, fake):
    album = library_dir / "Fixture Artist" / "Album B"
    main = open_app.window
    main.open_folder(folder_id(service, album))
    ids = {r.name: r.id for r in main.file_view.model.rows}
    main.file_view.select_ids([ids["01 Loud.flac"]])
    metadata = main.side_panel.metadata
    qtbot.waitUntil(lambda: metadata.snapshot is not None, timeout=5000)
    metadata.edits["comment"].setText("mine")
    metadata.edits["comment"].textEdited.emit("mine")
    metadata.ask_leave = lambda: "cancel"
    main.file_view.select_ids([])
    menu = QMenu(main)
    main.tools.fill_replaygain_menu(menu)
    menu.actions()[0].trigger()
    assert open_app.entry_windows == [] and fake.calls == []  # Cancel: no window, nothing run
    metadata.revert()


def test_the_real_rsgain_through_the_window(qtbot, open_app, service, library_dir):
    """The bundled rsgain, end to end (fixtures copied; about a second per album)."""
    album = library_dir / "Fixture Artist" / "Album B"
    window = run(qtbot, open_app, service, album)
    finished = window.view.finished
    assert [log.result() for log in finished.album_logs] == ["OK"], window.view.text.toPlainText()
    assert rg(album / "01 Loud.flac", ALBUM_GAIN) is not None


def test_the_real_rsgain_while_the_library_is_scanned(qtbot, open_app, service, library_dir, monkeypatch):
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
    window = start(qtbot, open_app, service, album)
    qtbot.waitUntil(lambda: window.view.state in ("result", "error"), timeout=30000)
    assert service.indexer.busy  # the scan was still running all along
    finished = window.view.finished
    gate.set()
    assert [log.result() for log in finished.album_logs] == ["OK"], window.view.text.toPlainText()


def test_the_real_rsgain_while_tags_are_read(qtbot, open_app, service, library_dir, fixtures_dir, monkeypatch):
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
    window = start(qtbot, open_app, service, album)
    qtbot.waitUntil(lambda: window.view.state in ("result", "error"), timeout=60000)
    elapsed = time.monotonic() - started
    assert service.indexer.busy, "the tag reading should still be running"
    finished = window.view.finished
    assert [log.result() for log in finished.album_logs] == ["OK"], window.view.text.toPlainText()
    assert elapsed < 20, elapsed


def test_elapsed_time_while_an_album_is_scanned(qtbot, open_app, service, library_dir, fake):
    """rsgain's output only arrives when it exits, so the page shows the album's elapsed time meanwhile."""
    import threading

    release = threading.Event()
    fake.before = lambda folder: release.wait(10)  # the "rsgain" run lasts until the test releases it
    view = start(qtbot, open_app, service, library_dir / "Fixture Artist" / "Album B").view
    qtbot.waitUntil(lambda: view.message.text().startswith("Album 1 of 1: Album B · 0:0"), timeout=5000)
    first = view.message.text()
    assert "rsgain reports each album when it is done" in first
    qtbot.waitUntil(lambda: view.message.text() != first, timeout=3000)  # it ticks
    release.set()
    qtbot.waitUntil(lambda: view.state == "result", timeout=10000)
    assert not view._clock.isActive() and view.message.text().startswith("Done")


def test_the_selection_is_kept_after_replaygain(qtbot, open_app, service, library_dir, fake):
    album = library_dir / "Fixture Artist" / "Album B"
    main = open_app.window
    window = run(qtbot, open_app, service, album, names=["01 Loud.flac", "02 Quiet.mp3"],
                 entry="ReplayGain: these 2 tracks as an album")
    assert window.view.finished.album_logs[0].result() == "OK"
    ids = {r.name: r.id for r in main.file_view.model.rows}
    model = main.file_view.model
    qtbot.waitUntil(lambda: model.rows[model.row_for(ids["01 Loud.flac"])].rg == "full",
                    timeout=10000)  # the main window's rescan reached the list: the row shows the new tags
    assert main.file_view.selected_ids() == [ids["01 Loud.flac"], ids["02 Quiet.mp3"]]


def test_m5_done_when_the_windows_replaygain_log_matches_the_script(qtbot, open_app, service, library_dir,
                                                                     reference_music_tools, monkeypatch, tmp_path):
    """M5's "Done when": the app's replaygain.log, from a run through the ReplayGain window with the real rsgain,
    matches a script run on a copy of the same album (timestamps masked); the tags match too."""
    import contextlib

    from test_replaygain_parity import compare_trees

    from music_desk.paths import rsgain_path

    monkeypatch.setattr(reference_music_tools, "RSGAIN_PATH", str(rsgain_path()))
    monkeypatch.setattr(reference_music_tools, "AUDIO_EXTENSIONS", {".flac", ".mp3"})
    app_album = library_dir / "Fixture Artist" / "Album A"
    script_album = shutil.copytree(app_album, tmp_path / "script" / "Album A")
    with contextlib.chdir(script_album):
        reference_music_tools.process_album()

    window = start(qtbot, open_app, service, app_album)
    qtbot.waitUntil(lambda: window.view.state in ("result", "error"), timeout=60000)
    assert window.view.finished.album_logs[0].result() == "OK", window.view.text.toPlainText()
    assert compare_trees(script_album, app_album) == 1
