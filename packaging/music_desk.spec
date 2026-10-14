# PyInstaller spec: one-folder, windowed build with the bundled rsgain.
# Build from the project root:  uv run pyinstaller packaging/music_desk.spec
# Icon and version info come later (app name and icon are deferred, see CLAUDE.md Open questions).

from pathlib import Path

ROOT = Path(SPECPATH).parent

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    # paths.resource_path() expects <_MEIPASS>/resources/rsgain when frozen.
    datas=[(str(ROOT / "resources" / "rsgain"), "resources/rsgain")],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MusicDesk",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="MusicDesk",
)
