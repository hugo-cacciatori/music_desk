"""The static type check (SPEC §18 "Static types"): basedpyright in basic mode on `src/`, as configured in
pyproject.toml. It runs with the suite so it can't be forgotten; the tests stay the main guard."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_pyright_finds_no_errors():
    result = subprocess.run([sys.executable, "-m", "basedpyright", "--project", str(ROOT)], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr
