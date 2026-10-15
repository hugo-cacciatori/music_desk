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


@pytest.fixture(autouse=True)
def no_real_dialogs(monkeypatch):
    """A real modal dialog would wait for a click and stall the run. Tests answer the app's questions by replacing
    its confirm_* / ask_* / choose_* methods; any dialog that still opens fails the test at once."""
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    def refuse(name):
        def opened(*_args, **_kwargs):
            raise AssertionError(f"a real dialog opened during a test ({name}); answer it in the test instead")
        return opened

    for name in ("question", "information", "warning", "critical", "exec"):
        monkeypatch.setattr(QMessageBox, name, refuse(f"QMessageBox.{name}"))
    for name in ("getOpenFileName", "getOpenFileNames", "getSaveFileName", "getExistingDirectory"):
        monkeypatch.setattr(QFileDialog, name, refuse(f"QFileDialog.{name}"))
