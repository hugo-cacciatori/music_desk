# MusicDesk: CLAUDE.md

MusicDesk is a Windows desktop app (Python 3.13 + PySide6) for browsing a music library from a SQLite index,
editing tags, tidying albums (renaming from tags, ReplayGain with the bundled rsgain), looking up lyrics on LRCLIB
in the background and building playlists. It replaced `music_tools.py` and `seek_lyrics.py`; their frozen copies
are in `tests/reference/`.

This file is the working summary: state, rules, the work plan and open questions. It is the only doc loaded
automatically, so keep it short (under about 15 KB): how things were built belongs in the spec, not here.
- `docs/SPEC.md`: the spec's index. "SPEC §n" means `docs/spec/NN-*.md`; read the files the current milestone
  lists, not the whole spec.
- `docs/DECISIONS.md`: every dated decision. New decisions get a row there, and their spec section is updated at
  the same time.
- `docs/HISTORY.md`: how M0–M7 were built (session notes, checklists, measurements). Read it only to find out
  why something is the way it is.
- `README.md`: the user-facing description.

## Resume here
- **State (2026-10-16, session 12):** version 0.2.9 is on `main` (not pushed). 0.2.2 added the Start menu folder
  with "Uninstall MusicDesk" (SPEC §13, measured). 0.2.3 added `fsops.can_recycle` (SPEC §19, measured on a
  network path). 0.2.4 added Delete to the Recycle Bin. 0.2.5 and 0.2.6 added the file preview (M9; text cap
  512 KB and the wrap rule, measured in SPEC §20). 0.2.7 added the shared file name masks (M10 task 1, §21).
  0.2.8 added `core/cue.py`, the split plan, with Hugo's EAC sheet as a fixture (M10 task 2, §22). 0.2.9
  bundles flac, with its self-check, `core/flac.py` and `core/split.py` (cut, proof, cleanup; M10 task 3).
  v0.2.1 is the latest GitHub release. M0–M9 are complete (Progress is in HISTORY.md). All 1046 tests passed
  in the last full run, slow ones included. Ruff is clean.
- **Left to Hugo:** opening an exported playlist in a real player, and a mouse drag in the installed app.
- **Next step: M10 task 4**: the Split card, "Split into tracks…", the preview page with its encoding picker, and
  the job (lock, Stop, renames from `.partial`, tags and cover, recycling, ReplayGain afterwards) and its report.
  The frozen build's flac hasn't been run yet (task 5 builds the installer).
- **Watcher flake, solved (2026-10-16, measured):** `test_live_folder_changes` failed 2 runs in 17 with
  "Access is denied" on its folder rename. The cause was the app's own tag read of the new file: Windows
  refuses to rename a folder while any file under it is open, even with FILE_SHARE_DELETE (SPEC §7). It hit
  7 runs in 100, and 0 in 100 with tag reading stubbed out. The test now waits for the tag read and retries
  the rename for up to 2 s: 0 failures in 40 runs alone.

## Work plan
Specified with Hugo on 2026-10-16. Each task ends with a patch version bump (0.2.2, 0.2.3, …); M10 ends with
0.3.0 and a release. Tick tasks as they are done.

**Installer: uninstall shortcut** (read SPEC §13, "Uninstalling from the Start menu"). Small, so it goes first; it
reaches Hugo with the next setup he asks for.
- [x] 1. `installer.iss`: the shortcut in a MusicDesk Start menu folder with "Uninstall MusicDesk", and the old loose
      shortcut deleted on upgrade; `test_packaging.py`; the README's Install section says how to uninstall.

Done when: a per-user install over a scratch 0.2.1 install shows the folder with both shortcuts and no loose one,
"Open file location" leads there, the shortcut starts the uninstaller, and §13 records whether typing "uninstall
musicdesk" finds it.

**M8 Recycle Bin safety and Delete** (read SPEC §19, §5, and §10 A for the junk cleanup). This comes first: the
junk cleanup already relies on recycling, and the split's originals will.
- [x] 1. Measure what send2trash does where Windows can't recycle. Send a scratch file under `\\localhost\C$\…`
      (show Hugo the exact commands first), then check the Recycle Bin, and record the measured result in §19.
      Add `fsops.can_recycle`; `send_to_recycle_bin` refuses when it fails; the junk cleanup keeps and reports
      such files. Tests use an injected check.
