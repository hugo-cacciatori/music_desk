"""`Refrain.exe --self-test`, the release gate (AGENTS.md rule 9, SPEC §13): every check from source, a failing
one, the report file, and the start that runs it instead of the app."""

import sys
from pathlib import Path

from refrain import app, paths, self_test


def test_every_check_passes_from_source(qapp, tmp_path):
    report = tmp_path / "report.txt"
    assert self_test.main(["--self-test", str(report)]) == 0
    lines = report.read_text(encoding="utf-8").splitlines()
    assert lines[-1] == "PASSED"
    assert [line.split(":")[0].split()[-1] for line in lines[1:-1]] == [
        "bundle", "resources", "rsgain", "flac", "modules", "sqlite", "qt"]
    assert all(line.startswith("OK") for line in lines[1:-1])


def test_a_missing_tool_fails_the_gate(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "rsgain_path", lambda: tmp_path / "rsgain.exe")
    report = tmp_path / "report.txt"
    assert self_test.run(report) == 1
    text = report.read_text(encoding="utf-8")
    assert "FAIL  rsgain:" in text and "FAIL  resources: missing:" in text and text.endswith("FAILED\n")
    assert "OK    flac:" in text  # the other checks still run


def test_the_start_runs_the_gate_instead_of_the_app(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "argv", ["Refrain.exe", "--self-test", "out.txt"])
    monkeypatch.setattr(self_test, "run", lambda report=None: calls.append(report) or 7)
    monkeypatch.setattr(app, "setup_logging", lambda: calls.append("logging"))
    assert app.main() == 7 and calls == [Path("out.txt")]  # no log, no hand-over, no window
