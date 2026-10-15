import importlib.util
import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "generated"
REFERENCE = Path(__file__).parent / "reference"


@pytest.fixture(scope="session")
def reference_music_tools():
    """The frozen music_tools.py, imported as a module (it has no side effects at import)."""
    spec = importlib.util.spec_from_file_location("reference_music_tools", REFERENCE / "music_tools.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def reference_seek_lyrics():
    """The frozen seek_lyrics.py, imported as a module (it has no side effects at import)."""
    spec = importlib.util.spec_from_file_location("reference_seek_lyrics", REFERENCE / "seek_lyrics.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fixtures_dir() -> Path:
    """The committed fixtures. Read-only: use `fixture_copy` for anything that writes."""
    return FIXTURES


@pytest.fixture
def fixture_copy(tmp_path):
    """Copy a fixture file or folder (relative to tests/fixtures/generated) into tmp_path and return the copy."""

    def copy(relative: str) -> Path:
        source = FIXTURES / relative
        target = tmp_path / source.name
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
        return target

    return copy
