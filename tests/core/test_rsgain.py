import pytest

from music_desk.core.rsgain import rsgain_version, strip_ansi
from music_desk.paths import rsgain_path


def test_strip_ansi():
    assert strip_ansi("\x1b[1;32mrsgain\x1b[0m 3.6\n") == "rsgain 3.6"
    assert strip_ansi(None) == ""


def test_missing_rsgain_returns_none(tmp_path):
    assert rsgain_version(tmp_path / "nope.exe") is None


@pytest.mark.slow
def test_bundled_rsgain_version():
    version = rsgain_version(rsgain_path())
    assert version is not None
    assert version == "rsgain 3.6"
