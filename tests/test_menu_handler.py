"""File Explorer's Refrain submenu, the handler DLL (SPEC §31): built from native/context_menu/, then driven
through its test exports, which make and initialize the real COM object as Explorer does. Explorer itself showing
the menu is a manual check (the installer registers the handler)."""

import ctypes
import importlib.util
import json
import os
import re
from ctypes import wintypes
from pathlib import Path

import pytest

from refrain.context_menu.entries import Selection, menu_entries
from refrain.context_menu.request import (
    CLEANUP,
    EDIT_CUE,
    REMOVE_REPLAYGAIN,
    RENAME,
    REPLAYGAIN_DISCOGRAPHY,
    REPLAYGAIN_LOOSE,
    REPLAYGAIN_TRACKS,
    SETTINGS,
    SPLIT,
    TIDY,
    TRIM,
    StartRequest,
    parse_arguments,
)
from refrain.core import cue
from refrain.core.formats import FORMATS

# packaging/ is a folder of scripts, not a package (and `packaging` is a library's name): load the build script.
_spec = importlib.util.spec_from_file_location("build_menu", Path(__file__).parents[1] / "packaging" / "build_menu.py")
assert _spec is not None and _spec.loader is not None
build_menu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_menu)

EXE = r"C:\Program Files\Refrain\Refrain.exe"
SETTINGS_ONLY = ["settings"]
ONE_FOLDER = ["open", "replaygain-album", "replaygain-discography", "rename", "tidy", "cleanup", "settings"]


@pytest.fixture(scope="module")
def dll(tmp_path_factory):
    if build_menu.find_vcvars() is None:
        pytest.skip("no C++ compiler (Visual Studio Build Tools) on this PC")
    library = ctypes.WinDLL(str(build_menu.build(tmp_path_factory.mktemp("menu"))))
    for name, extra in (("RefrainMenu_Entries", []),
                        ("RefrainMenu_Command", [wintypes.LPCWSTR, wintypes.LPCWSTR]),
                        ("RefrainMenu_Place", [wintypes.LPCWSTR])):
        function = getattr(library, name)
        function.restype = ctypes.c_long
        function.argtypes = [ctypes.POINTER(wintypes.LPCWSTR), ctypes.c_int, wintypes.BOOL, *extra,
                             wintypes.LPWSTR, ctypes.c_int]
    ctypes.windll.ole32.CoInitialize(None)  # the shell's objects, as on Explorer's thread
    return library


def paths_array(paths):
    return (wintypes.LPCWSTR * len(paths))(*[str(p) for p in paths]), len(paths)


def submenu(dll, paths, background=False) -> list[tuple[str, str]]:
    """The submenu's (verb, label) pairs, in order."""
    out = ctypes.create_unicode_buffer(4096)
    array, count = paths_array(paths)
    result = dll.RefrainMenu_Entries(array, count, background, out, len(out))
    assert result == 0, hex(result & 0xFFFFFFFF)
    title, *lines = out.value.split("\n")
    assert title == "Refrain:"
    return [tuple(line.split("\t")) for line in lines]


def entries(dll, paths, background=False) -> list[str]:
    """The submenu's verbs, without "refrain."."""
    return [verb.removeprefix("refrain.") for verb, _label in submenu(dll, paths, background)]


def labels(dll, paths, background=False) -> list[str]:
    return [label for _verb, label in submenu(dll, paths, background)]


def command(dll, paths, verb, background=False) -> list[str]:
    """The started command line, split as Refrain's own start splits it."""
    out = ctypes.create_unicode_buffer(4096)
    array, count = paths_array(paths)
    result = dll.RefrainMenu_Command(array, count, background, verb, EXE, out, len(out))
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
    for name in ("01.flac", "02.MP3", "03.aac", "notes.txt", "image.CUE"):
        (album / name).write_bytes(b"")
    (other / "01.opus").write_bytes(b"")
    return album, other


@pytest.fixture
def appdata(tmp_path, monkeypatch):
    """%LOCALAPPDATA%: the list files go there, and the labels' target comes from its settings.json."""
    folder = tmp_path / "appdata"
    monkeypatch.setenv("LOCALAPPDATA", str(folder))
    return folder


def test_the_entries_by_what_was_clicked(dll, items, appdata):
    album, other = items
    assert entries(dll, [album], background=True) == ONE_FOLDER  # empty space: the open folder
    assert entries(dll, [album]) == ONE_FOLDER
    assert entries(dll, [album, other]) == ["replaygain-albums", "rename", "tidy", "cleanup", "settings"]
    assert entries(dll, [album / "01.flac"]) == ["replaygain-loose", "rename", "remove-replaygain", "trim",
                                                 "settings"]
    assert entries(dll, [album / "02.MP3"]) == ["replaygain-loose", "rename", "remove-replaygain", "settings"]
    assert entries(dll, [album / "image.CUE"]) == ["split", "edit-cue", "settings"]  # a sheet alone
    assert entries(dll, [album / "image.CUE", album / "01.flac"]) == [  # with a track: neither Split nor Trim
        "replaygain-loose", "rename", "settings"]
    assert entries(dll, [album / "01.flac", album / "02.MP3", album / "notes.txt"]) == [
        "replaygain-tracks", "replaygain-loose", "rename", "settings"]  # Remove: only when every item is a track
    assert entries(dll, [album / "01.flac", other / "01.opus"]) == [  # two folders: no album
        "replaygain-loose", "rename", "remove-replaygain", "settings"]
    assert entries(dll, [album / "03.aac", album / "notes.txt"]) == ["rename", "settings"]  # nothing rsgain scans
    assert entries(dll, [album, album / "01.flac"]) == ["rename", "settings"]  # folders and files together
    assert entries(dll, [album / "notes.txt"]) == SETTINGS_ONLY  # no folder, no music file


