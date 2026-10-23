"""core/rsgain.scan: the album scan subprocess (SPEC §13)."""

import pytest

from music_desk.core.cancel import CancelToken
from music_desk.core.rsgain import MAX_COMMAND_LINE, scan
from music_desk.core.tags import TaggedFile
from music_desk.paths import rsgain_path

ALBUM_A = "replaygain/Fixture Artist/Album A"


def test_command_line_too_long_does_not_start_rsgain(tmp_path):
    names = [f"{i:05} {'x' * 200}.flac" for i in range(MAX_COMMAND_LINE // 200 + 1)]
    result = scan(names, tmp_path, "-15", rsgain_path())
    assert result.error == "command line too long"
    assert not result.ok


def test_missing_rsgain(tmp_path):
    result = scan(["a.flac"], tmp_path, "-15", tmp_path / "nope.exe")
    assert result.error == "not found"


@pytest.mark.slow
def test_scan_writes_tags_and_streams_lines(fixture_copy):
    folder = fixture_copy(ALBUM_A)
    lines = []
    result = scan(["01 Tone.flac", "02 Spike.flac"], folder, "-15", rsgain_path(), on_line=lines.append)
    assert result.ok, result.stderr
    assert lines and not any("\x1b" in line for line in lines)
    assert TaggedFile(folder / "02 Spike.flac").get_replaygain("replaygain_track_gain") == "4.11 dB"
    assert TaggedFile(folder / "03 Spike.mp3").get_replaygain("replaygain_track_gain") is None  # not listed


@pytest.mark.slow
def test_scan_failure_reports_the_exit_code(fixture_copy):
    folder = fixture_copy(ALBUM_A)
    result = scan(["missing.flac"], folder, "-15", rsgain_path())
    assert result.returncode != 0 and not result.ok


@pytest.mark.slow
def test_cancel_kills_rsgain(fixture_copy):
    folder = fixture_copy(ALBUM_A)
    token = CancelToken()
    token.cancel()
    result = scan(["01 Tone.flac", "02 Spike.flac", "03 Spike.mp3"], folder, "-15", rsgain_path(), cancel=token)
    assert result.cancelled and not result.ok
