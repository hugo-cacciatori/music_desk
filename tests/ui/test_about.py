"""Help › About MusicDesk (SPEC §12): versions, the components and their licences, the licences folder."""

import threading

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
    menu = window.more_button.menu()  # the … button (SPEC §36 task 3)
    next(a for a in menu.actions() if a.text() == "&About MusicDesk").trigger()
    return shown


def test_about_shows_versions_and_components(window):
    window.show_rsgain_version("rsgain 3.6")
    [dialog] = open_about(window)
    assert "rsgain 3.6" in dialog.versions.text()
    assert any("version 3 or later" in label.text() for label in dialog.findChildren(type(dialog.versions)))
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


def test_flac_version_and_unavailable(window):
    """The cue split's flac (SPEC §13): its self-check result is shown beside rsgain's."""
    window.show_flac_version("flac 1.5.0")
    [dialog] = open_about(window)
    assert "Cue split: flac 1.5.0" in dialog.versions.text()
    window.show_flac_version(None)
    [dialog] = open_about(window)
    assert "Cue split: flac unavailable" in dialog.versions.text()
