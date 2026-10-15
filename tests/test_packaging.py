"""Packaging inputs (SPEC §13): the app icon and the exe's version resource."""

import re
import struct
from pathlib import Path

from music_desk import __version__, paths

ROOT = Path(__file__).parents[1]


def test_icon_holds_every_windows_size():
    data = paths.icon_path().read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data)
    assert (reserved, kind) == (0, 1)
    sizes = {struct.unpack_from("<B", data, 6 + 16 * i)[0] or 256 for i in range(count)}
    assert {16, 24, 32, 48, 256} <= sizes
    for i in range(count):  # every entry points inside the file, at a PNG
        length, offset = struct.unpack_from("<II", data, 6 + 16 * i + 8)
        assert offset + length <= len(data) and data[offset:offset + 8] == b"\x89PNG\r\n\x1a\n"


def test_version_info_matches_the_app_version():
    text = (ROOT / "packaging" / "version_info.txt").read_text(encoding="utf-8")
    numbers = tuple(int(n) for n in __version__.split("."))
    assert f"filevers=({', '.join(map(str, numbers))}, 0)" in text
    assert f"prodvers=({', '.join(map(str, numbers))}, 0)" in text
    assert re.findall(r"StringStruct\('(?:File|Product)Version', '([^']+)'\)", text) == [__version__] * 2


def test_the_spec_uses_them():
    spec = (ROOT / "packaging" / "music_desk.spec").read_text(encoding="utf-8")
    assert "music_desk.ico" in spec and "version_info.txt" in spec and '"resources/icons"' in spec


def test_installer_script():
    """The installer's fixed points (SPEC §13): the AppId never changes, Program Files by default with an
    "only for me" choice, the licence page, the icon, the files from the PyInstaller folder, and an uninstaller
    that keeps the app's data unless asked (and always in a silent uninstall)."""
    iss = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
    assert "AppId={{AF5F6B62-C2F8-41D7-8FBE-124F1D693BF9}" in iss
    assert r"DefaultDirName={autopf}\{#AppName}" in iss and "PrivilegesRequiredOverridesAllowed=dialog" in iss
    assert "ArchitecturesInstallIn64BitMode=x64compatible" in iss
    assert r"LicenseFile=..\LICENSE" in iss and r"SetupIconFile=..\resources\icons\music_desk.ico" in iss
    assert r'Source: "..\dist\MusicDesk\*"' in iss and "recursesubdirs" in iss
    assert r"{localappdata}\{#AppName}" in iss and "not UninstallSilent" in iss and "MB_DEFBUTTON2" in iss
    for path in ("LICENSE", "resources/icons/music_desk.ico"):
        assert (ROOT / path).is_file()
