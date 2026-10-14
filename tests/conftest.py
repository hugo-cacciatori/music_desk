import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "generated"


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
