"""Using the playlist builder (SPEC §15, M7 task 4): Export… to a .m3u8 with relative or absolute paths (the
choice and the folder remembered), Play through the default player, and tracks gone from the library left out.

Nothing is launched (the window's launcher is a recorder) and the Save dialog is answered through
`choose_export_file`. Exports go into the test's copied library or tmp_path.
"""

from pathlib import Path

import pytest
from browser_helpers import entry

from music_desk.core.playlist_builder import Item
from music_desk.settings import Settings
from music_desk.ui.main_window import MainWindow

ALBUM_FILES = ["track 1.flac", "Track 2.flac", "Track 3.mp3", "Track 10.flac"]


@pytest.fixture
def settings(tmp_path):
    return Settings(path=tmp_path / "app" / "settings.json")


@pytest.fixture
def window(qtbot, service, settings):
    window = MainWindow(service, settings)
    qtbot.addWidget(window)
    window.show()
    return window


def fill(window, service) -> None:
    window.playlist_panel.model.add_entries([entry(service, "Artist", "Album")])


def export(qtbot, window, target: Path | str, asked: list | None = None) -> Path:
    def choose(suggested: str) -> str:
        if asked is not None:
            asked.append(suggested)
        return str(target)
    window.choose_export_file = choose
    with qtbot.waitSignal(window.playlistExported, timeout=5000) as blocker:
        window.playlist_panel.export_button.click()
    return Path(blocker.args[0])


def paths_in(file: Path) -> list[str]:
    text = file.read_bytes().decode("utf-8")
    assert text.startswith("#EXTM3U\r\n")
    return [line for line in text.split("\r\n") if line and not line.startswith("#")]


def test_buttons_follow_the_playlist(window, service):
    panel = window.playlist_panel
    assert not panel.play_button.isEnabled() and not panel.export_button.isEnabled()
    fill(window, service)
    assert panel.play_button.isEnabled() and panel.export_button.isEnabled()


def test_export_with_relative_paths(qtbot, window, service, library_dir, settings):
    fill(window, service)
    assert window.playlist_panel.relative_paths.isChecked()  # the default
    asked = []
    saved = export(qtbot, window, library_dir / "Artist" / "mix", asked)  # no extension typed
    assert saved == library_dir / "Artist" / "mix.m3u8"
    assert asked == [str(Path.home() / "Music" / "Playlist.m3u8")]
    assert paths_in(saved) == [str(Path("Album") / name) for name in ALBUM_FILES]
    assert "#EXTINF:" in saved.read_text(encoding="utf-8")
    assert settings.playlist_export_dir == str(library_dir / "Artist")
    assert window.statusBar().currentMessage() == f"Exported 4 tracks to {saved}."

    export(qtbot, window, saved, asked)  # the next export starts in the same folder
    assert asked[-1] == str(library_dir / "Artist" / "Playlist.m3u8")


def test_export_with_absolute_paths_is_remembered(qtbot, window, service, library_dir, settings, tmp_path):
    fill(window, service)
    window.playlist_panel.relative_paths.setChecked(False)
    assert settings.playlist_relative is False
    saved = export(qtbot, window, tmp_path / "out.m3u8")
    assert paths_in(saved) == [str(library_dir / "Artist" / "Album" / name) for name in ALBUM_FILES]

    again = MainWindow(service, settings)
    qtbot.addWidget(again)
    assert not again.playlist_panel.relative_paths.isChecked()


def test_missing_tracks_are_left_out(qtbot, window, service, tmp_path):
    fill(window, service)
    window.playlist_panel.model.add([Item(None, str(tmp_path / "gone.flac"))])
    saved = export(qtbot, window, tmp_path / "out.m3u8")
    assert len(paths_in(saved)) == 4
    assert window.statusBar().currentMessage().endswith("1 not in the library was left out.")


def test_cancelling_the_dialog_writes_nothing(window, service, tmp_path):
    fill(window, service)
    window.choose_export_file = lambda suggested: ""
    window.export_playlist()
    assert not list(tmp_path.glob("*.m3u8"))


def test_a_failed_export_says_so(qtbot, window, service, tmp_path):
    fill(window, service)
    window.choose_export_file = lambda suggested: str(tmp_path / "no such folder" / "out.m3u8")
    window.export_playlist()
    qtbot.waitUntil(window.toast.isVisible, timeout=3000)
    assert "couldn't be saved" in window.toast.label.text()


def test_play_uses_a_temporary_playlist(qtbot, window, service, tmp_path):
    launched = []
    window.launch = launched.append
    window.playlist_dir = tmp_path / "playlists"
    fill(window, service)
    window.playlist_panel.model.move_rows([3], -1)
    window.playlist_panel.play_button.click()
    qtbot.waitUntil(lambda: bool(launched), timeout=5000)
    played = Path(launched[0])
    assert played.parent == tmp_path / "playlists" and played.name.startswith("play-")
    assert [Path(p).name for p in paths_in(played)] == ["track 1.flac", "Track 2.flac", "Track 10.flac",
                                                        "Track 3.mp3"]