def test_the_labels_carry_the_target(dll, items, appdata):
    album, other = items
    assert labels(dll, [album]) == ["Open in Refrain", "ReplayGain as an album (−15 LUFS)",
                                    "ReplayGain as a discography (−15 LUFS)", "Rename from tags…",
                                    "Tidy this folder…", "Clean up this folder…", "Settings"]  # the default
    assert labels(dll, [album], background=True)[-3:-1] == ["Tidy this folder…", "Clean up this folder…"]
    assert labels(dll, [album, other])[2:4] == ["Tidy 2 folders…", "Clean up 2 folders…"]
    assert labels(dll, [album / "01.flac"])[-3:-1] == ["Remove ReplayGain tags…", "Trim this track…"]
    assert labels(dll, [album / "image.CUE"]) == ["Split this cue sheet…", "Edit this cue sheet…", "Settings"]
    (appdata / "Refrain").mkdir(parents=True)
    settings = appdata / "Refrain" / "settings.json"
    settings.write_text(json.dumps({"name_masks": {"x": "{target}"}, "replaygain_options": {
        "comment": "LUFS Target : {target}", "subfolder_keywords": ["lite"], "target": -18.5}}, indent=2),
        encoding="utf-8")
    assert labels(dll, [album, other])[0] == "ReplayGain: 2 folders, each as an album (−18.5 LUFS)"
    assert labels(dll, [album / "01.flac"])[0] == "ReplayGain this track (−18.5 LUFS)"
    assert labels(dll, [album / "01.flac", album / "02.MP3", album / "notes.txt"])[:2] == [
        "ReplayGain: these 2 tracks as an album (−18.5 LUFS)",
        "ReplayGain: each of these 2 tracks on its own (−18.5 LUFS)"]
    settings.write_text(json.dumps({"replaygain_options": {"target": "loud"}}), encoding="utf-8")
    assert labels(dll, [album / "01.flac"])[0] == "ReplayGain this track (−15 LUFS)"  # as the app: the default


def test_the_extensions_are_the_apps():
    """The handler's lists stay in step with the app: rsgain's formats and every audio format (core/formats.py),
    and what Trim cuts (core/cue.py)."""
    source = (Path(__file__).parents[1] / "native" / "context_menu" / "menu.cpp").read_text(encoding="utf-8")
    for name, extensions in (("REPLAYGAIN_EXTENSIONS", [e for f in FORMATS if f.replaygain for e in f.extensions]),
                             ("AUDIO_EXTENSIONS", [e for f in FORMATS for e in f.extensions]),
                             ("TRIM_EXTENSIONS", list(cue.SUPPORTED))):
        block = source.split(f"{name}[] = {{", 1)[1].split("};", 1)[0]
        assert re.findall(r'L"(\.[a-z0-9]+)"', block) == extensions, name


def test_the_other_entries_hand_their_paths_over(dll, items, appdata):
    album, other = items
    for verb, entry, clicked in (("refrain.rename", RENAME, [album, other / "01.opus", album / "notes.txt"]),
                                 ("refrain.remove-replaygain", REMOVE_REPLAYGAIN, [album / "01.flac"]),
                                 ("refrain.tidy", TIDY, [album, other]),
                                 ("refrain.cleanup", CLEANUP, [album, other]),
                                 ("refrain.split", SPLIT, [album / "image.CUE"]),
                                 ("refrain.edit-cue", EDIT_CUE, [album / "image.CUE"]),
                                 ("refrain.trim", TRIM, [album / "01.flac"])):
        exe, *arguments = command(dll, clicked, verb)
        assert exe == EXE and arguments[:2] == [f"--{entry}", "--list"]
        request = parse_arguments(arguments, appdata / "Refrain" / "menu")
        assert request == StartRequest(entry, tuple(os.fspath(p) for p in clicked))
    exe, *arguments = command(dll, [album], "refrain.rename", background=True)
    assert parse_arguments(arguments, appdata / "Refrain" / "menu") == StartRequest(RENAME, (os.fspath(album),))


