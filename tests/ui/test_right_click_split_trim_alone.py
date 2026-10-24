"""The right-click Split and Trim with MusicDesk not open (SPEC §31): the sheet's Split page or the track's Trim page
alone in a window of its own (`ToolHost`), driven by the app's controllers, with the real bundled flac on copied
fixtures. No main window; the process ends when the window closes."""

import shutil

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from music_desk.app import Starter
from music_desk.context_menu import request as menu
from music_desk.context_menu.tool_host import SplitWindow, TrimWindow
from music_desk.settings import Settings


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "split" / "wav_image", lib / "Albums" / "wav_image")
    shutil.copy2(fixtures_dir / "formats" / "tone.wav", lib / "Albums" / "tone.wav")
    return lib


@pytest.fixture
def albums(library_dir):
    return library_dir / "Albums"


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


def alone(qtbot, starter, entry, paths, kind):
    starter.run_entry(entry, [str(p) for p in paths])
    window = starter.entry_windows[-1]
    assert isinstance(window, kind)
    # Not given to qtbot.addWidget: a closed window is deleted (deleteLater) while its controller, which points
    # back at it, keeps the Python object alive, and pytest-qt would close it again. The fixture closes what's
    # left.
    return window


# -- Split --

def test_split_alone_splits(qtbot, starter, albums, quits):
    album = albums / "wav_image"
    window = alone(qtbot, starter, menu.SPLIT, [album / "image.cue"], SplitWindow)
    split = window.controller
    assert starter.window is None and window.isVisible() and window.windowTitle() == "MusicDesk · Split"
    qtbot.waitUntil(lambda: split.page.state == "editing" and not split._planning
                    and not split._plan_timer.isActive(), timeout=20000)
    split.page.split_button.click()
    qtbot.waitUntil(lambda: split.page.state in ("result", "error") and split.job is None, timeout=30000)
    assert split.page.state == "result" and (album / "01 One.flac").exists()
    assert quits == []  # the report stays until the window is closed
    window.close()
    assert starter.entry_windows == [] and quits == [True]


def test_split_alone_unsaved_edits_ask_before_closing(qtbot, starter, albums, quits):
    window = alone(qtbot, starter, menu.SPLIT, [albums / "wav_image" / "image.cue"], SplitWindow)
    split = window.controller
    qtbot.waitUntil(lambda: split.page.state == "editing" and not split._planning, timeout=20000)
    split.page.cell(0, "title").setText("Edited")  # an unsaved edit, typed in the table
    qtbot.waitUntil(lambda: split.doc.modified, timeout=5000)
    asked = []
    split.ask_leave = lambda name: asked.append(name) or "cancel"
    window.close()
    assert asked == ["image.cue"] and window.isVisible() and quits == []
    split.ask_leave = lambda name: "discard"
    window.close()
    assert quits == [True]


def test_split_alone_refuses_anything_but_one_cue(qtbot, starter, albums, quits):
    window = alone(qtbot, starter, menu.SPLIT, [albums / "tone.wav"], SplitWindow)
    assert window.controller.page.state == "error"


# -- Trim --

def test_trim_alone_trims(qtbot, starter, albums, quits):
    window = alone(qtbot, starter, menu.TRIM, [albums / "tone.wav"], TrimWindow)
    trim = window.controller
    assert window.windowTitle() == "MusicDesk · Trim"
    qtbot.waitUntil(lambda: trim.page.state == "editing" and trim.plan is not None and not trim._planning,
                    timeout=10000)
    trim.page.start_edit.setText("0:00.500")
    trim.page.start_edit.textEdited.emit("0:00.500")
    qtbot.waitUntil(lambda: trim.page.trim_button.isEnabled() and not trim._planning, timeout=10000)
    trim.page.trim_button.click()
    qtbot.waitUntil(lambda: trim.page.state in ("result", "error") and trim.job is None, timeout=30000)
    assert trim.page.state == "result" and list(albums.glob("*/tone.flac"))
    trim.page.done_button.click()  # Done closes the window
    assert starter.entry_windows == [] and quits == [True]


def test_trim_alone_refuses_a_folder(qtbot, starter, albums, quits):
    window = alone(qtbot, starter, menu.TRIM, [albums], TrimWindow)
    assert window.controller.page.state == "error"


def test_a_main_window_takes_later_entries(qtbot, starter, service, albums, quits):
    window = alone(qtbot, starter, menu.TRIM, [albums / "tone.wav"], TrimWindow)
    starter.open_folder(None)
    main = starter.window
    qtbot.addWidget(main)
    window.close()
    assert quits == []
    starter.run_entry(menu.SPLIT, [str(albums / "wav_image" / "image.cue")])
    assert starter.entry_windows == [] and main.center.currentWidget() is main.split.page