- [x] 2. Delete: Del, Shift+Del and the menu item in the list, the tree and the results, through `ask_delete`;
      locks, refresh, the selection afterwards, and the playlist's Del unchanged.

Done when:
- a multi-selection of files and folders goes to the Recycle Bin exactly as confirmed (tests with an injected
  recycler);
- roots, locked items and places that can't recycle are refused;
- §19 records the measured network-path behaviour;
- a manual run on a copied folder sends the items to the real Recycle Bin, and Hugo finds them there.

**M9 File preview** (read SPEC §20, §7)
- [x] 1. `core/preview.py` (`read_text`, the encodings; `decode_text` with UTF-16), with fixtures and tests.
- [x] 2. The Preview page: `TagIO.read_preview`, `FilePreview` as the side panel's third page, the refresh, and
      the stall measurement for 1 MB of text.

Done when:
- every listed type previews;
- an EAC UTF-16 log, a cp1252 text and a CP437 `.nfo` read correctly;
- showing the capped text stays under the 50 ms stall target (measured);
- nothing on the page can write.

**M10 Shared masks and cue split** (read SPEC §21, §22, §13, and §10)
- [x] 1. Masks: `core/masks.py` (moved out of `rename.py`; the parity tests stay unchanged),
      `Settings.name_masks` / `name_replacement` with the migration, the File names card, and the hint using the
      user's masks.
- [x] 2. `core/cue.py`: parsing, encodings, samples, boundaries and the hidden track, names and clashes, tags;
      the split fixtures, with audio that never repeats.
- [x] 3. flac: bundle `resources/flac` (PyInstaller spec, `licenses.py`, `resources/licenses/`, About), the
      `flac --version` self-check, the runner and the proof, `slow` tests.
- [ ] 4. UI: the Split card, "Split into tracks…" (menus, `.cue` preview), the preview page with its encoding
      picker, the job (lock, Stop, partial cleanup, recycling, ReplayGain afterwards) and its report.
