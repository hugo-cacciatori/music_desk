"""Parity of core/rename.py with the frozen music_tools.py rename step (CLAUDE.md Ground rule 3, SPEC §17).

The script deletes junk with os.remove, so it never runs with junk for real here: renames are compared with its
junk list emptied, and junk lists with DRY_RUN on (which deletes nothing).
"""

import shutil

import pytest

from music_desk.core.rename import RenameOptions, Status, apply_plan, plan_renames

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


class Recorder:
    def __init__(self):
        self.paths = []

    def __call__(self, path):
        self.paths.append(path)


def test_album_rename_parity(script, fixtures_dir, tmp_path):
    script_dir, app_dir = twins(fixtures_dir, tmp_path, "album/Fixture Artist - Fixture Album")

    script_report = script.RenameReport()
    script.process_folder(script_dir, script_report)
    app_report = apply_plan(plan_renames(app_dir, RenameOptions(audio_extensions=SCRIPT_FORMATS)), recycle=Recorder())

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
    apply_plan(plan_renames(app_dir, RenameOptions(audio_extensions=SCRIPT_FORMATS)), recycle=Recorder())

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

    plan = plan_renames(folder, RenameOptions(cleanup=True, audio_extensions=SCRIPT_FORMATS))
    app_junk = {item.source.name for item in plan.with_status(Status.JUNK)}
    kept = {item.source.name: item.detail for item in plan.with_status(Status.KEPT)}
    sidecar_kept = {item.sidecar.source.name: item.sidecar.detail for item in plan.items
                    if item.sidecar is not None and item.sidecar.detail}

    deliberate = {"playlist.m3u", "Fixture Album.cue", "no_lyrics.lrc"}
    assert app_junk == set(script_report.junk_deleted) - deliberate
    assert deliberate <= set(script_report.junk_deleted)  # the script did treat them as junk
    assert kept["Fixture Album.cue"] == "needed to play Fixture Album.flac"
    assert sidecar_kept == {"no_lyrics.lrc": "lyrics not in the tags yet"}
