"""Builds the release: the PyInstaller folder (dist/MusicDesk), the right-click menu's handler DLL into it
(packaging/build_menu.py, SPEC §31), the release gate (a copy of dist/MusicDesk in a temporary folder outside the
repository runs `MusicDesk.exe --self-test`; a failure stops the build), then the Inno Setup installer and its
checksum: the local release
dist/v<version>/ (MusicDesk-Setup-<version>.exe, SHA256SUMS.txt). Older dist/v* folders and the intermediate
build output (build/, dist/MusicDesk) are deleted afterwards; only the newest local release is kept.
Run from the project root:  uv run python packaging/build_installer.py

Inno Setup 6 must be installed (its ISCC.exe compiler is looked up in Program Files, or set ISCC to its path), and
Visual Studio Build Tools with "Desktop development with C++" for the DLL.
Pass --skip-pyinstaller to reuse an existing dist/MusicDesk (the DLL is built again), and --keep-build to keep
build/ and dist/MusicDesk for such a rerun. --self-test-only stops after the gate (no installer: an unreleased
build never replaces the local release).
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from music_desk import __version__  # noqa: E402

sys.path.insert(0, str(ROOT / "packaging"))
import build_menu  # noqa: E402


def find_iscc() -> Path:
    candidates = [os.environ.get("ISCC", "")]
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", "")):
        if base:
            candidates.append(str(Path(base) / "Inno Setup 6" / "ISCC.exe"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise SystemExit("Inno Setup 6 wasn't found. Install it (winget install JRSoftware.InnoSetup) or set ISCC.")


def self_test(dist: Path) -> None:
    """The release gate (AGENTS.md rule 9): the build must run on its own, from a folder that isn't the repository,
    with no Python setup or working-directory files to lean on and its own empty app data."""
    with tempfile.TemporaryDirectory(prefix="musicdesk-gate-") as temp:
        folder = Path(temp) / "MusicDesk"
        shutil.copytree(dist, folder)
        report = Path(temp) / "self-test.txt"
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PYTHON") and k.upper() != "VIRTUAL_ENV"}
        env["LOCALAPPDATA"] = str(Path(temp) / "appdata")
        try:
            code = subprocess.run([str(folder / "MusicDesk.exe"), "--self-test", str(report)], cwd=temp, env=env,
                                  timeout=180).returncode
        except subprocess.TimeoutExpired:
            code = None
        print(report.read_text(encoding="utf-8") if report.is_file() else "The self-test wrote no report.")
        if code != 0:
            raise SystemExit(f"The release gate failed (exit code {code}): no installer is built.")


def main(argv: list[str]) -> int:
    if "--skip-pyinstaller" not in argv:
        spec = ROOT / "packaging" / "music_desk.spec"
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", str(spec)], cwd=ROOT, check=True)
    if not (ROOT / "dist" / "MusicDesk" / "MusicDesk.exe").is_file():
        raise SystemExit("dist/MusicDesk/MusicDesk.exe is missing: run without --skip-pyinstaller.")
    build_menu.build(ROOT / "dist" / "MusicDesk")  # PyInstaller's --noconfirm emptied the folder
    self_test(ROOT / "dist" / "MusicDesk")
    if "--self-test-only" in argv:
        if "--keep-build" not in argv:
            shutil.rmtree(ROOT / "build", ignore_errors=True)
            shutil.rmtree(ROOT / "dist" / "MusicDesk", ignore_errors=True)
        print("The release gate passed (no installer built: --self-test-only).")
        return 0
    subprocess.run([str(find_iscc()), f"/DAppVersion={__version__}", str(ROOT / "packaging" / "installer.iss")],
                   cwd=ROOT, check=True)
    release = ROOT / "dist" / f"v{__version__}"
    installer = release / f"MusicDesk-Setup-{__version__}.exe"
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    (release / "SHA256SUMS.txt").write_text(f"{digest}  {installer.name}\n", encoding="utf-8", newline="\n")
    for old in (ROOT / "dist").iterdir():
        if old.is_dir() and old != release and (old.name.startswith("v") or old.name == "installer"):
            shutil.rmtree(old)
    if "--keep-build" not in argv:
        shutil.rmtree(ROOT / "build", ignore_errors=True)
        shutil.rmtree(ROOT / "dist" / "MusicDesk", ignore_errors=True)
    print(f"Built {installer} and {release / 'SHA256SUMS.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
