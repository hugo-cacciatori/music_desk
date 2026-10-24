"""The right-click ReplayGain entries with MusicDesk not open (SPEC §31): the ReplayGain page alone in a window of
its own, no main window; the process ends when the last entry's window closes. A fake scanner tags files as rsgain
would."""

import shutil
import threading
from functools import partial

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from rsgain_fake import FakeScanner

from music_desk.app import Starter
from music_desk.context_menu import replaygain_entry
from music_desk.context_menu import request as menu
from music_desk.services.replaygain_job import ReplayGainRunner
from music_desk.settings import Settings


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "replaygain" / "Fixture Artist", lib / "Fixture Artist")
    return lib


@pytest.fixture
def artist(library_dir):
    return library_dir / "Fixture Artist"


@pytest.fixture
def fake(monkeypatch):
    scanner = FakeScanner()
    monkeypatch.setattr(replaygain_entry, "ReplayGainRunner", partial(ReplayGainRunner, scanner=scanner))
    return scanner


@pytest.fixture
def gated(monkeypatch):
    """A scanner that waits on the job thread until the test sets the event: the run stays "running"."""
    event = threading.Event()
    scanner = FakeScanner(before=lambda folder: event.wait(20))
    monkeypatch.setattr(replaygain_entry, "ReplayGainRunner", partial(ReplayGainRunner, scanner=scanner))
    yield event
    event.set()


@pytest.fixture
def quits(monkeypatch):
    calls = []
    monkeypatch.setattr("music_desk.app.QApplication.quit", lambda: calls.append(True))
    return calls


@pytest.fixture
def starter(qtbot, service):
    starter = Starter(Settings(), lambda: service, check_tools=False)
    yield starter
    starter.release()  # the entries' windows and the cards made without a main window, freed while Qt runs
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


def start(qtbot, starter, entry, paths):
    starter.run_entry(entry, [str(p) for p in paths])
    window = starter.replaygain_windows[-1]
    qtbot.addWidget(window)
    return window


def wait_done(qtbot, window):
    qtbot.waitUntil(lambda: window.view.state in ("result", "error"), timeout=20000)
    return window.view.finished


def test_an_album_runs_in_its_own_window(qtbot, starter, artist, fake, quits):
    album = artist / "Album A"
    window = start(qtbot, starter, menu.REPLAYGAIN_ALBUM, [album])
    assert starter.window is None and window.isVisible()  # no main window
    assert window.view.state == "running" and not window.view.back_button.isVisible()
    finished = wait_done(qtbot, window)
    assert [log.result() for log in finished.album_logs] == ["OK"] and (album / "replaygain.log").exists()
    assert window.view.message.text().startswith("Done: 1 album OK.")
    assert window.view.open_log_button.isVisible() and window.view.done_button.isVisible()
    assert quits == []  # the report stays until it is closed
    window.view.done_button.click()
    assert starter.replaygain_windows == [] and quits == [True]


def test_loose_tracks_report_in_the_window(qtbot, starter, artist, fake, quits):
    window = start(qtbot, starter, menu.REPLAYGAIN_LOOSE, [artist / "Album A" / "02 Spike.flac"])
    wait_done(qtbot, window)
    assert "this track" in window.view.heading.text()
    assert window.view.message.text() == "Done: 1 track OK, each on its own."
    assert not window.view.open_log_button.isVisible()


def test_a_second_entry_while_running_is_refused(qtbot, starter, artist, gated, quits):
    window = start(qtbot, starter, menu.REPLAYGAIN_ALBUM, [artist / "Album A"])
    starter.run_entry(menu.REPLAYGAIN_ALBUM, [str(artist / "Album B")])
    assert len(starter.replaygain_windows) == 1 and window.note.isVisible()  # one rsgain at a time
    gated.set()
    wait_done(qtbot, window)
    assert not (artist / "Album B" / "replaygain.log").exists()


def test_after_a_report_a_new_entry_gets_a_new_window(qtbot, starter, artist, fake, quits):
    first = start(qtbot, starter, menu.REPLAYGAIN_ALBUM, [artist / "Album A"])
    wait_done(qtbot, first)
    second = start(qtbot, starter, menu.REPLAYGAIN_ALBUM, [artist / "Album B"])
    wait_done(qtbot, second)
    assert first.isVisible() and first.view.finished.folder == artist / "Album A"
    first.close()
    assert quits == []  # one window is still open
    second.close()
    assert quits == [True]


def test_closing_while_running_asks_first(qtbot, starter, artist, gated, quits):
    window = start(qtbot, starter, menu.REPLAYGAIN_ALBUM, [artist / "Album A"])
    answers = []
    window.confirm_stop = lambda: answers.append(True) or False
    window.close()
    assert answers == [True] and window.isVisible() and quits == []
    window.confirm_stop = lambda: True
    gated.set()
    window.close()
    assert starter.replaygain_windows == [] and quits == [True]


def test_options_needing_a_fix_refuse_the_run(qtbot, starter, artist, fake, quits):
    starter.cards().set_rsgain_unavailable("rsgain couldn't run")
    window = start(qtbot, starter, menu.REPLAYGAIN_ALBUM, [artist / "Album A"])
    assert window.view.state == "error" and "rsgain couldn't run" in window.view.message.text()
    assert fake.calls == []


def test_a_normal_start_meanwhile_shares_locks_and_options(qtbot, starter, service, artist, fake, quits):
    window = start(qtbot, starter, menu.REPLAYGAIN_ALBUM, [artist / "Album A"])
    starter.open_folder(None)
    main = starter.window
    qtbot.addWidget(main)
    assert service.locks is starter.locks and main.tools.cards is window.cards
    wait_done(qtbot, window)
    window.close()
    assert quits == []  # the main window stays, as the app
    main.tools.rg_runner.scanner = fake
    starter.run_entry(menu.REPLAYGAIN_ALBUM, [str(artist / "Album B")])  # now a job of the main window
    qtbot.waitUntil(lambda: main.rg_view.state == "result" and main.tools.rg_job is None, timeout=20000)
    assert starter.replaygain_windows == []
