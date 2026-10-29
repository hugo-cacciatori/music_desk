"""context_menu/entries.py: the submenu's entries, labels and help texts for a selection (SPEC §31, §35), as File
Explorer's handler shows them (tests/test_menu_handler.py compares the two)."""

import re
from pathlib import Path

import pytest

from music_desk.context_menu.entries import HELP, OPEN, Selection, entry_names, target_text
from music_desk.context_menu.entries import menu_entries as entries_for

ONE_FOLDER = ["open", "replaygain-album", "replaygain-discography", "rename", "tidy", "cleanup", "settings"]
MENU_CPP = Path(__file__).parents[1] / "native" / "context_menu" / "menu.cpp"


@pytest.fixture
def items(tmp_path):
    album, other = tmp_path / "Café 日本", tmp_path / "Other"
    for folder in (album, other):
        folder.mkdir()
    for name in ("01.flac", "02.MP3", "03.aac", "notes.txt", "image.CUE"):
        (album / name).write_bytes(b"")
    (other / "01.opus").write_bytes(b"")
    return album, other


def names(paths, background=False) -> list[str]:
    return entry_names(Selection.of(paths, background))


def labels(paths, background=False, target=-15.0) -> list[str]:
    return [e.label for e in entries_for(Selection.of(paths, background), target)]


def test_the_entries_by_what_was_clicked(items):
    album, other = items
    assert names([album], background=True) == ONE_FOLDER  # empty space: the open folder
    assert names([album]) == ONE_FOLDER
    assert names([album, other]) == ["replaygain-albums", "rename", "tidy", "cleanup", "settings"]
    assert names([album / "01.flac"]) == ["replaygain-loose", "rename", "remove-replaygain", "trim", "settings"]
    assert names([album / "02.MP3"]) == ["replaygain-loose", "rename", "remove-replaygain", "settings"]
    assert names([album / "image.CUE"]) == ["split", "edit-cue", "settings"]  # a sheet alone
    assert names([album / "image.CUE", album / "01.flac"]) == ["replaygain-loose", "rename", "settings"]
    assert names([album / "01.flac", album / "02.MP3", album / "notes.txt"]) == [
        "replaygain-tracks", "replaygain-loose", "rename", "settings"]  # Remove: only when every item is a track
    assert names([album / "01.flac", other / "01.opus"]) == [  # two folders: no album
        "replaygain-loose", "rename", "remove-replaygain", "settings"]
    assert names([album / "03.aac", album / "notes.txt"]) == ["rename", "settings"]  # nothing rsgain scans
    assert names([album, album / "01.flac"]) == ["rename", "settings"]  # folders and files together
    assert names([album / "notes.txt"]) == ["settings"]  # no folder, no music file


def test_the_labels_carry_what_they_act_on_and_the_target(items):
    album, other = items
    assert labels([album]) == ["Open in Refrain", "ReplayGain as an album (−15 LUFS)",
                               "ReplayGain as a discography (−15 LUFS)", "Rename from tags…", "Tidy this folder…",
                               "Clean up this folder…", "Settings"]
    assert labels([album, other])[2:4] == ["Tidy 2 folders…", "Clean up 2 folders…"]
    assert labels([album, other], target=-18.5)[0] == "ReplayGain: 2 folders, each as an album (−18.5 LUFS)"
    assert labels([album / "01.flac"])[0] == "ReplayGain this track (−15 LUFS)"
    assert labels([album / "01.flac", album / "02.MP3"])[:2] == [
        "ReplayGain: these 2 tracks as an album (−15 LUFS)", "ReplayGain: each of these 2 tracks on its own (−15 LUFS)"]
    assert labels([album / "image.CUE"]) == ["Split this cue sheet…", "Edit this cue sheet…", "Settings"]
    assert target_text(-15) == "−15 LUFS" and target_text(-18.5) == "−18.5 LUFS" and target_text(3) == "3 LUFS"


def test_the_app_can_tell_folders_without_the_disk(items):
    """The app's own submenu asks its index, not the disk (SPEC §35)."""
    album, _other = items
    selection = Selection.of([album / "Gone"], is_dir=lambda path: True)
    assert entry_names(selection) == ONE_FOLDER


def test_every_entry_has_the_handlers_help_text():
    """menu.cpp's Entry help texts (adjacent literals joined) are these, verb by verb."""
    source = MENU_CPP.read_text(encoding="utf-8")
    found = {}
    for verb, body in re.findall(r'static const Entry \w+ = \{L"refrain\.([\w-]+)",\s*((?:L"[^"]*"\s*)+)\};',
                                 source):
        found[verb] = "".join(re.findall(r'L"([^"]*)"', body))
    assert found == HELP and OPEN in found

