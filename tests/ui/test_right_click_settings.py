"""The right-click Settings entry, app side (SPEC §31): the start's arguments and list file, the first track of the
clicked items, the hand-over to a running copy, and the Options window with or without the main window."""

import subprocess
import sys
import uuid

from music_desk.app import Starter
from music_desk.context_menu.request import SETTINGS, StartRequest, first_track, parse_arguments
from music_desk.instance import InstanceServer
from music_desk.settings import Settings

# -- the start's arguments --

def test_a_normal_start_keeps_its_folder():
    assert parse_arguments([r"D:\Music"]) == StartRequest(None, (r"D:\Music",))
    assert parse_arguments(["--verbose", r"D:\Music"]).folder == r"D:\Music"
    assert parse_arguments([]).folder is None


def test_an_entry_takes_every_path():
    request = parse_arguments(["--settings", r"D:\a", r"D:\b"])
    assert request == StartRequest(SETTINGS, (r"D:\a", r"D:\b")) and request.folder is None


def test_a_list_file_in_the_menu_folder_is_read_then_deleted(tmp_path):
    menu = tmp_path / "menu"
    menu.mkdir()
    listed = menu / "1234.txt"
    listed.write_text("\ufeffD:\\Music\\Ä\r\n\r\nD:\\Music\\B  \r\n", encoding="utf-8")
    assert parse_arguments(["--settings", "--list", str(listed)], menu).paths == (r"D:\Music\Ä", r"D:\Music\B")
    assert not listed.exists()


def test_a_list_file_elsewhere_is_only_read(tmp_path):
    listed = tmp_path / "mine.txt"
    listed.write_text("D:\\x\n", encoding="utf-8")
    assert parse_arguments(["--settings", "--list", str(listed)], tmp_path / "menu").paths == (r"D:\x",)
    assert listed.exists()
    assert parse_arguments(["--settings", "--list", str(tmp_path / "gone.txt")]).paths == ()


# -- the File names example's track --

def test_first_track(library_dir, tmp_path):
    album = library_dir / "Artist" / "Album"
    assert first_track([album / "Track 3.mp3"]) == album / "Track 3.mp3"
    assert first_track([album / "Track 10.flac", album / "cover.jpg", album / "Track 2.flac"]) == album / "Track 2.flac"
    assert first_track([album]) == album / "track 1.flac"  # natural order, ignoring case
    assert first_track([library_dir / "Artist"]) == album / "track 1.flac"  # in a subfolder
    assert first_track([library_dir / "Artist" / "Scans", album / "cover.jpg"]) is None
    assert first_track([tmp_path / "gone"]) is None


# -- a running copy --

def test_a_later_start_hands_its_entry_over(qtbot):
    name = f"MusicDesk-test-{uuid.uuid4().hex}"
    server = InstanceServer(name)
    entries, folders = [], []
    server.entryRequested.connect(lambda entry, paths: entries.append((entry, paths)))
    server.folderRequested.connect(folders.append)
    code = ("from PySide6.QtCore import QCoreApplication; app = QCoreApplication([]); "
            "from music_desk.instance import hand_over; "
            f"raise SystemExit(0 if hand_over(None, {name!r}, 'settings', ['D:/a', 'D:/b']) else 1)")
    process = subprocess.Popen([sys.executable, "-c", code])
    qtbot.waitUntil(lambda: entries == [("settings", ["D:/a", "D:/b"])], timeout=15000)
    assert process.wait(10) == 0 and folders == []
    server.close()


# -- the Options window --

def open_settings(qtbot, service, paths):
    starter = Starter(Settings(), lambda: service, check_tools=False)
    starter.run_entry(SETTINGS, [str(p) for p in paths])
    return starter


def test_settings_alone_opens_only_the_options_window(qtbot, service, library_dir, monkeypatch):
    quits = []
    monkeypatch.setattr("music_desk.app.QApplication.quit", lambda: quits.append(True))
    starter = open_settings(qtbot, service, [library_dir / "Artist" / "Album"])
    cards = starter.settings_entry.cards
    dialog = cards.options_window
    qtbot.addWidget(dialog)
    assert dialog.isVisible() and starter.window is None
    qtbot.waitUntil(lambda: cards.names_card.example.text().startswith("Example: "), timeout=10000)
    starter.run_entry(SETTINGS, [str(library_dir / "Other")])  # a second click raises the same window
    assert cards.options_window is dialog
    dialog.close()
    assert quits == [True]  # no main window: the process ends with the Options window
    starter.settings_entry.close()


def test_a_normal_start_meanwhile_opens_the_main_window_on_the_same_cards(qtbot, service, library_dir, monkeypatch):
    quits = []
    monkeypatch.setattr("music_desk.app.QApplication.quit", lambda: quits.append(True))
    starter = open_settings(qtbot, service, [])
    cards = starter.settings_entry.cards
    qtbot.addWidget(cards.options_window)
    starter.open_folder(None)
    window = starter.window
    qtbot.addWidget(window)
    assert window.isVisible() and window.tools.cards is cards and cards.window is window
    cards.options_window.close()
    assert quits == []  # the main window stays, as the app
    starter.settings_entry.close()


def test_settings_with_the_main_window_open_shows_its_own(qtbot, service, library_dir):
    starter = Starter(Settings(), lambda: service, check_tools=False)
    starter.open_folder(None)
    window = starter.window
    qtbot.addWidget(window)
    starter.run_entry(SETTINGS, [str(library_dir / "Artist" / "Album" / "Track 3.mp3")])
    cards = window.tools.cards
    assert cards.options_window.isVisible() and starter.settings_entry is None
    qtbot.waitUntil(lambda: cards.names_card.example.text().endswith(".mp3"), timeout=10000)
