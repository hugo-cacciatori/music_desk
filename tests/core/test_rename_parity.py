"""Parity of core/rename.py with the frozen music_tools.py rename step (AGENTS.md Ground rule 3, SPEC §17).

The script deletes junk with os.remove, so it never runs with junk for real here: renames are compared with its
junk list emptied, and junk lists with DRY_RUN on (which deletes nothing). Its junk cleanup is Clean up's now
(core/cleanup.py, SPEC §32): the junk parity test compares Clean up's default list with the script's.
"""

import shutil

import pytest

from refrain.core import cleanup
from refrain.core.rename import RenameOptions, apply_plan, plan_renames

SCRIPT_FORMATS = frozenset({".flac", ".mp3"})


@pytest.fixture
def script(reference_music_tools, monkeypatch):
    ref = reference_music_tools
    monkeypatch.setattr(ref, "RENAME_MASKS", ["{track:02} {title}", "{title}"])
    monkeypatch.setattr(ref, "RECURSIVE", True)
    monkeypatch.setattr(ref, "INVALID_CHAR_REPLACEMENT", "-")
    monkeypatch.setattr(ref, "AUDIO_EXTENSIONS", set(SCRIPT_FORMATS))
    monkeypatch.setattr(ref, "JUNK_EXTENSIONS", set())  # never let the script delete anything
    monkeypatch.setattr(ref, "DRY_RUN", False)
    return ref


def twins(fixtures_dir, tmp_path, relative):
    source = fixtures_dir / relative
    return [shutil.copytree(source, tmp_path / side / source.name) for side in ("script", "app")]


def file_names(folder, extensions=None) -> set[str]:
    return {p.relative_to(folder).as_posix() for p in folder.rglob("*")
            if p.is_file() and (extensions is None or p.suffix.lower() in extensions)}


def test_album_rename_parity(script, fixtures_dir, tmp_path):
    script_dir, app_dir = twins(fixtures_dir, tmp_path, "album/Fixture Artist - Fixture Album")

    script_report = script.RenameReport()
    script.process_folder(script_dir, script_report)
    app_report = apply_plan(plan_renames(app_dir, RenameOptions(audio_extensions=SCRIPT_FORMATS)))

    assert file_names(app_dir) == file_names(script_dir)
    assert app_report.scanned == script_report.scanned
    assert app_report.renamed == script_report.renamed
    assert app_report.fallback == script_report.fallback
    assert app_report.missing_tags == script_report.missing_tags
    assert app_report.name_taken == script_report.name_taken
    assert app_report.errors == script_report.errors == []
    assert script_report.renamed  # the comparison is not vacuous


def test_cleanup_rename_parity_on_audio_files(script, fixtures_dir, tmp_path):
    """Audio names match. Deliberate differences: the single-file album's audio is left alone (it is unchanged
    in the script too, by luck of its tags), and .lrc sidecars follow their audio file."""
    script_dir, app_dir = twins(fixtures_dir, tmp_path, "cleanup")
    script.process_folder(script_dir, script.RenameReport())
    apply_plan(plan_renames(app_dir, RenameOptions(audio_extensions=SCRIPT_FORMATS)))

    assert file_names(app_dir, SCRIPT_FORMATS) == file_names(script_dir, SCRIPT_FORMATS)
    assert file_names(script_dir, {".lrc"}) == {"lrc/no_lyrics.lrc", "lrc/with_lyrics.lrc"}
    assert file_names(app_dir, {".lrc"}) == {"lrc/02 No Lyrics.lrc", "lrc/01 With Lyrics.lrc"}


def test_junk_parity_with_deliberate_keeps(script, monkeypatch, fixtures_dir, tmp_path):
    folder = shutil.copytree(fixtures_dir / "cleanup", tmp_path / "cleanup")
    monkeypatch.setattr(script, "JUNK_EXTENSIONS", {".cue", ".m3u", ".nfo", ".sfv", ".txt", ".lrc"})  # its own
    monkeypatch.setattr(script, "DRY_RUN", True)
    script_report = script.RenameReport()
    script.process_folder(folder, script_report)
    assert file_names(folder) == file_names(fixtures_dir / "cleanup")  # dry run: nothing deleted

    items = cleanup.find_items(folder, cleanup.DEFAULT_OPTIONS)  # Clean up's defaults: junk only
    app_junk = {item.path.name for item in items if item.kept is None}
    kept = {item.path.name: item.kept for item in items if item.kept is not None}

    deliberate = {"playlist.m3u", "Fixture Album.cue"}  # an .lrc is junk again since M12, as in the script
    assert app_junk == set(script_report.junk_deleted) - deliberate
    assert deliberate <= set(script_report.junk_deleted)  # the script did treat them as junk
    assert kept == {"Fixture Album.cue": "kept: needed to play Fixture Album.flac"}


def test_summary_wording_matches_the_script(script, fixtures_dir, tmp_path, capsys):
    """The result's section titles are the script's print_summary lines (its list output aside)."""
    from refrain.core.rename import summary_sections

    script_dir, app_dir = twins(fixtures_dir, tmp_path, "album/Fixture Artist - Fixture Album")
    script_report = script.RenameReport()
    script.process_folder(script_dir, script_report)
    capsys.readouterr()
    script.print_summary(script_report)
    printed = [line for line in capsys.readouterr().out.splitlines() if line[:1].isdigit()]

    app_report = apply_plan(plan_renames(app_dir, RenameOptions(audio_extensions=SCRIPT_FORMATS)))
    titles = [title for title, _entries in summary_sections(app_report, dry_run=False)]
    assert titles == printed
    assert len(printed) >= 3  # renamed, fallback, missing tags or name taken: not vacuous
