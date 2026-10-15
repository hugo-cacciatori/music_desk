"""Help › About MusicDesk (SPEC §12): versions, the components and their licences, the licences folder."""

import threading

from PySide6.QtWidgets import QMenu

from music_desk import __version__
from music_desk.licenses import COMPONENTS, licenses_dir


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.lock = threading.Lock()

    def __call__(self, *args) -> None:
        with self.lock:
            self.calls.append(args)


def open_about(window) -> list:
    shown = []
    window.show_about = shown.append  # never a real modal dialog in tests
    menu = next(m for m in window.menuBar().findChildren(QMenu) if m.title() == "&Help")
    next(a for a in menu.actions() if a.text() == "&About MusicDesk").trigger()
    return shown


def test_about_shows_versions_and_components(window):
    window.show_rsgain_version("rsgain 3.6")
    [dialog] = open_about(window)
    assert "rsgain 3.6" in dialog.versions.text()
    assert dialog.findChildren(type(dialog.versions))[0].text() == f"<b>MusicDesk {__version__}</b>"
    rows = dialog.rows()
    assert [r[0] for r in rows] == [c.name for c in COMPONENTS]
    assert ("mutagen", rows[2][1], "GPL-2.0-or-later") == rows[2] and rows[2][1]


def test_rsgain_unavailable(window):
    window.show_rsgain_version(None)
    [dialog] = open_about(window)
    assert "rsgain unavailable" in dialog.versions.text()


def test_open_licences_folder(qtbot, window):
    recorder = Recorder()
    window.show_in_explorer = recorder
    [dialog] = open_about(window)
    dialog.licenses_button.click()
    qtbot.waitUntil(lambda: recorder.calls == [(str(licenses_dir()), True)], timeout=5000)
