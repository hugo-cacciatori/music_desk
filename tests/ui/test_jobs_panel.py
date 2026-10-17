"""The jobs panel: jobs keep running while you browse, the drawer follows them, reopens their pages and stops them;
one job per tool at a time; quitting asks; and M5's "Done when" through the window (SPEC §10)."""

import contextlib
import shutil
import threading

import pytest
from rsgain_fake import FakeScanner

from music_desk.core.index.store import path_of
from music_desk.core.rsgain import ScanResult
from music_desk.services.jobs import DONE, RUNNING, STOPPED
from music_desk.ui.tools.tool_cards import RENAME, REPLAYGAIN


class BlockingScanner(FakeScanner):
    """Tags like rsgain once released; until then the job is running. A cancel ends it as rsgain would."""

    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, files, folder, target, exe, cancel=None, on_line=None):
        self.started.set()
        while not self.release.wait(0.02):
            if cancel is not None and cancel.cancelled:
                return ScanResult(1, "", "", 0.1, cancelled=True)
        return super().__call__(files, folder, target, exe, cancel=cancel, on_line=on_line)


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "replaygain" / "Fixture Artist", lib / "Fixture Artist")
    shutil.copytree(fixtures_dir / "album" / "Fixture Artist - Fixture Album", lib / "Other")
    return lib


@pytest.fixture
def scanner(qtbot, window):
    scanner = BlockingScanner()
    window.tools.rg_runner.scanner = scanner
    # pytest-qt closes the window before fixtures end: a job still running then is stopped, without the question
    window.confirm_quit_with_jobs = lambda names: True
    yield scanner
    scanner.release.set()


def folder_id(service, path) -> int:
    conn = service.conn
    return next(i for (i,) in conn.execute("SELECT id FROM entries WHERE is_dir = 1") if path_of(conn, i) == path)


def start_replaygain(qtbot, window, service, folder):
    window.open_folder(folder_id(service, folder))
    window.tools.start(REPLAYGAIN)  # Start: the job starts at once, on the open folder (a selection's too)


def test_a_job_runs_on_while_browsing(qtbot, window, service, library_dir, scanner):
    album = library_dir / "Fixture Artist" / "Album B"
    start_replaygain(qtbot, window, service, album)
    assert scanner.started.wait(5)
    assert window.jobs_dock.isVisible() and window.jobs_button.text() == "Jobs: 1 running"
    [job] = window.jobs.jobs
    assert (job.kind, job.folder, job.state) == ("ReplayGain", album, RUNNING)
    assert window.jobs_panel.rows()[0][:2] == ("ReplayGain", str(album))

    window.open_folder(folder_id(service, library_dir / "Other"))  # browsing elsewhere: the job goes on
    assert not window.showing_tools and window.tools.rg_runner.running
    window.jobs_panel.open_button.click()  # the drawer reopens its live page
    assert window.center.currentWidget() is window.rg_view and window.rg_view.state == "running"

    window.close_tools()
    scanner.release.set()
    qtbot.waitUntil(lambda: job.state == DONE, timeout=10000)
    assert window.jobs_panel.rows()[0][2] == "Done: 1 album OK"
    assert window.jobs_button.text() == "Jobs" and not window.showing_tools  # the list stays on screen
    assert "replaygain" in job.reports


def test_one_job_per_tool(qtbot, window, service, library_dir, scanner):
    start_replaygain(qtbot, window, service, library_dir / "Fixture Artist" / "Album B")
    assert scanner.started.wait(5)
    window.open_folder(folder_id(service, library_dir / "Fixture Artist" / "Album A"))
    window.tools.start(REPLAYGAIN)  # a second ReplayGain run: the running one is shown instead
    assert "ReplayGain is still running in" in window.toast.label.text()
    assert window.center.currentWidget() is window.rg_view and window.rg_view.state == "running"
    window.file_view.select_ids([])
    window.tools.start(RENAME)  # the Rename tool is free
    qtbot.waitUntil(lambda: window.rename_preview.state == "preview", timeout=10000)
    scanner.release.set()
    qtbot.waitUntil(lambda: not window.jobs.running, timeout=10000)


def test_stop_from_the_panel(qtbot, window, service, library_dir, scanner):
    start_replaygain(qtbot, window, service, library_dir / "Fixture Artist" / "Album B")
    assert scanner.started.wait(5)
    window.close_tools()
    window.jobs_panel.stop_button.click()
    job = window.jobs.jobs[0]
    qtbot.waitUntil(lambda: job.state == STOPPED, timeout=10000)
    assert window.jobs_panel.rows()[0][2] == "Stopped: nothing was leveled; see the report"


