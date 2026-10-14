"""SPEC §1: the ReplayGain keys the bundled rsgain writes per format, verified on the real binary.

core/tags.py relies on these keys; if an rsgain update changes them, this test says where.
"""

import re
import subprocess

import pytest

from music_desk.core.formats import format_for, open_audio
from music_desk.core.rsgain import strip_ansi
from music_desk.paths import rsgain_path

pytestmark = pytest.mark.slow

# Fixture -> (gain key, peak key), as mutagen exposes them. Vorbis comment keys match ignoring case.
EXPECTED_KEYS = {
    "tone.flac": ("REPLAYGAIN_TRACK_GAIN", "REPLAYGAIN_TRACK_PEAK"),
    "tone.ogg": ("REPLAYGAIN_TRACK_GAIN", "REPLAYGAIN_TRACK_PEAK"),
    "tone.oga": ("REPLAYGAIN_TRACK_GAIN", "REPLAYGAIN_TRACK_PEAK"),
    "tone.opus": ("REPLAYGAIN_TRACK_GAIN", "REPLAYGAIN_TRACK_PEAK"),
    "tone.mp3": ("TXXX:REPLAYGAIN_TRACK_GAIN", "TXXX:REPLAYGAIN_TRACK_PEAK"),
    "tone.mp2": ("TXXX:REPLAYGAIN_TRACK_GAIN", "TXXX:REPLAYGAIN_TRACK_PEAK"),
    "tone.wav": ("TXXX:REPLAYGAIN_TRACK_GAIN", "TXXX:REPLAYGAIN_TRACK_PEAK"),
    "tone.aiff": ("TXXX:REPLAYGAIN_TRACK_GAIN", "TXXX:REPLAYGAIN_TRACK_PEAK"),
    "tone_aac.m4a": ("----:com.apple.iTunes:REPLAYGAIN_TRACK_GAIN", "----:com.apple.iTunes:REPLAYGAIN_TRACK_PEAK"),
    "tone_alac.m4a": ("----:com.apple.iTunes:REPLAYGAIN_TRACK_GAIN", "----:com.apple.iTunes:REPLAYGAIN_TRACK_PEAK"),
    "tone.wv": ("REPLAYGAIN_TRACK_GAIN", "REPLAYGAIN_TRACK_PEAK"),
    "tone.wma": ("REPLAYGAIN_TRACK_GAIN", "REPLAYGAIN_TRACK_PEAK"),
}
UNSUPPORTED = ["tone.m4b", "tone.tta", "tone.aac"]


def run_rsgain(path):
    """A track scan as the script runs it (custom mode, write tags, -15 LUFS), from the file's folder."""
    return subprocess.run(
        [str(rsgain_path()), "custom", "-s", "i", "-l", "-15", path.name],
        cwd=path.parent, capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW, timeout=60,
    )


def value(tags, key) -> str:
    if hasattr(tags, "getall") and key.startswith("TXXX:"):  # ID3
        return str(tags[key])
    raw = tags[key][0]
    return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)


@pytest.mark.parametrize("name", sorted(EXPECTED_KEYS))
def test_rsgain_writes_expected_keys(fixture_copy, name):
    path = fixture_copy(f"formats/{name}")
    assert format_for(path).replaygain
    result = run_rsgain(path)
    assert result.returncode == 0, strip_ansi(result.stderr)

    tags = open_audio(path).tags
    gain_key, peak_key = EXPECTED_KEYS[name]
    assert re.fullmatch(r"-?\d+\.\d\d dB", value(tags, gain_key))
    assert re.fullmatch(r"\d\.\d{6}", value(tags, peak_key))


@pytest.mark.parametrize("name", UNSUPPORTED)
def test_rsgain_rejects_formats_marked_unsupported(fixture_copy, name):
    path = fixture_copy(f"formats/{name}")
    assert not format_for(path).replaygain
    result = run_rsgain(path)
    assert result.returncode != 0
    assert "not of a supported type" in strip_ansi(result.stdout + result.stderr)
