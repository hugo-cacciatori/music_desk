"""File Explorer's MusicDesk submenu, the handler DLL (SPEC §31): built from native/context_menu/, then driven
through its test exports, which make and initialize the real COM object as Explorer does. Explorer itself showing
the menu is a manual check (the installer registers the handler)."""

import ctypes
import importlib.util
import os
from ctypes import wintypes
from pathlib import Path

import pytest

from music_desk.context_menu.request import SETTINGS, StartRequest, parse_arguments

# packaging/ is a folder of scripts, not a package (and `packaging` is a library's name): load the build script.
_spec = importlib.util.spec_from_file_location("build_menu", Path(__file__).parents[1] / "packaging" / "build_menu.py")
assert _spec is not None and _spec.loader is not None
build_menu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_menu)

EXE = r"C:\Program Files\MusicDesk\MusicDesk.exe"
OPEN_AND_SETTINGS = "MusicDesk:\nmusicdesk.open\nmusicdesk.settings"
SETTINGS_ONLY = "MusicDesk:\nmusicdesk.settings"


@pytest.fixture(scope="module")
def dll(tmp_path_factory):
    if build_menu.find_vcvars() is None:
        pytest.skip("no C++ compiler (Visual Studio Build Tools) on this PC")
    library = ctypes.WinDLL(str(build_menu.build(tmp_path_factory.mktemp("menu"))))
    for name, extra in (("MusicDeskMenu_Entries", []),
                        ("MusicDeskMenu_Command", [wintypes.LPCWSTR, wintypes.LPCWSTR])):
        function = getattr(library, name)
        function.restype = ctypes.c_long
        function.argtypes = [ctypes.POINTER(wintypes.LPCWSTR), ctypes.c_int, wintypes.BOOL, *extra,
                             wintypes.LPWSTR, ctypes.c_int]
    ctypes.windll.ole32.CoInitialize(None)  # the shell's objects, as on Explorer's thread
    return library


def paths_array(paths):
    return (wintypes.LPCWSTR * len(paths))(*[str(p) for p in paths]), len(paths)


def entries(dll, paths, background=False) -> str:
    out = ctypes.create_unicode_buffer(1024)
    array, count = paths_array(paths)
    result = dll.MusicDeskMenu_Entries(array, count, background, out, len(out))
    assert result == 0, hex(result & 0xFFFFFFFF)
    return out.value


def command(dll, paths, verb, background=False) -> list[str]:
    """The started command line, split as MusicDesk's own start splits it."""
    out = ctypes.create_unicode_buffer(4096)
    array, count = paths_array(paths)
    result = dll.MusicDeskMenu_Command(array, count, background, verb, EXE, out, len(out))
    assert result == 0, hex(result & 0xFFFFFFFF)
    split = ctypes.windll.shell32.CommandLineToArgvW
    split.restype = ctypes.POINTER(wintypes.LPWSTR)
    argc = ctypes.c_int()
    argv = split(out.value, ctypes.byref(argc))
    try:
        return [argv[i] for i in range(argc.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(argv)


@pytest.fixture
def items(tmp_path):
    album = tmp_path / "Café 日本"
    other = tmp_path / "Other"
    for folder in (album, other):
        folder.mkdir()
    (album / "01.flac").write_bytes(b"")
    (album / "notes.txt").write_bytes(b"")
    return album, other


def test_the_entries_by_what_was_clicked(dll, items):
    album, other = items
    assert entries(dll, [album], background=True) == OPEN_AND_SETTINGS  # empty space: the open folder
    assert entries(dll, [album]) == OPEN_AND_SETTINGS
    assert entries(dll, [album, other]) == SETTINGS_ONLY
    assert entries(dll, [album / "01.flac"]) == SETTINGS_ONLY
    assert entries(dll, [album / "notes.txt"]) == SETTINGS_ONLY  # files without audio: Settings
    assert entries(dll, [album, album / "01.flac"]) == SETTINGS_ONLY


def test_open_starts_musicdesk_on_the_folder(dll, items):
    album, _other = items
    assert command(dll, [album], "musicdesk.open") == [EXE, str(album)]
    assert command(dll, [album], "musicdesk.open", background=True) == [EXE, str(album)]
    root = Path(album.anchor)  # "C:\": its trailing backslash must not escape the closing quote
    assert command(dll, [root], "musicdesk.open", background=True) == [EXE, str(root)]


def test_settings_hands_every_path_over_in_a_list_file(dll, items, tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    album, other = items
    clicked = [album, other, album / "01.flac"]
    exe, *arguments = command(dll, clicked, "musicdesk.settings")
    assert exe == EXE and arguments[:2] == ["--settings", "--list"] and len(arguments) == 3
    menu = tmp_path / "appdata" / "MusicDesk" / "menu"
    listed = Path(arguments[2])
    assert listed.parent == menu and listed.is_file()
    # MusicDesk's start reads it back, as written (UTF-8), and deletes it.
    request = parse_arguments(arguments, menu)
    assert request == StartRequest(SETTINGS, tuple(os.fspath(p) for p in clicked))
    assert not listed.exists()


def test_an_entry_the_selection_doesnt_have_starts_nothing(dll, items):
    album, other = items
    out = ctypes.create_unicode_buffer(256)
    array, count = paths_array([album, other])
    assert dll.MusicDeskMenu_Command(array, count, False, "musicdesk.open", EXE, out, len(out)) != 0
    assert out.value == ""
