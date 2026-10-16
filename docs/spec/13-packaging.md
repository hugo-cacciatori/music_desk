# 13. rsgain and packaging
- `resources/rsgain/` holds `rsgain.exe`, its DLLs, `presets/` and the licenses. `paths.resource_path()` returns
  `sys._MEIPASS` when frozen, else the project's `resources/`.
- **No ffmpeg at runtime.** `rsgain.exe` (3.8 MB) has its FFmpeg decoders built in; its folder only adds the MSVC
  runtime DLLs. So the app never calls ffmpeg, and the installer doesn't ship it. ffmpeg is a dev-only tool for
  regenerating the test fixtures (§18). The cue split uses flac.exe instead (below).
- How rsgain is called:
  - `subprocess.Popen([...], cwd=album_folder, creationflags=CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS)`
    with UTF-8 pipes, read incrementally in the job's worker thread.
  - File names are passed relative to that folder, to keep the command line short (limit 32,767 characters). If
    it is still too long, the album is reported as unscannable instead of being split, because album mode needs
    one call.
  - rsgain prints ANSI colour codes, which are stripped as in the script's `strip_ansi`.
- Startup self-check: if `rsgain --version` fails, the ReplayGain tools are disabled with an explanation.
- **flac** (from M10, for the cue split, §22): `resources/flac/` holds `flac.exe` and `libFLAC.dll` from the
  official FLAC 1.5.0 Win64 build (`flac-1.5.0-win.zip` from github.com/xiph/flac/releases, SHA-256
  `53f1500f…0dde`, checked against the release notes on 2026-10-16), with `COPYING.GPL.txt`, `COPYING.Xiph.txt`
  and a `README.txt`. flac.exe needs only libFLAC.dll (Xiph's build notes).
  - Chosen over an LGPL FFmpeg build (77–170 MB zipped) because Hugo's single-file albums are FLAC or WAV, which
    flac reads (with AIFF, W64 and RF64). It adds about 0.9 MB.
  - Licences: the flac program is GPL-2.0-or-later and libFLAC is under Xiph's BSD-like licence, both compatible
    with the app's GPL-3.0-or-later. As built (M10 task 3) they are `flac-GPL-2.0.txt` and
    `libFLAC-Xiph-BSD.txt` in `resources/licenses/`, two `COMPONENTS` rows, and About.
  - Resolved through `paths.flac_path()` (`resources/flac/flac.exe`), never configurable (as rsgain, Ground rule
    5). The PyInstaller spec bundles `resources/flac`, as built in M10 task 3.
  - Startup self-check: `flac --version` (expected "flac 1.5.0"). If it fails, the Split card and "Split into
    tracks…" are disabled with the reason.
  - It is called like rsgain: `CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS`, output read by the job's
    thread, and killed on Stop.
- PyInstaller one-folder `--windowed` build with icon and version info (as built at M6 task 4:
  `resources/icons/music_desk.ico`, drawn by `packaging/make_icon.py` (a blue rounded square with two white beamed
  notes, 16 to 256 px), is the exe's icon and every window's; `packaging/version_info.txt` is the exe's version
  resource, kept equal to `music_desk.__version__` by `tests/test_packaging.py`). Light and dark follow Windows
  through Qt (checked with screenshots); there is no theme setting. An Inno Setup installer puts it in
  `%ProgramFiles%\MusicDesk` with a Start menu shortcut (from the uninstall-shortcut task: a MusicDesk folder in
  the Start menu, with "Uninstall MusicDesk" beside it, below). Uninstalling keeps `%LOCALAPPDATA%` data unless the user
  ticks the option.
- As built (M6 task 5), the installer (`packaging/installer.iss`, built with `packaging/build_installer.py`):
  - `dist/installer/MusicDesk-Setup-<version>.exe` (about 33 MB) installs `dist/MusicDesk/` for all users in
    `C:\Program Files\MusicDesk` (admin). Its first page also offers "only for me", in
    `%LOCALAPPDATA%\Programs\MusicDesk` without admin. It is 64-bit only.
  - Pages: the licence (`LICENSE`, GPL-3.0), then the folder. It creates a Start menu shortcut and, if ticked, a
    desktop one; "Launch MusicDesk" is offered at the end. A running MusicDesk is closed through the Restart
    Manager.
  - The AppId (`AF5F6B62-…`) is fixed, so a newer setup upgrades in place.
  - The uninstaller removes the app's files and shortcuts, then asks whether to delete `%LOCALAPPDATA%\MusicDesk`
    (index, settings, logs). No is the default; a silent uninstall keeps it, and music folders are never touched.
  - Checked on this machine: a per-user install, run and silent uninstall in a scratch folder (files and shortcut
    gone, data kept), and an all-users install in Program Files, whose exe ran with its bundled rsgain
    (`rsgain self-check OK … C:\Program Files\MusicDesk\_internal\resources\rsgain\rsgain.exe`). That install was left
    in place for Hugo.
  - `tests/test_packaging.py` checks the script's fixed points (AppId, folders, licence, icon, files, the
    uninstaller's data rule).
- **Uninstalling from the Start menu** (Hugo, 2026-10-16; the "Installer: uninstall shortcut" task):
  - **The need:** Hugo uninstalls by pressing the Windows key, typing the app's name, and clicking Uninstall on the
    result. For classic desktop apps, that button opens Settings › Installed apps, where he has to find the app
    again. His fallback is "Open file location" on the search result, hoping to find an uninstaller in the folder
    it opens.
  - **Why the button can't do it:** since Windows 11 22H2 build 22621.2361 (KB5030310, September 2023), Start's
    Uninstall on a Win32 app always routes to Settings. Only MSIX-packaged apps still get a direct uninstall
    prompt. This comes from secondary sources ([Eleven Forum's guide](https://www.elevenforum.com/t/uninstall-apps-in-windows-11.2346/));
    Hugo sees the same behaviour with 0.2.1. MSIX was rejected: it needs a paid signing certificate or the
    Microsoft Store, it moves the app's data into a package folder deleted on uninstall (losing the "keep my
    data?" question), and it replaces the Program Files install.
  - **What the installer does instead:** the Start menu shortcut goes into a **MusicDesk folder** (`{group}`, which
    follows the all-users or only-for-me choice), next to an **Uninstall MusicDesk** shortcut:

        [Icons]
        Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
        Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"; \
          Comment: "Removes MusicDesk. Your music folders are not touched."

    - "Open file location" on the search result then opens that folder, with the uninstaller in it.
    - Typing "uninstall musicdesk" in Start finds the shortcut and starts the uninstaller directly (measured below).
    - The uninstaller itself is unchanged: UAC for an all-users install, then the question about the app's data.
  - **Upgrades:** 0.1.0–0.2.x put a loose `MusicDesk.lnk` at the top of the Start menu's Programs folder. The new
    setup deletes it (`[InstallDelete] Type: files; Name: "{autoprograms}\{#AppName}.lnk"`), so Start doesn't list
    MusicDesk twice.
    - Taskbar pins are copies of the shortcut and keep working.
    - A pin on the Start menu may disappear with the old shortcut (inferred). If it does, re-pinning is needed once,
      and the release notes say so.
  - The desktop shortcut (optional task) is unchanged.
  - **Known trade-off:** Microsoft's Start guidelines advise against uninstall shortcuts. Hugo prefers the shortcut
    (2026-10-16), and Inno Setup's documentation shows exactly this entry.
  - **Checks:**
    - `tests/test_packaging.py` asserts both `[Icons]` entries and the `[InstallDelete]` line.
    - On this machine, a per-user install over a scratch 0.2.1 per-user install shows the MusicDesk folder with
      both entries and no loose shortcut.
    - "Open file location" opens that folder.
    - The search "uninstall musicdesk" finds the shortcut (record the result here as measured).
    - The shortcut starts the uninstaller.
    - Hugo's own Program Files install is upgraded only with the next release, on his go.
  - **Measured on 2026-10-16** (0.2.2): a silent per-user 0.2.1 install in `%TEMP%\md-install-test`, then a silent
    per-user 0.2.2 setup over it (`/CURRENTUSER` is needed, or the setup may follow Hugo's all-users install):
    - 0.2.1 left `Programs\MusicDesk.lnk`; 0.2.2 deleted it and created `Programs\MusicDesk\` with `MusicDesk.lnk`
      (→ the exe) and `Uninstall MusicDesk.lnk` (→ `unins000.exe`, with its comment).
    - Hugo, in Start: "Open file location" opens that folder; typing "uninstall musicdesk" **finds the shortcut**;
      clicking it starts the uninstaller.
    - Start still lists the loose shortcut of Hugo's all-users 0.2.1 install, which a per-user setup can't remove.
- Licenses to ship: rsgain (BSD-2-Clause), CRCpp, and the FFmpeg libraries built into rsgain (LGPL, checked
  below). Also Qt/PySide6 (LGPL) and mutagen (GPL-2.0-or-later).
- As built (M6 task 3): the texts are in `resources/licenses/` (bundled as `resources/licenses`, listed in its
  `README.txt` and in `music_desk/licenses.py`; `tests/test_licenses.py` checks that every runtime dependency is
  covered and every file exists). The LGPL-3.0 text (Qt) was downloaded from gnu.org with Hugo's go.
- **FFmpeg inside rsgain** (checked at M6 task 4): rsgain 3.6's Windows build takes FFmpeg 7.1 from vcpkg with
  the avcodec, avformat, swresample and fdk-aac features, without "gpl" (rsgain's `vcpkg.json` at tag v3.6), so
  FFmpeg is LGPL-2.1-or-later. The exe embeds no configuration string; the `libfdk_aac` strings in it confirm the
  FDK AAC part. `FFmpeg-LGPL-2.1.txt` is shipped, and the README points to FFmpeg's and rsgain's source.
  - The **Fraunhofer FDK AAC** licence (the `NOTICE` file of github.com/mstorsjo/fdk-aac) was downloaded with
    Hugo's go, as `FDK-AAC-NOTICE.txt`.
  - That licence isn't GPL-compatible. It is shipped inside rsgain.exe, a separate program the app runs, not
    linked into the app, which is the usual "aggregate" reading (the app's licence, below, was chosen with this
    in mind).
- **MusicDesk's own licence: GPL-3.0-or-later** (Hugo, 2026-10-15). It is compatible with mutagen's
  GPL-2.0-or-later and Qt's LGPL-3.0. `LICENSE` at the repo root is the GPL-3.0 text (the same file as
  `resources/licenses/GPL-3.0.txt`), `pyproject.toml` declares it, and About says so.