def test_replaygain_entries_hand_their_paths_over(dll, items, appdata):
    album, _other = items
    clicked = [album / "01.flac", album / "02.MP3", album / "notes.txt"]
    for verb, entry in (("refrain.replaygain-tracks", REPLAYGAIN_TRACKS),
                        ("refrain.replaygain-loose", REPLAYGAIN_LOOSE)):
        exe, *arguments = command(dll, clicked, verb)
        assert exe == EXE and arguments[:2] == [f"--{entry}", "--list"]
        request = parse_arguments(arguments, appdata / "Refrain" / "menu")
        assert request == StartRequest(entry, tuple(os.fspath(p) for p in clicked))  # the app skips notes.txt
    exe, *arguments = command(dll, [album], "refrain.replaygain-discography", background=True)
    request = parse_arguments(arguments, appdata / "Refrain" / "menu")
    assert request == StartRequest(REPLAYGAIN_DISCOGRAPHY, (os.fspath(album),))


def placed(dll, paths, existing, background=False) -> list[str]:
    """Explorer's menu after the handler adds to one already holding `existing` (labels; "Refrain": a submenu)."""
    out = ctypes.create_unicode_buffer(4096)
    array, count = paths_array(paths)
    result = dll.RefrainMenu_Place(array, count, background, "\n".join(existing), out, len(out))
    assert result == 0, hex(result & 0xFFFFFFFF)
    return out.value.split("\n")


def test_the_submenu_goes_just_below_7_zip(dll, items, appdata):
    """Hugo, 2026-10-24: "just below 7-Zip", wherever Explorer asks the handler to insert."""
    album, _other = items
    menu = ["Open", "Copy as path", "7-&Zip", "CRC SHA", "Send to"]
    assert placed(dll, [album], menu) == ["Open", "Copy as path", "7-Zip", "Refrain", "CRC SHA", "Send to"]
    # No 7-Zip (empty space has none): where Explorer asks (here after the first item).
    assert placed(dll, [album], ["View", "Sort by"], background=True) == ["View", "Refrain", "Sort by"]


def test_the_submenu_shows_once(dll, items, appdata):
    """Explorer's navigation pane asked the handler twice for one folder: two submenus (Hugo, 2026-10-24)."""
    album, _other = items
    menu = ["Open", "7-Zip", "Refrain", "Send to"]
    assert placed(dll, [album], menu) == menu


def test_open_starts_refrain_on_the_folder(dll, items):
    album, _other = items
    assert command(dll, [album], "refrain.open") == [EXE, str(album)]
    assert command(dll, [album], "refrain.open", background=True) == [EXE, str(album)]
    root = Path(album.anchor)  # "C:\": its trailing backslash must not escape the closing quote
    assert command(dll, [root], "refrain.open", background=True) == [EXE, str(root)]


def test_settings_hands_every_path_over_in_a_list_file(dll, items, tmp_path, appdata):
    album, other = items
    clicked = [album, other, album / "01.flac"]
    exe, *arguments = command(dll, clicked, "refrain.settings")
    assert exe == EXE and arguments[:2] == ["--settings", "--list"] and len(arguments) == 3
    menu = tmp_path / "appdata" / "Refrain" / "menu"
    listed = Path(arguments[2])
    assert listed.parent == menu and listed.is_file()
    # Refrain's start reads it back, as written (UTF-8), and deletes it.
    request = parse_arguments(arguments, menu)
    assert request == StartRequest(SETTINGS, tuple(os.fspath(p) for p in clicked))
    assert not listed.exists()


def test_an_entry_the_selection_doesnt_have_starts_nothing(dll, items):
    album, other = items
    out = ctypes.create_unicode_buffer(256)
    array, count = paths_array([album, other])
    assert dll.RefrainMenu_Command(array, count, False, "refrain.open", EXE, out, len(out)) != 0
    assert out.value == ""


def test_the_apps_entry_list_is_the_handlers(dll, items, appdata):
    """One list in Python (context_menu/entries.py, the app's own submenu and toolbar, SPEC §35) and the handler's
    in C++: the same entries, order and labels for every kind of selection, with the default target and another."""
    album, other = items
    selections = [([album], True), ([album], False), ([album, other], False), ([album / "01.flac"], False),
                  ([album / "02.MP3"], False), ([album / "image.CUE"], False),
                  ([album / "image.CUE", album / "01.flac"], False),
                  ([album / "01.flac", album / "02.MP3", album / "notes.txt"], False),
                  ([album / "01.flac", other / "01.opus"], False), ([album / "03.aac", album / "notes.txt"], False),
                  ([album, album / "01.flac"], False), ([album / "notes.txt"], False),
                  ([album, other, album / "01.flac"], False)]
    for target in (None, -18.5):
        if target is not None:
            (appdata / "Refrain").mkdir(parents=True, exist_ok=True)
            (appdata / "Refrain" / "settings.json").write_text(
                json.dumps({"replaygain_options": {"target": target}}), encoding="utf-8")
        for paths, background in selections:
            ours = menu_entries(Selection.of(paths, background), -15.0 if target is None else target)
            assert submenu(dll, paths, background) == [(f"refrain.{e.entry}", e.label) for e in ours], paths
