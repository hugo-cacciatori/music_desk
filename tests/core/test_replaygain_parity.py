"""Parity of core/replaygain.py with the frozen music_tools.py, both running the real bundled rsgain
(AGENTS.md Ground rule 3; this is M5's "Done when", checked early).

Twin copies of the replaygain/ fixtures: the script runs in one (under contextlib.chdir, as it needs the cwd;
that's for the reference only, the app never changes directory), the app in the other. Then every file's tags
and every replaygain.log must match, with timestamps, clock times, durations and the twin folder masked.
Deliberate difference: ID3v2.3 files stay v2.3 in the app (the script upgrades them), so dumps compare frames.
"""

import contextlib
import re
import shutil

import pytest
from tagdump import tag_dump

from music_desk.core.formats import open_audio
from music_desk.core.replaygain import process_album, process_discography
from music_desk.paths import rsgain_path

ARTIST = "replaygain/Fixture Artist"


@pytest.fixture
def script(reference_music_tools, monkeypatch):
    ref = reference_music_tools
    monkeypatch.setattr(ref, "RSGAIN_PATH", str(rsgain_path()))
    monkeypatch.setattr(ref, "AUDIO_EXTENSIONS", {".flac", ".mp3"})
    return ref


def twins(fixtures_dir, tmp_path, relative):
    source = fixtures_dir / relative
    return [shutil.copytree(source, tmp_path / side / source.name) for side in ("script", "app")]


def mask(text: str, root) -> str:
    text = text.replace(str(root), "<ROOT>")
    text = re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", "<TIMESTAMP>", text)
    text = re.sub(r"\b\d{2}:\d{2}:\d{2}\b", "<TIME>", text)
    return re.sub(r"\d+\.\d s\b", "<N> s", text)


def compare_trees(script_root, app_root):
    script_files = sorted(p.relative_to(script_root) for p in script_root.rglob("*") if p.is_file())
    assert script_files == sorted(p.relative_to(app_root) for p in app_root.rglob("*") if p.is_file())
    logs = 0
    for relative in script_files:
        script_path, app_path = script_root / relative, app_root / relative
        if relative.suffix.lower() in (".flac", ".mp3"):
            assert tag_dump(open_audio(app_path)) == tag_dump(open_audio(script_path)), relative
        elif relative.name == "replaygain.log":
            script_text = mask(script_path.read_text(encoding="utf-8"), script_root)
            app_text = mask(app_path.read_text(encoding="utf-8"), app_root)
            assert app_text == script_text, relative
            logs += 1
    return logs


def test_album_parity(script, fixtures_dir, tmp_path):
    script_dir, app_dir = twins(fixtures_dir, tmp_path, f"{ARTIST}/Album A")
    with contextlib.chdir(script_dir):
        script_log = script.process_album()
    app_log = process_album(app_dir)

    assert app_log.result() == script_log.result() == "OK"
    assert app_log.album == script_log.album
    assert compare_trees(script_dir, app_dir) == 1
    assert "reduced to prevent clipping" in (app_dir / "replaygain.log").read_text(encoding="utf-8")


def test_discography_parity(script, fixtures_dir, tmp_path):
    script_dir, app_dir = twins(fixtures_dir, tmp_path, ARTIST)
    with contextlib.chdir(script_dir):
        script.process_discography()
    result = process_discography(app_dir)

    assert [album.result for album in result.albums] == ["OK", "OK"]
    assert compare_trees(script_dir, app_dir) == 3  # two album logs and the artist summary
    # The ID3v2.3 file is leveled too, and keeps its version in the app only.
    assert open_audio(app_dir / "Album B" / "02 Quiet.mp3").tags.version == (2, 3, 0)
    assert open_audio(script_dir / "Album B" / "02 Quiet.mp3").tags.version == (2, 4, 0)
