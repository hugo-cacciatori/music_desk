"""The licence texts shipped with the app (SPEC §13): every component's files exist, every runtime dependency is
listed, and the README names every file."""

import re
import tomllib
from pathlib import Path

from refrain.licenses import COMPONENTS, licenses_dir

ROOT = Path(__file__).parents[1]


def test_every_component_has_its_files():
    folder = licenses_dir()
    for component in COMPONENTS:
        assert component.files, component.name
        for name in component.files:
            assert (folder / name).is_file() and (folder / name).stat().st_size > 200, name


def test_every_runtime_dependency_is_listed():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    listed = {c.distribution.casefold() for c in COMPONENTS if c.distribution}
    for requirement in project["dependencies"]:
        name = re.split(r"[<>=!~\[ ;]", requirement, maxsplit=1)[0].casefold()
        assert name in listed, name


def test_the_readme_names_every_file():
    folder = licenses_dir()
    readme = (folder / "README.txt").read_text(encoding="utf-8")
    for path in folder.iterdir():
        if path.name != "README.txt":
            assert path.name in readme, path.name


def test_the_app_licence():
    """Refrain is GPL-3.0-or-later: the repo's LICENSE is the GPL-3.0 text, also shipped in the licences folder."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["license"] == "GPL-3.0-or-later"
    licence = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "GNU GENERAL PUBLIC LICENSE" in licence and "Version 3, 29 June 2007" in licence
    assert licence == (licenses_dir() / "GPL-3.0.txt").read_text(encoding="utf-8")


def test_installed_versions():
    versions = {c.name: c.installed_version() for c in COMPONENTS}
    assert versions["mutagen"] and versions["Qt for Python (PySide6, shiboken6)"] == "6.12.0"
