"""core/inline_rename.py: validation, renames with the .lrc sidecar, case-only renames, locks and Undo."""

import shutil

import pytest

from refrain.core.inline_rename import RenameError, rename_entry, sidecar_for, undo, validate_name
from refrain.core.path_locks import PathLocks


@pytest.fixture
def album(tmp_path, fixtures_dir):
    folder = tmp_path / "Album"
    folder.mkdir()
    shutil.copy2(fixtures_dir / "formats" / "tone.flac", folder / "01 Song.flac")
    (folder / "01 Song.lrc").write_text("[00:01.00]la")
    shutil.copy2(fixtures_dir / "formats" / "tone.mp3", folder / "02 Other.mp3")
    (folder / "notes.txt").write_text("x")
    return folder


def names(folder) -> set[str]:
    return {p.name for p in folder.iterdir()}


@pytest.mark.parametrize(("new", "problem"), [
    ("", "can't be empty"),
    ("   ", "can't be empty"),
    ("a?b.flac", "can't contain"),
    ("50%.flac", "can't contain"),  # forbidden in the script's names too (SPEC §17)
    ("tab\there.flac", "can't contain"),
    ("song.flac.", "end with a space or a dot"),
    ("song ", "end with a space or a dot"),
    ("CON.flac", "Windows reserves"),
    ("com1", "Windows reserves"),
    ("02 Other.mp3", "already exists"),
    ("02 OTHER.MP3", "already exists"),
    ("x" * 300 + ".flac", "longer than"),
])
def test_validation(album, new, problem):
    assert problem in validate_name(album / "01 Song.flac", new)


def test_valid_names(album):
    for new in ("01 Song (live).flac", "01 SONG.flac", "Concerto.flac", "ok"):
        assert validate_name(album / "01 Song.flac", new) is None


def test_rename_moves_the_sidecar(album):
    result = rename_entry(album / "01 Song.flac", "01 Intro.flac")
    assert names(album) == {"01 Intro.flac", "01 Intro.lrc", "02 Other.mp3", "notes.txt"}
    assert result.steps == [(album / "01 Song.flac", album / "01 Intro.flac"),
                            (album / "01 Song.lrc", album / "01 Intro.lrc")]
    assert result.sidecar == (album / "01 Song.lrc", album / "01 Intro.lrc")
    back = undo(result)
    assert names(album) == {"01 Song.flac", "01 Song.lrc", "02 Other.mp3", "notes.txt"}
    assert {old.name for old, _new in back.steps} == {"01 Intro.lrc", "01 Intro.flac"}


def test_sidecar_stays_when_two_audio_files_share_the_stem(album, fixtures_dir):
    shutil.copy2(fixtures_dir / "formats" / "tone.mp3", album / "01 Song.mp3")
    assert sidecar_for(album / "01 Song.flac") is None
    rename_entry(album / "01 Song.flac", "01 Intro.flac")
    assert "01 Song.lrc" in names(album)


def test_sidecar_target_taken(album):
    (album / "01 Intro.lrc").write_text("other")
    result = rename_entry(album / "01 Song.flac", "01 Intro.flac")
    assert "01 Song.lrc" in names(album) and "wasn't renamed" in result.warnings[0]


def test_case_only_rename(album):
    result = rename_entry(album / "01 Song.flac", "01 SONG.flac")
    assert "01 SONG.flac" in names(album) and "01 SONG.lrc" in names(album)
    undo(result)
    assert "01 Song.flac" in names(album) and "01 Song.lrc" in names(album)


def test_extension_change_keeps_the_sidecar_stem(album):
    rename_entry(album / "notes.txt", "notes.nfo")
    assert "notes.nfo" in names(album)
    result = rename_entry(album / "01 Song.flac", "01 Song.FLAC")
    assert {n for n in names(album) if n.startswith("01 Song")} == {"01 Song.FLAC", "01 Song.lrc"}
    assert result.sidecar is None  # same stem: the .lrc needs no rename


def test_folder_rename(album):
    result = rename_entry(album, "Album (2024)")
    assert (album.parent / "Album (2024)" / "01 Song.flac").exists()
    undo(result)
    assert (album / "01 Song.flac").exists()


def test_nothing_happens_on_errors(album):
    with pytest.raises(RenameError, match="already exists"):
        rename_entry(album / "01 Song.flac", "02 Other.mp3")
    with pytest.raises(RenameError, match="isn't there"):
        rename_entry(album / "missing.flac", "x.flac")
    assert rename_entry(album / "01 Song.flac", "01 Song.flac").steps == []
    assert names(album) == {"01 Song.flac", "01 Song.lrc", "02 Other.mp3", "notes.txt"}


def test_locks(album):
    locks = PathLocks()
    with locks.lock([album], "ReplayGain (album)"), pytest.raises(RenameError, match="while ReplayGain"):
        rename_entry(album / "01 Song.flac", "x.flac", locks)
    with locks.lock([album / "01 Song.flac"], "a tag save"), pytest.raises(RenameError, match="a tag save"):
        rename_entry(album, "Renamed", locks)  # a folder with something locked inside
    rename_entry(album / "01 Song.flac", "x.flac", locks)
    assert locks.held() == []


def test_undo_when_the_name_was_taken_meanwhile(album):
    result = rename_entry(album / "02 Other.mp3", "02 New.mp3")
    (album / "02 Other.mp3").write_text("someone else's file")
    with pytest.raises(RenameError, match="Undo failed"):
        undo(result)
    assert "02 New.mp3" in names(album)
