import importlib.util
import os
import shutil
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

# No window on Hugo's screen during a run (Hugo, 2026-10-23): Qt draws off screen. Before any QApplication exists;
# QT_QPA_PLATFORM=windows in the environment brings the real windows back, to watch a test.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FIXTURES = Path(__file__).parent / "fixtures" / "generated"
REFERENCE = Path(__file__).parent / "reference"


# -- test selection by feature (tests/areas.py, SPEC §18 "Test selection") --

STARTED = time.time()  # this conftest loads as the run starts: the run's time includes its start-up
RUN_LOG = Path(tempfile.gettempdir()) / "refrain-test-runs.log"
RECENT_S = 30 * 60


def pytest_addoption(parser):
    group = parser.getgroup("selection", "test selection by feature (tests/areas.py, SPEC §18)")
    group.addoption("--changed", action="store_true",
                    help="run the tests the files changed since HEAD (or --since) need, layer by layer; the "
                         "default when no test path, area or -m is given")
    group.addoption("--all", action="store_true", help="run the whole suite (AGENTS.md \"Test stages\" says when)")
    group.addoption("--since", default="HEAD", metavar="BASE", help="with --changed: compare with this commit")
    group.addoption("--with-callers", action="store_true",
                    help="with --changed: a backend change that alters what it gives its callers also runs its "
                         "areas' desktop tests")
    group.addoption("--area", action="append", default=[], metavar="NAME",
                    help="run every test of a feature area (repeatable); see tests/areas.py")


def _plan(config):
    """What this run tests: (selected test files or None for everything, header lines, label, file count). Worked
    out once, early enough for the worker count below."""
    plan = getattr(config, "_test_plan", None)
    if plan is not None:
        return plan
    import areas

    names = config.getoption("area")
    explicit = config.option.file_or_dir or config.option.markexpr != "not perf"  # paths, or a -m of one's own
    everything = len(areas.all_test_files())
    selected, header = None, []
    if config.getoption("all"):
        label, count = "the whole suite (--all)", everything
    elif names:
        unknown = [n for n in names if n not in areas.AREAS]
        if unknown:
            raise pytest.UsageError(f"unknown area {', '.join(unknown)}; areas: {', '.join(areas.AREAS)}")
        selected = areas.area_tests(names)
        label, count = f"--area {' '.join(names)}", len(selected)
        header = [f"areas: {', '.join(names)} ({len(selected)} test files)"]
    elif config.getoption("changed") or not explicit:
        # Hugo, 2026-10-23: a bare `pytest` runs what the changes need; the whole suite takes --all.
        changed = areas.changed_files(config.getoption("since"))
        selection = areas.select(changed, with_callers=config.getoption("with_callers"))
        header = [f"changed since {config.getoption('since')}: {', '.join(changed) or 'nothing'}"]
        if selection.everything:
            label, count = "--changed: the whole suite", everything
            header += ["the whole suite, because:"] + [f"  {reason}" for reason in selection.everything]
        else:
            selected = set(selection.tests)
            label, count = f"--changed: {len(selected)} test files", len(selected)
            header += [f"{len(selected)} test files:"]
            header += [f"  {test}: {'; '.join(why)}" for test, why in sorted(selection.tests.items())]
            if not selected:
                header += ["nothing to test (--all runs the whole suite)"]
    else:
        label = " ".join(config.option.file_or_dir) or f"-m {config.option.markexpr}"
        count = 0
        for arg in config.option.file_or_dir:
            path = Path(arg.split("::")[0])
            count += len(list(path.rglob("test_*.py"))) if path.is_dir() else 1
    plan = config._test_plan = (selected, header, label, count)
    return plan


def pytest_xdist_auto_num_workers(config):
    """`-n auto` (the default, pyproject.toml): workers by the size of the run (measured 2026-10-23 on this PC's
    32 threads: the whole suite takes 156 s alone, 32 s on 8 workers, 27 s on 16). A few files run in this
    process, since each worker spends about 3 s starting; the performance measurements always run alone."""
    if config.option.markexpr not in ("not perf", ""):
        return 0
    count = _plan(config)[3]
    return 0 if count <= 3 else min(12, max(2, count // 4))


def pytest_configure(config):
    config.selected_tests, config.selection_header, config.run_label, _count = _plan(config)


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


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """This run's time, start-up included, and every run of the last 30 minutes (AGENTS.md "Test stages"): the
    test time of a whole turn of work, not one run's."""
    now = time.time()
    entry = f"{now:.0f}\t{now - STARTED:.0f}\t{config.run_label}\n"
    try:
        with RUN_LOG.open("a", encoding="utf-8") as log:
            log.write(entry)
        rows = [line.split("\t", 2) for line in RUN_LOG.read_text(encoding="utf-8").splitlines()]
    except OSError:
        return
    recent = [(float(end), float(secs), label) for end, secs, label in (r for r in rows if len(r) == 3)
              if now - float(end) < RECENT_S]
    total = sum(secs for _end, secs, _label in recent)
    write = terminalreporter.write_line
    write(f"this run: {now - STARTED:.0f} s ({config.run_label})")
    write(f"test runs in the last 30 minutes: {len(recent)}, {total // 60:.0f} min {total % 60:.0f} s in all")
    for end, secs, label in recent[:-1]:
        write(f"  {time.strftime('%H:%M', time.localtime(end))}  {secs:5.0f} s  {label}")


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
