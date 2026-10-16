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
    with the app's GPL-3.0-or-later. They join `resources/licenses/`, `licenses.py` `COMPONENTS` and About in
    M10 task 3.
  - Resolved through `paths.resource_path("flac/flac.exe")`, never configurable (as rsgain, Ground rule 5). The
    PyInstaller spec bundles `resources/flac` (M10 task 3).
  - Startup self-check: `flac --version` (expected "flac 1.5.0"). If it fails, the Split card and "Split into
    tracks…" are disabled with the reason.
  - It is called like rsgain: `CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS`, output read by the job's
    thread, and killed on Stop.
- PyInstaller one-folder `--windowed` build with icon and version info (as built at M6 task 4:
  `resources/icons/music_desk.ico`, drawn by `packaging/make_icon.py` (a blue rounded square with two white beamed
  notes, 16 to 256 px), is the exe's icon and every window's; `packaging/version_info.txt` is the exe's version
  resource, kept equal to `music_desk.__version__` by `tests/test_packaging.py`). Light and dark follow Windows
  through Qt (checked with screenshots); there is no theme setting. An Inno Setup installer puts it in
  `%ProgramFiles%\MusicDesk` with a Start menu shortcut. Uninstalling keeps `%LOCALAPPDATA%` data unless the user
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
