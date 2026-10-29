# PyInstaller spec: one-folder, windowed build with the bundled rsgain and flac.
# Build from the project root:  uv run pyinstaller packaging/refrain.spec
# The icon is drawn by packaging/make_icon.py; the version info is packaging/version_info.txt (keep its version in
# step with refrain.__version__: tests/test_packaging.py checks it).

from pathlib import Path

ROOT = Path(SPECPATH).parent

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    # paths.resource_path() expects <_MEIPASS>/resources/rsgain (and flac) when frozen.
    datas=[(str(ROOT / "resources" / "rsgain"), "resources/rsgain"),
           (str(ROOT / "resources" / "flac"), "resources/flac"),  # the cue split (SPEC §22)
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
    name="Refrain",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ROOT / "resources" / "icons" / "refrain.ico"),
    version=str(ROOT / "packaging" / "version_info.txt"),
)
# RefrainHere.exe (SPEC §29): what the `refrain` keyword in File Explorer's address bar runs. Plain Python, no
# Qt, so it starts at once; it starts Refrain.exe on its working directory.
here = Analysis(
    [str(ROOT / "packaging" / "here_launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "PySide6", "shiboken6", "mutagen", "natsort", "send2trash", "watchdog"],
    noarchive=False,
)
here_exe = EXE(
    PYZ(here.pure),
    here.scripts,
    [],
    exclude_binaries=True,
    name="RefrainHere",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ROOT / "resources" / "icons" / "refrain.ico"),
    version=str(ROOT / "packaging" / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    here_exe,
    here.binaries,
    here.datas,
    strip=False,
    upx=False,
    name="Refrain",
)