- [ ] 5. README, version 0.3.0, the installer, and the GitHub release (Hugo's go).

Done when:
- every split fixture gets its expected result;
- the slow test proves the per-track lengths and the MD5 on the FLAC and WAV images, and fails on a one-sample
  shift;
- the installed app splits a copied album with its bundled flac.

## Session protocol
1. Read this file, then the spec files of the current milestone. Pick up the first unchecked task.
2. Hugo's global rule: before running anything destructive (deleting files, etc.), tell him the exact command
   first.
   - Hugo works in auto mode, whose safety check blocks some needed commands (running an installer, for
     example). When a step needs more privileges than auto mode allows, ask him to switch to manual mode (and
     say why) instead of working around the block; tell him when he can switch back.
3. Commit each finished task on `main`. Push, publish a release or install global tools only when Hugo asks.
   Project dependencies go in the project's virtual environment.
4. **Versions** (Hugo, M7; reminded 2026-10-16): don't forget them. Every finished task of the work plan bumps
   the patch version (0.2.2 → 0.2.3) in its own commit, following "Version bump" below. A milestone that ends
   with a release (M10: 0.3.0) bumps the minor version instead (see Release). Docs-only commits and measurements
   don't bump.
5. At the end of a session:
   - tick the finished tasks in the work plan;
   - rewrite Resume here (state and next step, a few lines, no history);
   - add the new decisions to `docs/DECISIONS.md` and update the spec sections they touch;
   - move answered questions out of Open questions.

## Ground rules
Code and tests cite these by number; keep the numbering.
1. The app never copies, moves, deletes or creates files or folders in the library. The only exceptions:
   - renaming files and folders (inline F2, or the Rename tool), including an audio file's same-stem `.lrc`
     sidecar, which follows its rename;
   - writing tags into music files (metadata editor, "Save to file tags" in the Lyrics tab, ReplayGain tool);
   - writing `replaygain.log` in album folders (ReplayGain tool; each run replaces it, while the script
     appended);
   - writing a playlist the user exports from the playlist builder, where the user saves it (the Save dialog
     asks before replacing a file);
   - sending junk files to the **Recycle Bin**, only when the user ticks that option in the Rename tool and
     confirms. Audio files, playlists, cover images, the ReplayGain log, cue sheets of single-file albums and
     `.lrc` files not yet in the tags are never junk (SPEC §10);
   - (M8) sending the files and folders the user selects to the **Recycle Bin** with Delete, after a
     confirmation (SPEC §19);
   - (M10) creating the track files of a cue split, never over an existing file, and deleting that run's own
     `.partial` files; its originals go to the Recycle Bin only when the user ticks it (SPEC §22).

   Nothing is ever deleted permanently: everything removed goes to the Recycle Bin, and only where Windows can
   recycle (`fsops.can_recycle`, SPEC §19). The only exception is the split's own `.partial` files. For anything
   else, the app offers "Open in File Explorer". Drags inside the app carry entry ids only (never
   file URLs) and only copy, because a drop of files in Explorer would move them. The app's own files (index,
   settings, logs, temporary playlists) live only in `%LOCALAPPDATA%\MusicDesk\`.
2. The UI thread never blocks. All scanning, tag reading and writing, rsgain runs and LRCLIB calls happen in
   background workers. Target: no event-loop stall above 50 ms.
3. Behavior parity: the ported logic behaves exactly like the scripts, except for the deliberate changes in
   SPEC §17. Frozen copies of the scripts live in `tests/reference/`, and parity tests compare the ported code
   against them.
4. `core/` never imports Qt. It is plain Python and unit-tested.
5. rsgain is bundled in `resources/rsgain/` and resolved through `paths.resource_path()`. Its path is never
   configurable. The same goes for flac in `resources/flac/` (M10).
6. Development and tests never touch a real library. Use the fixtures or copies.

## Standing decisions
The full log is `docs/DECISIONS.md`. These apply to all work:
- **Lean UI** (Hugo): a feature must answer a real need. Propose its lightest form first (a menu entry, an
  existing page), and never resize what is already on screen. Smart views and a Settings dialog were dropped for
  this reason; preferences live where they are used (SPEC §12).
- **Stack:** Python 3.13, PySide6-Essentials 6.12.0 (no Addons: no WebEngine, no PDF), mutagen for every tag
  read and write, SQLite (WAL, FTS5 trigram), watchdog, natsort (Explorer order), send2trash, urllib. uv with
  hatchling and a `src/` layout; pytest, pytest-qt, ruff; PyInstaller one-folder plus Inno Setup. tinytag
  2.0.0 is test-only (parity). The stacks considered and rejected are in HISTORY.md ("Stack").
- **Licence:** GPL-3.0-or-later. Every bundled third-party component is listed in `music_desk/licenses.py`, with
  its text in `resources/licenses/` (SPEC §13).
- **Repo:** public on GitHub, `hugo-cacciatori/music_desk`, branch `main`.

## Conventions
- Python 3.13, type hints, ruff (line length 120), dataclasses for values, `pathlib` in new code.
- Match the scripts' style: short docstrings that explain why, and plain, specific user messages that say what
  happens to which files.
- `logging` writes to `%LOCALAPPDATA%\MusicDesk\logs\app.log`.
- Long work takes a `CancelToken`; workers talk to the UI only through queued signals (SPEC §16).

**Testing gotchas** (learned the hard way, details in HISTORY.md):
- Tests never open a real dialog: an autouse fixture fails them. Dialogs go through `confirm_*`, `ask_*` and
  `choose_*` methods that tests replace.
- Tests never use the real Recycle Bin (inject a recorder), the real network (an autouse guard blocks urllib's
  opener; LRCLIB is scripted with `tests/core/lrclib_fake.py`) or a real library.
- For signals emitted from worker threads, use `collect(signal)` and `qtbot.waitUntil`, not `qtbot.waitSignal`
  (a worker can emit while the blocker is being set up).
- pytest-qt closes the test's widgets before fixture finalizers run: a test with a running job must end it, or
  pre-answer `confirm_quit_with_jobs`.
- Test module names must be unique across folders (pytest refuses two `test_search.py`).
- A run that takes 30 s or more before starting is pytest deleting an old 100k-file temp tree, not a hang.
- The UI tests open real windows; some rely on window focus, so they don't run offscreen.

## Measurements (this machine)
- M2 "Done when" (`tests/ui/test_responsiveness.py`, slow; 500 folders, 10,000 files, 1,000 tracks): indexing
  2.1 s, worst stall 45 ms while indexing and 47 ms while browsing, reopening a folder 3–4 ms.
- Re-measured on 2026-10-15: about 3.5 s of indexing and stalls of 50–80 ms, with rare outliers (150–233 ms).
  The pre-M4 code measured the same that day, so this is the machine, not a regression.
- Other timings: search in SPEC §6, the scanner in HISTORY.md.

## Environment (checked 2026-10-15)
- Windows 11 Pro. Shells: PowerShell 5.1 (primary) and Git Bash.
- Python 3.13.1 at `C:\Users\Hugo\AppData\Local\Programs\Python\Python313`. The project venv is `.venv/`
  (pinned in `.python-version`; its mutagen 1.48.1 also runs the parity tests' reference scripts).
- uv 0.13.1 (winget). A shell started before its install may lack it on PATH: use
  `%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe`.
- Inno Setup 6.7.3 in `C:\Program Files\Inno Setup 6\`, for the installer build only.
- ffmpeg (dev-only, for regenerating fixtures, SPEC §18) at
  `C:\Program Files\ffmpeg\ffmpeg-master-latest-win64-gpl-shared\bin\ffmpeg.exe`. Its build has no Speex
  encoder.
- `gh` is logged in as hugo-cacciatori.
- My shells may see a sandboxed view of `%LOCALAPPDATA%\MusicDesk`: ask Hugo for his app.log, and run the
  installed app with a scratch `LOCALAPPDATA`.
- The original scripts' rsgain download is gone; `resources/rsgain/` is the only copy. To run the frozen
  `music_tools.py` for a comparison, point its `RSGAIN_PATH` at `resources/rsgain/rsgain.exe`, on a copy. Never
  edit `tests/reference/` (ruff excludes it; pytest collects neither `reference/` nor `fixtures/`).

## Commands
From the project root:

    uv sync
    uv run python -m music_desk
    uv run pytest                 # add -m "not slow" to skip rsgain integration tests
    uv run ruff check .
    uv run pyinstaller --noconfirm packaging/music_desk.spec    # -> dist/MusicDesk/MusicDesk.exe
    uv run python packaging/build_installer.py                  # both builds -> dist/installer/MusicDesk-Setup-<v>.exe
    uv run python packaging/make_icon.py                        # redraw resources/icons/ after changing the icon

**Version bump:** `music_desk.__version__`, `packaging/version_info.txt` (`tests/test_packaging.py` checks they
agree) and pyproject's `version`, then `uv lock`.
**Release** (Hugo's go): bump the version, run `uv run python packaging/build_installer.py`, then
`gh release create v<version> dist/installer/MusicDesk-Setup-<version>.exe`.

## Project layout
    CLAUDE.md, README.md, LICENSE (GPL-3.0), pyproject.toml, uv.lock
    docs/                 SPEC.md (index), spec/NN-*.md, DECISIONS.md, HISTORY.md
    src/music_desk/
      app.py, __main__.py   entry point: logging, the rsgain self-check, the window
      paths.py, logging_setup.py, settings.py (settings.json; playlist.json beside it), licenses.py
      core/   (no Qt)  formats, tags, metadata, masks, rename, inline_rename, replaygain, rsgain, lyrics, preview,
                       playlists, playlist_builder, fsops, path_locks, cancel; index/ (schema v3, store, scanner,
                       queries, search, roots, lyrics_index, lyrics_import)
      services/ (Qt)   library (owns writer, indexer, watcher, lyrics service), indexer, watcher, lyrics_service,
                       lyrics_embedder, search_service, tag_io, tag_writer, jobs, rename_job, replaygain_job
      ui/              main_window, sidebar, folder_tree, file_view, address_bar, search_view, playlist_panel,
                       entry_drag, stall_watch, jobs_panel, about_dialog, toast;
                       side_panel/ (panel, metadata_tab, lyrics_tab, folder_summary, file_preview);
                       tools/ (tools_page, names_card, rename_card, replaygain_card, rename_preview, rg_report)
    resources/            rsgain/ (rsgain.exe 3.6), flac/ (flac 1.5.0, bundled from M10), icons/, licenses/
    tests/                conftest.py, reference/ (frozen scripts), fixtures/ (make_fixtures.py + generated/),
                          core/ (lrclib_fake.py, rsgain_fake.py), ui/ (conftest.py, browser_helpers.py)
    packaging/            music_desk.spec, launcher.py, installer.iss, build_installer.py, make_icon.py,
                          version_info.txt

## Open questions
None.
