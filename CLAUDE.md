# MusicDesk: CLAUDE.md

MusicDesk (working name) is a Windows desktop app for browsing a music library, editing tags, tidying albums
(renaming from tags, ReplayGain with the bundled rsgain) and looking up lyrics in the background. It replaces
`music_tools.py` and `seek_lyrics.py`, which sit at the project root (`C:\Code\music_desk\`).

This file is the working summary: state, rules, decisions, progress and open questions. The detailed feature spec
is in [docs/SPEC.md](docs/SPEC.md), referred to as "SPEC §n". It isn't loaded automatically: before working on a
milestone, read the SPEC sections listed under it in Progress. A new session must be able to continue from these
two files alone.

## Resume here
**Current state (2026-10-15, end of session 1):**
- The spec is complete and approved (`docs/SPEC.md`).
- **M0 is complete.** uv 0.13.1 is installed; `pyproject.toml`, `uv.lock` and `.venv/` exist. `src/music_desk/`
  has `paths.py`, `logging_setup.py`, `core/rsgain.py` (only the `--version` self-check), an empty `MainWindow`
  whose status bar shows the rsgain version, and empty `services/` and `ui/` packages. 8 tests pass, ruff is clean.
- `uv run pyinstaller --noconfirm packaging/music_desk.spec` builds `dist/MusicDesk/` (about 104 MB). The built
  exe starts, shows the window, and logs `rsgain 3.6` resolved from `dist\MusicDesk\_internal\resources\rsgain\`.
- No git repo yet.

**Next step:** M1 (see Progress), starting with freezing the scripts into `tests/reference/`. Don't start it until
Hugo says so.

**Session protocol:**
1. Read this file, then the SPEC sections for the current milestone. Pick up the first unchecked task in Progress.
2. Hugo's global rule: before running anything destructive (deleting files, etc.), tell him the exact command first.
3. Don't run `git init` until Hugo asks. Don't install global tools without asking. Project dependencies go in the
   project's virtual environment.
4. At the end of a session:
   - tick the finished tasks in Progress;
   - update Current state and Next step;
   - add new decisions, dated, to the Decisions log, and update the SPEC sections they touch;
   - move answered questions out of Open questions.

**Environment (checked 2026-10-15):**
- Windows 11 Pro. Shells: PowerShell 5.1 (primary) and Git Bash.
- Python 3.13.1 at `C:\Users\Hugo\AppData\Local\Programs\Python\Python313`, with these installed globally:
  mutagen 1.47.0, tinytag 2.0.0, PyInstaller 6.13.0. PySide6 isn't installed.
- uv 0.13.1 installed with winget (2026-10-15). Its alias is
  `%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe`; a shell started before the install may not have it on PATH,
  so use that full path there. The project venv is `.venv/` (Python 3.13.1, pinned in `.python-version`).
- ffmpeg is installed at `C:\Program Files\ffmpeg\ffmpeg-master-latest-win64-gpl-shared\bin\ffmpeg.exe`. It is
  only for regenerating test fixtures (SPEC §18). The app never needs it, because rsgain has its decoders built in
  (SPEC §13).
- git is installed, but there is no repo here.

**Sources:**
- `music_tools.py` and `seek_lyrics.py` at the project root: the code to port. Frozen copies go into
  `tests/reference/` at M1. Keep the root copies out of ruff and pytest collection.
- The original rsgain download (`rsgain-3.6-win64/`, with the legacy `scan_library_full.py`) is no longer on disk;
  `resources/rsgain/` is the only copy. The legacy script was superseded and isn't ported.
- `music_tools.py` resolves rsgain as `<script folder>/rsgain-3.6-win64/rsgain.exe`, which doesn't exist anymore.
  Before running the script for a comparison (M5), run it on a copy whose `RSGAIN_PATH` points at
  `resources/rsgain/rsgain.exe`.
- The legacy script pointed at a library on `H:\`. Never point development runs at a real library.

## Ground rules
1. The app never copies, moves, deletes or creates files or folders in the library. The only exceptions:
   - renaming files and folders (inline F2, or the Rename tool), including an audio file's same-stem `.lrc`
     sidecar, which follows its rename;
   - writing tags into music files (metadata editor, "Save to file tags" in the Lyrics tab, ReplayGain tool);
   - appending to `replaygain.log` in album folders (ReplayGain tool, as the script does);
   - sending junk files to the **Recycle Bin**, only when the user ticks that option in the Rename tool and
     confirms. Audio files, playlists, cover images, the ReplayGain log, cue sheets of single-file albums and `.lrc`
     files not yet in the tags are never junk (SPEC §10).

   For anything else, the app offers "Open in File Explorer". Dragging items out of the app is disabled, because a
   drop in Explorer would move the files. The app's own files (index, settings, logs, temporary playlists) live only
   in `%LOCALAPPDATA%\MusicDesk\`.
2. The UI thread never blocks. All scanning, tag reading and writing, rsgain runs and LRCLIB calls happen in
   background workers. Target: no event-loop stall above 50 ms.
3. Behavior parity: the ported logic behaves exactly like the scripts, except for the deliberate changes in
   SPEC §17. Frozen copies of the scripts live in `tests/reference/`, and parity tests compare the ported code
   against them.
4. `core/` never imports Qt. It is plain Python and unit-tested.
5. rsgain is bundled in `resources/rsgain/` and resolved through `paths.resource_path()`. Its path is never
   configurable.
6. Development and tests never touch a real library. Use the fixtures or copies.

## Decisions log
| Date       | Decision |
|------------|----------|
| 2026-10-15 | Stack: Python 3.13 + PySide6, SQLite index, PyInstaller + Inno Setup (see Stack). |
| 2026-10-15 | The app replaces the scripts. It lives in `C:\Code\music_desk\`, with no git repo until Hugo asks. rsgain is copied into `resources/rsgain/`. |
| 2026-10-15 | Junk cleanup is opt-in and sends files to the Recycle Bin after a preview and a confirmation. |
| 2026-10-15 | v1 editing includes multi-file tag editing, folder renaming and cover editing. The lyrics tag is an ordinary field in the Metadata tab. The Lyrics tab shows only LRCLIB results, or the Genius/Google buttons. |
| 2026-10-15 | Per-track fields (Title, Track #, Lyrics) are edited one file at a time. They are disabled in multi-selection. |
| 2026-10-15 | Every track is looked up on LRCLIB, even when it already has a lyrics tag. This deliberately changes the script's tags-first rule. |
| 2026-10-15 | No songs.html export. |
| 2026-10-15 | Support as many formats as possible: every format mutagen can tag, and ReplayGain on every format rsgain supports (SPEC §1). |
| 2026-10-15 | No playback in the app. "Open with default app", plus "Play folder" and "Play selection" through a temporary .m3u8 playlist. A playlist builder is planned as M7. |
| 2026-10-15 | ReplayGain albums are scanned one at a time (no parallel rsgain), in the background, so the UI stays responsive. |
| 2026-10-15 | The junk list no longer conflicts with the rest of the app (SPEC §10). .m3u is removed from the defaults. Playlists, cover images, audio files and the ReplayGain log can't be junk. Single-file-album .cue sheets and .lrc files not yet in the tags are kept. Kept .lrc sidecars follow their audio file's rename, and the Metadata tab can import them. |
| 2026-10-15 | ffmpeg isn't bundled: the app doesn't need it, because rsgain has its decoders built in. ffmpeg is a dev-only tool, and the fixtures it generates are committed. |
| 2026-10-15 | The detailed spec lives in `docs/SPEC.md`. This file keeps the working summary. |
| 2026-10-15 | Paths corrected: the project is `C:\Code\music_desk\`, the original scripts sit at its root, and `resources/rsgain/` is the only rsgain copy left. |
| 2026-10-15 | Env: uv (installed by winget), hatchling build backend, `src/` layout. |
| 2026-10-15 | Qt dependency is `PySide6-Essentials==6.12.0`, not the full `PySide6` meta-package: the app only needs QtCore/QtGui/QtWidgets, and Addons (WebEngine etc.) would bloat the build. If a later milestone needs an Addons module, switch back and pin the same version. |
| 2026-10-15 | PyInstaller enters through `packaging/launcher.py` and bundles `resources/rsgain` as `resources/rsgain` inside `_internal/`. Icon and version info wait for the app name decision. |
| deferred   | App name and icon. Retiring the original scripts. |

## Stack
| Concern        | Choice |
|----------------|--------|
| Language       | Python 3.13 |
| UI             | PySide6-Essentials 6.12.0 (pinned at M0): native Windows 11 style, virtualized model/view, native drag and drop |
| Tags           | mutagen for all reading and writing; tinytag 2.0.0 as a test-only dependency for parity |
| Index          | SQLite (stdlib) in WAL mode, FTS5 for search |
| FS watching    | watchdog |
| Sort order     | natsort `os_sort_keygen()` (Explorer order) |
| Recycle Bin    | send2trash |
| HTTP           | urllib (as in the script) |
| Env            | uv + pyproject.toml (hatchling, `src/` layout) |
| Tests and lint | pytest, pytest-qt, ruff |
| Packaging      | PyInstaller one-folder (`--windowed`) + Inno Setup installer, with rsgain bundled |

Rejected options:
- Electron or Tauri with a web UI: a Python sidecar or a rewrite, and browser drag and drop has no full paths.
- C#/WinUI: a full rewrite.
- Tkinter or Flet: weak large-tree virtualization.

Licenses are in SPEC §13.

Commands (from the project root):

    uv sync
    uv run python -m music_desk
    uv run pytest                 # add -m "not slow" to skip rsgain integration tests
    uv run ruff check .
    uv run pyinstaller --noconfirm packaging/music_desk.spec    # -> dist/MusicDesk/MusicDesk.exe

## Project layout
    music_desk/             C:\Code\music_desk
      CLAUDE.md, pyproject.toml
      music_tools.py, seek_lyrics.py   the original scripts (not part of the app; frozen into tests/reference/)
      docs/SPEC.md          detailed feature spec
      src/music_desk/
        __main__.py, app.py
        paths.py            resource_path(), rsgain_path(), app data and logs dirs (%LOCALAPPDATA%\MusicDesk)
        logging_setup.py    rotating app.log, plus stderr when not frozen
        settings.py         typed settings, JSON persistence
        core/               (no Qt)
          formats.py        the SPEC §1 table: extensions, tag family, mutagen class, capabilities
          tags.py           one adapter per tag family: editable fields, ReplayGain values, lyrics, cover
          rename.py         rename step: plan / apply, junk rules, sidecars
          replaygain.py     ReplayGain step, RunLog, discography
          rsgain.py         subprocess wrapper (cwd, CREATE_NO_WINDOW, below-normal priority, cancel)
          lyrics.py         LRCLIB client, matching, lookups, embed
          playlists.py      temporary .m3u8 writer (and the M7 playlist model later)
          fsops.py          rename validation, natural sort key, open in Explorer, open with default app, Recycle Bin
          index/            schema.py, scanner.py, store.py, search.py
        services/           (Qt glue: QObject workers and signals)
          indexer.py, watcher.py, lyrics_service.py, jobs.py, tag_writer.py, path_locks.py, db_writer.py
        ui/
          main_window.py, sidebar.py, folder_tree.py, file_view.py, address_bar.py, search_bar.py,
          status_bar.py, jobs_panel.py, settings_dialog.py
          side_panel/       panel.py, metadata_tab.py, lyrics_tab.py, cover_widget.py, folder_summary.py
          tools/            tools_page.py, rename_card.py, replaygain_card.py, rename_preview.py, rg_report.py
          theme/
      resources/rsgain/     rsgain.exe 3.6 + DLLs + presets + licenses (the only copy left)
      resources/icons/
      tests/                reference/ (frozen original scripts), fixtures/ (+ make_fixtures.py), core/, ui/
      packaging/            music_desk.spec, launcher.py (PyInstaller entry), installer.iss

## Progress
Tick tasks as they're done (`[x]`). A milestone is complete when its "Done when" holds.

**M0 Skeleton** (read SPEC §13 and §16)
- [x] Copy rsgain 3.6 into `resources/rsgain/` (`--version` runs)
- [x] Ask Hugo about installing uv; create `pyproject.toml`:
  - runtime dependencies: PySide6, mutagen, watchdog, natsort, send2trash;
  - dev dependencies: pytest, pytest-qt, ruff, pyinstaller, tinytag==2.0.0.
- [x] `src/` layout, `python -m music_desk` opens an empty main window
- [x] `paths.py` (`resource_path`, `app_data_dir`), logging setup
- [x] ruff + pytest configured, one smoke test
- [x] `packaging/music_desk.spec`; the built app logs the bundled rsgain version

Done when: the built exe in `dist/` starts, shows a window, and reports the rsgain version from its own bundle.

**M1 Core port** (read SPEC §1, §9, §10, §14, §17 and §18)
- [ ] Freeze the root `music_tools.py` and `seek_lyrics.py` into `tests/reference/`
- [ ] `tests/fixtures/make_fixtures.py` (ffmpeg) and the generated fixtures, including the cleanup cases. Missing
      formats go in Open questions.
- [ ] `core/formats.py` and round-trip tests per format
- [ ] `core/tags.py` family adapters: fields, ReplayGain, lyrics, cover. Verify rsgain's ReplayGain keys per format.
- [ ] `core/rename.py`: plan, apply, validate, junk protections, .cue and .lrc handling, sidecar renames, plus
      parity tests
- [ ] `core/rsgain.py` + `core/replaygain.py`: parity tests on RunLog text, plus slow tests with the real rsgain
- [ ] `core/lyrics.py`: LRCLIB client and lookups, mocked HTTP tests
- [ ] `core/playlists.py`: temporary .m3u8 writer

Done when: pytest passes, including FLAC/MP3 parity against the reference scripts and round trips for every format
with a fixture.

**M2 Index and browser** (read SPEC §2, §3, §4, §5, §14 and §16)
- [ ] DB schema, DbWriter, read connection
- [ ] Roots: picker, drop, remove, offline roots
- [ ] Scanner phases A and B, incremental, on-screen folder first
- [ ] Watcher, plus revalidation on open as a fallback
- [ ] Folder tree (lazy, natural order) and file list (columns, sort, persisted layout, Ctrl+F filter)
- [ ] Address bar, history, keyboard navigation
- [ ] Context menus: Open with default app, Play folder/selection, Open in Explorer, Copy path. Drag-out disabled.

Done when: a copied 10k+ file tree browses fluidly while it is being indexed, and folders reopen instantly from
the index.

**M3 Metadata side panel** (read SPEC §1, §7 and §11)
- [ ] Metadata tab for a single file, every format
- [ ] TagWriter and path locks
- [ ] Multi-file editing (per-track fields disabled)
- [ ] Cover display, replace and remove
- [ ] Folder summary
- [ ] Import from a .lrc sidecar
- [ ] Inline rename of files and folders, sidecars included, with Undo; the rename hint after a save

Done when: every format with a fixture round-trips through the UI, and ID3 versions are preserved.

**M4 Lyrics** (read SPEC §8 and §9)
- [ ] LyricsService: priority queue, 4 workers, circuit breaker, offline mode
- [ ] Lyrics tab, every state
- [ ] Save to file tags, and the auto-embed setting
- [ ] `lyrics_cache.json` import
- [ ] Lyrics tag and LRCLIB columns, status bar progress

Done when: every tab state is reachable with mocked LRCLIB, and a real run on a small copied folder works.

**M5 Tools** (read SPEC §10 and §13)
- [ ] Tools page and cards (Rename, ReplayGain, Tidy), options, validation
- [ ] Rename preview and apply: kept files with reasons, sidecar renames, junk files to the Recycle Bin
- [ ] ReplayGain album and discography jobs (one album at a time), cancel, reports
- [ ] Jobs panel, locks, index reconciliation

Done when: the app's `replaygain.log` matches a script run on the same copied album (timestamps masked), and every
cleanup fixture case gets the expected status.

**M6 Polish** (read SPEC §5, §6, §12 and §13)
- [ ] Library search (FTS5, filters, scope), Play results
- [ ] Smart views
- [ ] Settings dialog
- [ ] Theme and icons
- [ ] Inno Setup installer; check the licenses of the FFmpeg libraries built into rsgain

Done when: the installer installs the app, and it runs from Program Files with the bundled rsgain.

**M7 Playlists (after v1)** (read SPEC §15)
- [ ] Settle the design with Hugo (SPEC §15 and Open questions), then break it into tasks here.

## Conventions
- Python 3.13, type hints, ruff (line length 120), dataclasses for values, `pathlib` in new code.
- Match the scripts' style: short docstrings that explain why, and plain, specific user messages that say what
  happens to which files.
- `logging` writes to `%LOCALAPPDATA%\MusicDesk\logs\app.log`.

## Open questions
1. App name and icon: deferred by Hugo. "MusicDesk" is the working name until then.
2. Retiring the root `music_tools.py` and `seek_lyrics.py`: deferred by Hugo. They stay, and the app never deletes them.
3. At M1: small sample files for the formats ffmpeg can't encode: APE, TAK, Musepack, OptimFROG, DSF/DFF, and
   possibly Speex.
4. ReplayGain for the formats rsgain can't handle (OptimFROG, TTA, DSF/DFF, M4B)?
   - It would mean bundling ffmpeg (about 100 MB) to measure loudness, and its results differ slightly from
     rsgain's.
   - Default: no. Those files are listed as skipped.
5. Before M7: where saved playlists go, and whether they use absolute or relative paths.
