"""Opening Refrain on a folder from outside (SPEC §29): the folder argument, one instance at a time, and
RefrainHere.exe."""

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from browser_helpers import entry

from refrain import here
from refrain.app import folder_argument
from refrain.instance import InstanceServer, hand_over


def wait_open(qtbot, window, service, path: Path) -> None:
    qtbot.waitUntil(lambda: window.folder_path is not None
                    and os.path.normcase(window.folder_path) == os.path.normcase(str(path)), timeout=20000)


# -- the window --

def test_an_indexed_folder_opens(qtbot, window, service, library_dir):
    window.open_folder(entry(service, "Artist"))
    window.open_from_outside(str(library_dir / "Artist" / "Album"))
    assert window.folder_id == entry(service, "Artist", "Album") and window._waiting_folder is None
    assert window.center.currentWidget() is window.file_view


def test_a_new_folder_inside_the_library_opens_once_rescanned(qtbot, window, service, library_dir, fixtures_dir):
    new = library_dir / "Artist" / "New" / "Deeper"
    new.mkdir(parents=True)
    shutil.copy2(fixtures_dir / "formats" / "tone.flac", new / "01.flac")
    window.open_from_outside(str(new))
    wait_open(qtbot, window, service, new)
    assert service.roots()[0].path == str(library_dir)  # no new library folder


def test_a_folder_outside_the_library_is_added_then_opens(qtbot, window, service, tmp_path, fixtures_dir):
    outside = tmp_path / "elsewhere" / "Album"
    outside.mkdir(parents=True)
    shutil.copy2(fixtures_dir / "formats" / "tone.flac", outside / "01.flac")
    window.open_from_outside(str(outside))
    wait_open(qtbot, window, service, outside)
    assert str(outside) in [root.path for root in service.roots()]


def test_a_missing_folder_says_so(qtbot, window, service, tmp_path):
    missing = tmp_path / "gone"
    window.open_from_outside(str(missing))
    assert "isn't a folder that can be reached" in window.statusBar().currentMessage()
    assert window._waiting_folder is None and len(service.roots()) == 1


def test_no_folder_only_brings_the_window_to_the_front(qtbot, window, service):
    window.showMinimized()
    folder = window.folder_id
    window.open_from_outside(None)
    qtbot.waitUntil(lambda: not window.isMinimized())
    assert window.folder_id == folder


# -- one instance at a time --

def start_later(name: str, folder: str | None) -> subprocess.Popen:
    """A second start in its own process, as for real: the blocking client can't share the server's thread."""
    code = ("from PySide6.QtCore import QCoreApplication; app = QCoreApplication([]); "
            "from refrain.instance import hand_over; "
            f"raise SystemExit(0 if hand_over({folder!r}, {name!r}) else 1)")
    return subprocess.Popen([sys.executable, "-c", code])


def test_a_later_start_hands_its_folder_over(qtbot):
    name = f"Refrain-test-{uuid.uuid4().hex}"
    server = InstanceServer(name)
    assert server.listening
    received = []
    server.folderRequested.connect(received.append)
    for folder, expected in ((r"D:\Music\Album", [r"D:\Music\Album"]), (None, [r"D:\Music\Album", None])):
        process = start_later(name, folder)
        qtbot.waitUntil(lambda expected=expected: received == expected, timeout=15000)
        assert process.wait(10) == 0
    qtbot.waitUntil(lambda: not server.connections, timeout=5000)  # destroyed mid-teardown, a socket aborted
    server.close()


def test_no_running_app_means_no_hand_over():
    assert not hand_over("D:/Music", f"Refrain-test-{uuid.uuid4().hex}")


def test_folder_argument():
    assert folder_argument([]) is None
    assert folder_argument(["--verbose", r"D:\Music"]) == r"D:\Music"


# -- RefrainHere.exe --

def test_here_starts_refrain_on_its_working_directory():
    calls = []
    assert here.main(cwd=r"D:\Music\Album", executable=r"C:\Program Files\Refrain\RefrainHere.exe",
                     launch=lambda args, **kwargs: calls.append((args, kwargs)), allow_foreground=lambda: None) == 0
    [(args, kwargs)] = calls
    assert args == [r"C:\Program Files\Refrain\Refrain.exe", r"D:\Music\Album"]
    assert kwargs["cwd"] == r"C:\Program Files\Refrain"
