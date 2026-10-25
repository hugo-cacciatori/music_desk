"""The right-click Rename and Remove ReplayGain tags with MusicDesk not open (SPEC §31): the Rename preview page
alone in its own window; Remove's question then its report window alone. No main window; the process ends when
the last entry window closes. Files are copies."""

import shutil

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from music_desk.app import Starter
from music_desk.context_menu import request as menu
from music_desk.context_menu.remove_entry import RemoveEntry
from music_desk.context_menu.rename_entry import RenameWindow
from music_desk.core.replaygain import TRACK_GAIN
from music_desk.core.tags import TaggedFile
from music_desk.settings import Settings

ALBUM = "Fixture Artist - Fixture Album"


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "album" / ALBUM, lib / "Artist" / ALBUM)
    shutil.copytree(fixtures_dir / "cleanup" / "lrc", lib / "Artist" / "Lyrics")
    for name in ("no_lyrics.flac", "with_lyrics.flac"):
        tagged = TaggedFile(lib / "Artist" / "Lyrics" / name)
        tagged.set_replaygain(TRACK_GAIN, "-3.00 dB")
        tagged.save()
    return lib


@pytest.fixture
def artist(library_dir):
    return library_dir / "Artist"


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


class Answers(list):
    """The questions asked (count, where), and the reply to give."""

    reply = True


@pytest.fixture
def answers(monkeypatch):
    replies = Answers()
    monkeypatch.setattr(RemoveEntry, "ask_remove_replaygain",
                        lambda self, count, where, comment: replies.append((count, where)) or replies.reply)
    return replies


def has_rg(path) -> bool:
    return TaggedFile(path).get_replaygain(TRACK_GAIN) is not None


# -- Rename --

def rename_window(qtbot, starter, paths) -> RenameWindow:
    starter.run_entry(menu.RENAME, [str(p) for p in paths])
    window = starter.entry_windows[-1]
    assert isinstance(window, RenameWindow)
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: window.view.state in ("preview", "error"), timeout=10000)
    return window


def test_rename_alone_previews_then_applies(qtbot, starter, artist, quits):
    album = artist / ALBUM
    window = rename_window(qtbot, starter, [album, artist / "Lyrics" / "no_lyrics.flac"])
    view = window.view
    assert starter.window is None and window.isVisible()  # no main window
    assert view.back_bottom.text() == "Close"  # no Back to a list: the page is the window
    assert view.message.text().endswith("Close changes nothing.")
    view.apply_button.click()
    qtbot.waitUntil(lambda: view.state == "result", timeout=10000)
    assert (album / "02 Second.flac").exists() and (artist / "Lyrics" / "02 No Lyrics.flac").exists()
    assert view.done_button.isVisible() and quits == []  # the summary stays until it is closed
    view.done_button.click()
    assert starter.entry_windows == [] and quits == [True]


def test_rename_alone_close_changes_nothing(qtbot, starter, artist, quits):
    window = rename_window(qtbot, starter, [artist / ALBUM])
    window.view.back_bottom.click()
    assert (artist / ALBUM / "track2.flac").exists() and quits == [True]


def test_rename_alone_refuses_options_needing_a_fix(qtbot, starter, artist, quits, monkeypatch):
    monkeypatch.setattr(starter.cards(), "problems", lambda tool: ["a mask is empty"])
    window = rename_window(qtbot, starter, [artist / ALBUM])
    assert window.view.state == "error" and "MusicDesk > Settings" in window.view.message.text()


def test_rename_alone_respects_the_locks(qtbot, starter, artist, quits):
    album = artist / ALBUM
    window = rename_window(qtbot, starter, [album])
    lock = starter.locks.acquire([album], "ReplayGain")
    window.view.apply_button.click()
    assert "is busy: ReplayGain is running on it" in window.view.message.text() and window.view.state == "preview"
    starter.locks.release(lock)


# -- Remove ReplayGain tags --

def test_remove_alone_asks_then_reports(qtbot, starter, artist, quits, answers):
    lyrics = artist / "Lyrics"
    starter.run_entry(menu.REMOVE_REPLAYGAIN, [str(lyrics / "no_lyrics.flac")])
    assert answers == [(1, str(lyrics))] and starter.window is None
    entry = starter.entry_windows[-1]
    assert isinstance(entry, RemoveEntry) and entry.report is not None
    report = entry.report
    qtbot.addWidget(report)
    qtbot.waitUntil(lambda: report.message.text().startswith("Removed"), timeout=10000)
    assert report.message.text() == "Removed the ReplayGain tags of 1 track." and report.isVisible()
    assert not has_rg(lyrics / "no_lyrics.flac") and has_rg(lyrics / "with_lyrics.flac")
    assert quits == []
    report.close_button.click()
    assert starter.entry_windows == [] and quits == [True]


def test_remove_alone_cancelled_ends_at_once(qtbot, starter, artist, quits, answers):
    answers.reply = False
    starter.run_entry(menu.REMOVE_REPLAYGAIN, [str(artist / "Lyrics" / "no_lyrics.flac")])
    assert starter.entry_windows == [] and quits == [True]
    assert has_rg(artist / "Lyrics" / "no_lyrics.flac")


def test_a_main_window_meanwhile_leaves_entries_their_own_windows(qtbot, starter, service, artist, quits):
    """SPEC §35: a normal start meanwhile opens the main window, and later entries still open their own windows.
    Closing the main window leaves them open; the process ends with its last window."""
    window = rename_window(qtbot, starter, [artist / ALBUM])
    starter.open_folder(None)
    main = starter.window
    qtbot.addWidget(main)
    window.close()
    assert quits == []  # the main window stays, as the app
    later = rename_window(qtbot, starter, [artist / "Lyrics"])
    assert later.view.state == "preview" and main.center.currentWidget() is main.file_view
    main.close()
    assert starter.window is None and later.isVisible() and quits == []  # the entry's window stays
    assert service.closed  # the main window's library went with it
    later.close()
    assert quits == [True]
