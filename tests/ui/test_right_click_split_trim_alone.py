"""Windows alone (`ToolHost`, SPEC §31), driven by the app's controllers, with the real bundled flac on copied
fixtures: the track's Trim page ("Trim this track…" with MusicDesk closed), and the cue editor ("Edit this cue
sheet…", MusicDesk open or closed). The process ends when the last window closes without a main window. The Split
entry: test_right_click_split.py."""

import shutil

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from music_desk.app import Starter
from music_desk.context_menu import request as menu
from music_desk.context_menu.tool_host import CueEditorWindow, TrimWindow
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


# -- "Edit this cue sheet…": the cue editor alone, MusicDesk open or closed --

def editor(qtbot, starter, paths) -> CueEditorWindow:
    starter.run_entry(menu.EDIT_CUE, [str(p) for p in paths])
    window = starter.entry_windows[-1]
    assert isinstance(window, CueEditorWindow)
    return window


def on_screen(page) -> list[str]:
    return [b.text() for b in (page.back_button, page.save_button, page.save_as_button, page.preview_button,
                                page.split_button, page.stop_button, page.done_button) if b.isVisible()]


def test_the_editor_alone_edits_and_saves(qtbot, starter, albums, quits):
    cue = albums / "wav_image" / "image.cue"
    window = editor(qtbot, starter, [cue])
    split = window.controller
    split.recycle = lambda _path: None  # the old version would go to the Recycle Bin: a recorder here
    split.check_recycle = lambda _path: None
    assert starter.window is None and window.windowTitle() == "MusicDesk · Edit cue sheet"
    qtbot.waitUntil(lambda: split.page.state == "editing" and not split._planning, timeout=20000)
    # An editor: Save, Save as and Preview in player; no Split (the Split entry does that), no Back to a list.
    assert on_screen(split.page) == ["Save", "Save as…", "Preview in player"]
    split.page.cell(0, "title").setText("Edited Title")
    qtbot.waitUntil(lambda: split.doc.modified and split.page.save_button.isEnabled(), timeout=5000)
    split.page.save_button.click()
    qtbot.waitUntil(lambda: "Edited Title" in cue.read_text(encoding="utf-8", errors="replace"), timeout=10000)
    window.close()
    assert starter.entry_windows == [] and quits == [True]


def test_the_editor_asks_about_unsaved_edits_before_closing(qtbot, starter, albums, quits):
    window = editor(qtbot, starter, [albums / "wav_image" / "image.cue"])
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


def test_the_editor_refuses_anything_but_one_cue(qtbot, starter, albums, quits):
    page = editor(qtbot, starter, [albums / "tone.wav"]).controller.page
    assert page.state == "error" and on_screen(page) == ["Close"]  # Done reads Close here


def test_with_musicdesk_open_the_editor_is_its_own_window(qtbot, starter, service, albums, quits):
    starter.open_folder(None)
    main = starter.window
    qtbot.addWidget(main)
    page = main.center.currentWidget()
    window = editor(qtbot, starter, [albums / "wav_image" / "image.cue"])
    assert window.isVisible() and main.center.currentWidget() is page  # the main window stays where it was
    qtbot.waitUntil(lambda: window.controller.page.state == "editing", timeout=20000)
    window.close()


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


def test_a_main_window_meanwhile_leaves_entries_their_own_windows(qtbot, starter, service, albums, quits):
    window = alone(qtbot, starter, menu.TRIM, [albums / "tone.wav"], TrimWindow)
    starter.open_folder(None)
    main = starter.window
    qtbot.addWidget(main)
    window.close()
    assert quits == []
    alone(qtbot, starter, menu.TRIM, [albums / "tone.wav"], TrimWindow)  # its own window still (SPEC §35)
    assert main.center.currentWidget() is main.file_view
