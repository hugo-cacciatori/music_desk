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
    assert 'name="MusicDeskHere"' in spec and "here_exe," in spec and "here_launcher.py" in spec  # SPEC §29


def test_installer_script():
    """The installer's fixed points (SPEC §13): the AppId never changes, Program Files by default with an
    "only for me" choice, the licence page, the icon, the files from the PyInstaller folder, and an uninstaller
    that keeps the app's data unless asked (and always in a silent uninstall). The Start menu gets a MusicDesk
    folder with an uninstall shortcut, and an upgrade deletes the loose shortcut of 0.1.0-0.2.x."""
    iss = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
    assert "AppId={{AF5F6B62-C2F8-41D7-8FBE-124F1D693BF9}" in iss
    assert r"DefaultDirName={autopf}\{#AppName}" in iss and "PrivilegesRequiredOverridesAllowed=dialog" in iss
    assert "ArchitecturesInstallIn64BitMode=x64compatible" in iss
    assert r"LicenseFile=..\LICENSE" in iss and r"SetupIconFile=..\resources\icons\music_desk.ico" in iss
    assert r'Source: "..\dist\MusicDesk\*"' in iss and "recursesubdirs" in iss
    assert r"{localappdata}\{#AppName}" in iss and "not UninstallSilent" in iss and "MB_DEFBUTTON2" in iss
    assert r'Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"' in iss
    assert r'Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"' in iss
    assert "DefaultGroupName={#AppName}" in iss
    assert "[InstallDelete]" in iss and r'Type: files; Name: "{autoprograms}\{#AppName}.lnk"' in iss
    assert r'Name: "{autoprograms}\{#AppName}"; Filename' not in iss
    # The musicdesk keyword (SPEC §29): App Paths points to MusicDeskHere.exe, and the uninstaller removes it.
    assert (r'Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\musicdesk.exe"; '
            r'ValueType: string; ValueName: ""; ValueData: "{app}\MusicDeskHere.exe"; Flags: uninsdeletekey') in iss
    for path in ("LICENSE", "resources/icons/music_desk.ico"):
        assert (ROOT / path).is_file()


def test_flac_is_bundled():
    """flac.exe and its one DLL ship in resources/flac (SPEC §13), with their licences."""
    spec = (ROOT / "packaging" / "music_desk.spec").read_text(encoding="utf-8")
    assert '"resources/flac"' in spec
    assert paths.flac_path().is_file() and (paths.flac_path().parent / "libFLAC.dll").is_file()


def test_the_installer_registers_the_menu_handler():
    """The right-click menu (SPEC §31): the handler's COM class at its versioned file, its three places (files,
    folders, empty space) and the approved list, all removed by the uninstaller; older handlers Explorer still has
    loaded are moved out of the app's folder, at install and uninstall."""
    iss = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
    cpp = (ROOT / "native" / "context_menu" / "menu.cpp").read_text(encoding="utf-8")
    [clsid] = re.findall(r"// \{([0-9A-F-]{36})\}: never change it", cpp)
    assert f'#define MenuClsid "{{{{{clsid}}}"' in iss
    assert '#define MenuDll "MusicDeskMenu-" + AppVersion + ".dll"' in iss
    assert r'Excludes: "MusicDeskMenu.dll"' in iss
    assert (r'Source: "..\dist\MusicDesk\MusicDeskMenu.dll"; DestDir: "{app}"; DestName: "{#MenuDll}"; '
            "Flags: ignoreversion onlyifdoesntexist") in iss
    classes = r'Root: HKA; Subkey: "Software\Classes'
    assert (classes + r'\CLSID\{#MenuClsid}"; ValueType: string; ValueName: ""; ValueData: "MusicDesk menu"; '
            "Flags: uninsdeletekey") in iss
    assert (classes + r'\CLSID\{#MenuClsid}\InprocServer32"; ValueType: string; ValueName: ""; '
            r'ValueData: "{app}\{#MenuDll}"') in iss
    assert 'ValueName: "ThreadingModel"; ValueData: "Apartment"' in iss
    assert '#define MenuKey "8MusicDesk"' in iss  # sorts right after 7-Zip's key: the submenu shows below it
    for where in ("*", "Directory", r"Directory\Background"):
        assert (classes + rf'\{where}\shellex\ContextMenuHandlers\{{#MenuKey}}"; ValueType: string; ValueName: ""; '
                'ValueData: "{#MenuClsid}"; Flags: uninsdeletekey') in iss
        # 0.8.17's key: deleted at install, or the submenu would show twice
        assert (classes + rf'\{where}\shellex\ContextMenuHandlers\MusicDesk"; ValueType: none; '
                'Flags: deletekey') in iss
    assert (r'Subkey: "Software\Microsoft\Windows\CurrentVersion\Shell Extensions\Approved"; ValueType: string; '
            'ValueName: "{#MenuClsid}"; ValueData: "MusicDesk menu"; Flags: uninsdeletevalue') in iss
    assert "ChangesAssociations=yes" in iss
    assert "RemoveMenuHandlers('{#MenuDll}');" in iss and "RemoveMenuHandlers('');" in iss
    assert not re.search(r"Flags:[^\n]*restartreplace", iss)  # it does nothing on a per-user install


def test_the_installer_build_includes_the_menu_handler():
    """build_installer.py builds MusicDeskMenu.dll into dist/MusicDesk after PyInstaller, so the installer's
    [Files] glob takes it (SPEC §31)."""
    script = (ROOT / "packaging" / "build_installer.py").read_text(encoding="utf-8")
    steps = ['"-m", "PyInstaller"', 'build_menu.build(ROOT / "dist" / "MusicDesk")',
             'self_test(ROOT / "dist" / "MusicDesk")', "[str(find_iscc())"]  # the gate before Inno Setup (rule 9)
    assert [script.index(step) for step in steps] == sorted(script.index(step) for step in steps)
