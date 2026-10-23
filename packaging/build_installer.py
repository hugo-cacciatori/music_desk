"""Builds the release: the PyInstaller folder (dist/MusicDesk), the right-click menu's handler DLL into it
(packaging/build_menu.py, SPEC §31), then the Inno Setup installer (dist/installer/MusicDesk-Setup-<version>.exe).
Run from the project root:  uv run python packaging/build_installer.py

Inno Setup 6 must be installed (its ISCC.exe compiler is looked up in Program Files, or set ISCC to its path), and
Visual Studio Build Tools with "Desktop development with C++" for the DLL.
Pass --skip-pyinstaller to reuse an existing dist/MusicDesk (the DLL is built again).
"""

import os
import subprocess
import sys
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


def main(argv: list[str]) -> int:
    if "--skip-pyinstaller" not in argv:
        spec = ROOT / "packaging" / "music_desk.spec"
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", str(spec)], cwd=ROOT, check=True)
    if not (ROOT / "dist" / "MusicDesk" / "MusicDesk.exe").is_file():
        raise SystemExit("dist/MusicDesk/MusicDesk.exe is missing: run without --skip-pyinstaller.")
    build_menu.build(ROOT / "dist" / "MusicDesk")  # PyInstaller's --noconfirm emptied the folder
    subprocess.run([str(find_iscc()), f"/DAppVersion={__version__}", str(ROOT / "packaging" / "installer.iss")],
                   cwd=ROOT, check=True)
    print(f"Built {ROOT / 'dist' / 'installer' / f'MusicDesk-Setup-{__version__}.exe'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