def test_finished_reports_reopen_and_clear(qtbot, window, service, library_dir, scanner):
    scanner.release.set()
    first = library_dir / "Fixture Artist" / "Album B"
    start_replaygain(qtbot, window, service, first)
    qtbot.waitUntil(lambda: window.jobs.jobs[0].state == DONE, timeout=10000)
    window.open_folder(folder_id(service, library_dir / "Other"))
    window.file_view.select_ids([])
    window.tools.start(RENAME)
    qtbot.waitUntil(lambda: window.rename_preview.state == "preview", timeout=10000)
    window.rename_preview.apply_button.click()
    qtbot.waitUntil(lambda: len(window.jobs.jobs) == 2 and window.jobs.jobs[1].state == DONE, timeout=10000)
    assert [row[0] for row in window.jobs_panel.rows()] == ["Rename", "ReplayGain"]  # the newest first
    assert window.jobs_panel.rows()[0][2].startswith("Done: ") and "renamed" in window.jobs_panel.rows()[0][2]

    window.show_job(window.jobs.jobs[0].id)  # the first job's report, again
    assert window.center.currentWidget() is window.rg_view and window.rg_view.state == "result"
    assert str(first) in window.rg_view.heading.text()
    window.show_job(window.jobs.jobs[1].id)
    assert window.center.currentWidget() is window.rename_preview and window.rename_preview.state == "result"

    window.jobs_panel.clear_button.click()
    assert window.jobs.jobs == [] and window.jobs_panel.rows() == [] and window.jobs_button.text() == "Jobs"


def test_the_selection_is_kept_after_replaygain(qtbot, window, service, library_dir, scanner):
    scanner.release.set()
    album = library_dir / "Fixture Artist" / "Album B"
    window.open_folder(folder_id(service, album))
    ids = {r.name: r.id for r in window.file_view.model.rows}
    window.file_view.select_ids([ids["01 Loud.flac"]])
    start_replaygain(qtbot, window, service, album)  # the open folder: the job tags the selected file too
    qtbot.waitUntil(lambda: window.jobs.jobs[0].state == DONE, timeout=10000)
    model = window.file_view.model
    qtbot.waitUntil(lambda: model.rows[model.row_for(ids["01 Loud.flac"])].rg == "full",
                    timeout=10000)  # the job's recursive rescan reached the list: the row shows the new tags
    window.close_tools()
    assert window.file_view.selected_ids() == [ids["01 Loud.flac"]]


def test_quitting_with_a_running_job_asks(qtbot, window, service, library_dir, scanner):
    start_replaygain(qtbot, window, service, library_dir / "Fixture Artist" / "Album B")
    assert scanner.started.wait(5)
    asked = []
    window.confirm_quit_with_jobs = lambda names: asked.append(names) or False
    window.close()
    assert window.isVisible() and asked == [[f"ReplayGain: {library_dir / 'Fixture Artist' / 'Album B'}"]]
    window.confirm_quit_with_jobs = lambda names: True
    window.close()  # stops the job (rsgain is cancelled), then closes
    assert not window.isVisible()


@pytest.mark.slow
def test_m5_done_when_the_windows_replaygain_log_matches_the_script(qtbot, window, service, library_dir,
                                                                     reference_music_tools, monkeypatch, tmp_path):
    """M5's "Done when": the app's replaygain.log, from a job run through the window with the real rsgain, matches
    a script run on a copy of the same album (timestamps masked); the tags match too."""
    from test_replaygain_parity import compare_trees

    from music_desk.paths import rsgain_path

    monkeypatch.setattr(reference_music_tools, "RSGAIN_PATH", str(rsgain_path()))
    monkeypatch.setattr(reference_music_tools, "AUDIO_EXTENSIONS", {".flac", ".mp3"})
    app_album = library_dir / "Fixture Artist" / "Album A"
    script_album = shutil.copytree(app_album, tmp_path / "script" / "Album A")
    with contextlib.chdir(script_album):
        reference_music_tools.process_album()

    start_replaygain(qtbot, window, service, app_album)
    qtbot.waitUntil(lambda: window.jobs.jobs and window.jobs.jobs[0].state != RUNNING, timeout=60000)
    assert window.jobs.jobs[0].state == DONE, window.rg_view.text.toPlainText()
    assert compare_trees(script_album, app_album) == 1
