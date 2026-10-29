"""`Refrain.exe --self-test [report]`: the release gate (AGENTS.md rule 9, SPEC §13 "Release gate").

The built app checks itself before an installer is made from it: run from a copy of dist/Refrain/ outside the
repository, it proves the bundle stands alone. Its resources are found next to the executable, every bundled file is
there, rsgain and flac run, every module imports, SQLite has the trigram FTS5 the index needs, and Qt builds the main
window. No window is shown, no other Refrain is contacted and no app data is read; the exit code is 0 when every
check passes, else 1. The report (one line per check) goes to the `report` file, or to stdout when there is one
(the frozen app is windowed and has none).
"""

import importlib
import pkgutil
import sqlite3
import sys
import traceback
from collections.abc import Callable
from pathlib import Path

import music_desk
from music_desk import __version__, paths
from music_desk.core.flac import flac_version
from music_desk.core.rsgain import rsgain_version
from music_desk.licenses import COMPONENTS

FLAG = "--self-test"
COMPANIONS = ("RefrainHere.exe", "RefrainMenu*.dll")  # beside Refrain.exe (SPEC §29, §31)


class CheckFailed(Exception):
    pass


def _bundle() -> str:
    if not paths.is_frozen():
        return "running from source (not a build)"
    folder = Path(sys.executable).resolve().parent
    root = paths.resources_root().resolve()
    if not root.is_relative_to(folder):
        raise CheckFailed(f"resources at {root}, outside the program's folder {folder}")
    missing = [pattern for pattern in COMPANIONS if not any(folder.glob(pattern))]
    if missing:
        raise CheckFailed(f"missing beside Refrain.exe: {', '.join(missing)}")
    return f"resources in {root}"


def _resources() -> str:
    needed = [paths.icon_path(), paths.rsgain_path(), paths.flac_path()]
    needed += [paths.resource_path("licenses", name) for component in COMPONENTS for name in component.files]
    missing = [str(path) for path in needed if not path.is_file()]
    if missing:
        raise CheckFailed("missing: " + ", ".join(missing))
    return f"{len(needed)} files"


def _tool(name: str, exe: Path, version: Callable[[Path], str | None]) -> Callable[[], str]:
    def check() -> str:
        found = version(exe)
        if not found:
            raise CheckFailed(f"{exe} didn't run")
        return found
    check.__name__ = name
    return check


def _modules() -> str:
    names = [info.name for info in pkgutil.walk_packages(music_desk.__path__, "music_desk.")]
    for name in names:
        importlib.import_module(name)
    return f"{len(names)} modules"


def _sqlite() -> str:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')")  # the search table (SPEC §6)
        conn.execute("INSERT INTO t VALUES ('glove')")
        if conn.execute("SELECT count(*) FROM t WHERE t MATCH 'lov'").fetchone()[0] != 1:
            raise CheckFailed("the trigram search found nothing")
    finally:
        conn.close()
    return f"SQLite {sqlite3.sqlite_version}, FTS5 trigram"


def _qt() -> str:
    from PySide6.QtWidgets import QApplication

    from music_desk.settings import Settings
    from music_desk.ui import theme
    from music_desk.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    if isinstance(app, QApplication):  # a widgets app, as MainWindow needs
        theme.apply(app)
    window = MainWindow(None, Settings())  # never shown; Settings() without a path saves nothing
    window.deleteLater()
    app.processEvents()
    return f"Qt platform {QApplication.platformName()}, main window built"


def checks() -> list[tuple[str, Callable[[], str]]]:
    return [("bundle", _bundle), ("resources", _resources),
            ("rsgain", _tool("rsgain", paths.rsgain_path(), rsgain_version)),
            ("flac", _tool("flac", paths.flac_path(), flac_version)),
            ("modules", _modules), ("sqlite", _sqlite), ("qt", _qt)]


def run(report: Path | None = None) -> int:
    lines, failed = [f"Refrain {__version__} self-test"], False
    for name, check in checks():
        try:
            lines.append(f"OK    {name}: {check()}")
        except Exception as e:  # noqa: BLE001 - every failure is reported, and fails the gate
            failed = True
            detail = str(e) if isinstance(e, CheckFailed) else traceback.format_exc().strip()
            lines.append(f"FAIL  {name}: {detail}")
    lines.append("FAILED" if failed else "PASSED")
    text = "\n".join(lines) + "\n"
    if report is not None:
        report.write_text(text, encoding="utf-8")
    elif sys.stdout is not None:
        sys.stdout.write(text)
    return 1 if failed else 0


def main(arguments: list[str]) -> int:
    """`--self-test` and, after it, the report file if any."""
    rest = arguments[arguments.index(FLAG) + 1:]
    return run(Path(rest[0]) if rest and not rest[0].startswith("--") else None)
