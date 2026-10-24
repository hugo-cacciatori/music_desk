"""Builds MusicDeskMenu.dll, File Explorer's MusicDesk submenu (SPEC §31), from native/context_menu/.

Needs Visual Studio Build Tools with "Desktop development with C++" (on the development PC only), found through
vswhere. The C runtime is linked in (/MT), so the DLL needs no Visual C++ runtime on the user's PC.
Run from the project root:  uv run python packaging/build_menu.py [<output folder>]   (default: dist/MusicDesk)
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "native" / "context_menu"
DLL_NAME = "MusicDeskMenu.dll"


def find_vcvars() -> Path | None:
    """vcvars64.bat of the newest Visual Studio (or Build Tools) with the C++ compiler, or None."""
    vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft Visual Studio" / \
        "Installer" / "vswhere.exe"
    if not vswhere.is_file():
        return None
    found = subprocess.run([str(vswhere), "-products", "*", "-latest", "-requires",
                            "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
                           capture_output=True, text=True, check=False).stdout.strip()
    vcvars = Path(found) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat" if found else None
    return vcvars if vcvars is not None and vcvars.is_file() else None


def build(output: Path) -> Path:
    """Compiles the DLL into `output`; raises SystemExit with what is missing or the compiler's output."""
    vcvars = find_vcvars()
    if vcvars is None:
        raise SystemExit("The C++ compiler wasn't found: install Visual Studio Build Tools with "
                         "\"Desktop development with C++\".")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as work:
        compile_line = subprocess.list2cmdline([
            # /utf-8: the source's labels hold a minus sign (U+2212), and its comments other non-ASCII characters
            "cl", "/nologo", "/utf-8", "/O2", "/W4", "/WX", "/EHsc", "/MT", "/std:c++17", "/DUNICODE", "/D_UNICODE",
            "/LD",
            str(SOURCE / "menu.cpp"), f"/Fo{work}\\", "/link", f"/DEF:{SOURCE / 'menu.def'}",
            f"/OUT:{output / DLL_NAME}", f"/IMPLIB:{Path(work) / 'MusicDeskMenu.lib'}",
            "shell32.lib", "shlwapi.lib", "ole32.lib", "user32.lib", "uuid.lib"])
        script = Path(work) / "build.cmd"
        script.write_text(f'@call "{vcvars}" >nul\r\n@{compile_line}\r\n', encoding="mbcs")
        result = subprocess.run(["cmd", "/c", str(script)], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"The menu handler didn't build:\n{result.stdout}{result.stderr}")
    return output / DLL_NAME


def main(argv: list[str]) -> int:
    dll = build(Path(argv[0]) if argv else ROOT / "dist" / "MusicDesk")
    print(f"Built {dll}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
