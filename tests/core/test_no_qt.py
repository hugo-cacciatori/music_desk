"""Ground rule 4: core/ never imports Qt."""

import ast
from pathlib import Path

import refrain.core

CORE_DIR = Path(refrain.core.__file__).parent


def test_core_does_not_import_qt():
    offenders = []
    for path in CORE_DIR.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            offenders += [f"{path.name}: {n}" for n in names if n.split(".")[0] in {"PySide6", "shiboken6", "PyQt6"}]
    assert offenders == []
