# PyInstaller spec: one-folder, windowed build with the bundled rsgain.
# Build from the project root:  uv run pyinstaller packaging/music_desk.spec
# The icon is drawn by packaging/make_icon.py; the version info is packaging/version_info.txt (keep its version in
# step with music_desk.__version__: tests/test_packaging.py checks it).

from pathlib import Path

ROOT = Path(SPECPATH).parent

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    # paths.resource_path() expects <_MEIPASS>/resources/rsgain when frozen.
    datas=[(str(ROOT / "resources" / "rsgain"), "resources/rsgain"),
           (str(ROOT / "resources" / "licenses"), "resources/licenses"),  # shown by Help > About
           (str(ROOT / "resources" / "icons"), "resources/icons")],
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
    icon=str(ROOT / "resources" / "icons" / "music_desk.ico"),
    version=str(ROOT / "packaging" / "version_info.txt"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="MusicDesk",
)
