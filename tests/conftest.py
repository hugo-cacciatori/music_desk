import importlib.util
import shutil
import urllib.request
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "generated"
REFERENCE = Path(__file__).parent / "reference"


# -- test selection by feature (tests/areas.py, SPEC §18 "Test selection") --

def pytest_addoption(parser):
    group = parser.getgroup("selection", "test selection by feature (tests/areas.py, SPEC §18)")
    group.addoption("--changed", action="store_true",
                    help="run the tests the files changed since HEAD (or --since) need, layer by layer")
    group.addoption("--since", default="HEAD", metavar="BASE", help="with --changed: compare with this commit")
    group.addoption("--with-callers", action="store_true",
                    help="with --changed: a backend change that alters what it gives its callers also runs its "
                         "areas' desktop tests")
    group.addoption("--area", action="append", default=[], metavar="NAME",
                    help="run every test of a feature area (repeatable); see tests/areas.py")


def pytest_configure(config):
    import areas

    config.selected_tests = None  # None: no selection, collect as usual
    config.selection_header = []
    names = config.getoption("area")
    if names:
        unknown = [n for n in names if n not in areas.AREAS]
        if unknown:
            raise pytest.UsageError(f"unknown area {', '.join(unknown)}; areas: {', '.join(areas.AREAS)}")
        config.selected_tests = areas.area_tests(names)
        config.selection_header = [f"areas: {', '.join(names)} ({len(config.selected_tests)} test files)"]
    elif config.getoption("changed"):
        changed = areas.changed_files(config.getoption("since"))
        selection = areas.select(changed, with_callers=config.getoption("with_callers"))
        header = [f"changed since {config.getoption('since')}: {', '.join(changed) or 'nothing'}"]
        if selection.everything:
            header += ["the whole suite, because:"] + [f"  {reason}" for reason in selection.everything]
        else:
            config.selected_tests = set(selection.tests)
            header += [f"{len(selection.tests)} test files:"]
            header += [f"  {test}: {'; '.join(why)}" for test, why in sorted(selection.tests.items())]
        config.selection_header = header


def pytest_report_header(config):
    return config.selection_header


def pytest_ignore_collect(collection_path, config):
    selected = config.selected_tests
    if selected is None or not collection_path.is_file() or not collection_path.name.startswith("test_"):
        return None
    try:
        rel = collection_path.relative_to(Path(__file__).parent).as_posix()
    except ValueError:
        return None
    return rel not in selected or None


def pytest_sessionfinish(session, exitstatus):
    if session.config.selected_tests == set() and exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED:
        session.exitstatus = pytest.ExitCode.OK  # the change needs no test: nothing ran, nothing failed


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


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """No test may reach LRCLIB (or anything else): urllib's opener fails the test. Lyrics tests inject a
    scripted opener (tests/core/lrclib_fake.py)."""

    def refuse(*_args, **_kwargs):
        raise AssertionError("a test tried to reach the network")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
