# MusicDesk: CLAUDE.md

MusicDesk (working name) is a Windows desktop app for browsing a music library, editing tags, tidying albums
(renaming from tags, ReplayGain with the bundled rsgain) and looking up lyrics in the background. It replaces
`music_tools.py` and `seek_lyrics.py`, which sit at the project root (`C:\Code\music_desk\`).

This file is the working summary: state, rules, decisions, progress and open questions. The detailed feature spec
is in [docs/SPEC.md](docs/SPEC.md), referred to as "SPEC §n". It isn't loaded automatically: before working on a
milestone, read the SPEC sections listed under it in Progress. A new session must be able to continue from these
two files alone.

## Resume here
**Current state (2026-10-15, end of session 6):**
- **M2 in progress** (task 1 done: DB schema, DbWriter, read connection):
  - `core/index/schema.py`: schema v1 = the SPEC §2 tables (`roots`, `entries`, `tracks`, `lyrics`,
    `folder_stats`, FTS5 `search` with `unicode61 remove_diacritics 2`). `MIGRATIONS[n]` takes v`n` to v`n+1`,
    applied in one transaction and recorded in `PRAGMA user_version`. A newer DB raises `SchemaError`, untouched.
  - A root's top folder is an `entries` row with `parent_id NULL` and `name` = the root's full path. A partial
    unique index allows one per root. Siblings are unique ignoring case, and deletes cascade.
  - `core/index/store.py`:
    - `DbWriter(path)` is plain Python (no Qt): one thread, one transaction per `submit(fn) -> Future`. A failing
      job is rolled back alone, and `stop()` flushes the queue.
    - `connect_read(path)` is read-only (`mode=ro` + `query_only`) and version-checked.
    - `path_of(conn, id)` uses a recursive CTE.
    - `LyricsStore(read_conn, writer)` implements `core.lyrics.LyricsCache` over the `lyrics` table with the
      script's per-status entry shape.
  - `paths.library_db_path()`. The Qt `services/db_writer.py` wrapper is deferred to the indexer task.
  - 321 tests pass, 8 are skipped, the 21 `slow` tests pass, and ruff is clean.
- The spec is complete and approved (`docs/SPEC.md`).
- **M0 is complete.** `src/music_desk/` has `paths.py`, `logging_setup.py`, `core/rsgain.py` (only the
  `--version` self-check), an empty `MainWindow` whose status bar shows the rsgain version, and empty `services/`
  and `ui/` packages. `uv run pyinstaller --noconfirm packaging/music_desk.spec` builds `dist/MusicDesk/`
  (about 104 MB); the built exe logs `rsgain 3.6` from its own bundle.
- **M1 is complete** (its "Done when" holds: full pytest, slow rsgain tests included, passes):
  - git repo (local, branch `main`, no remote), one commit per finished task. `.gitattributes` keeps
    `tests/fixtures/generated/` and `tests/reference/` byte-exact (git's autocrlf is on).
  - The scripts are frozen in `tests/reference/` (hashes identical to the root copies).
  - `tests/fixtures/make_fixtures.py` generates 49 deterministic files into `tests/fixtures/generated/`:
    `formats/` (one tagged tone per format ffmpeg can encode), `album/Fixture Artist - Fixture Album/` (rename
    cases plus lite/bonus/CD2), `cleanup/` (single-file album .cue, per-track and missing-file .cue, .lrc with and
    without a lyrics tag, replaygain.log, folder.jpg, .m3u, .nfo/.sfv/.txt), `edge/` (FLAC/MP3/M4A with "03/12",
    full dates, padded/blank text, legacy keys, back+front covers, an iTunNORM COMM and a TXXX "comment"), and
    `cover.jpg`. `tests/conftest.py` has `fixtures_dir`, `fixture_copy` (copies into `tmp_path`) and
    `reference_music_tools` / `reference_seek_lyrics` (the frozen scripts imported as modules).
  - `core/formats.py` holds the SPEC §1 table (`FORMATS`, `format_for`, `open_audio`, `AUDIO_EXTENSIONS`,
    `REPLAYGAIN_EXTENSIONS`). Every format with a fixture round-trips title, lyrics and cover through mutagen with
    no downgrade needed; ID3v2.3 is preserved.
  - rsgain's ReplayGain keys per format are verified and recorded in SPEC §1.
  - `core/tags.py`: `TaggedFile(path)` with `fields()`/`set_fields()` (only named fields change, None clears),
    `rename_fields()` (TinyTag-equivalent, for rename masks), `get_replaygain`/`set_replaygain`, `set_comment`,
    `cover`/`set_cover`/`remove_cover` (front cover; other pictures kept), `stream_info()`, `save()` (keeps the ID3
    version). One adapter per family: Vorbis, ID3, MP4, APEv2, ASF, and none (raw .aac: writes raise `TagError`).
    The later modules (rename, replaygain, lyrics, Metadata tab) go through it, never through mutagen directly.
  - Parity: the script's get_tag/set_tag/set_comment and read_fields(TinyTag) agree with `tags.py`; the three
    deliberate differences are in SPEC §17.
  - `core/rename.py`:
    - `RenameOptions`, and `validate(options)`: the script's checks plus the SPEC §10 refusals.
    - `plan_renames(folder, options, selection=None) -> RenamePlan` is the dry run. It simulates the script's
      walk order, so "name taken" is identical, and it handles case-only renames, the single-file-album .cue
      (kept, audio left alone), junk, kept .lrc files and sidecar renames, plus cue warnings when cleanup is off.
    - `apply_plan(plan, recycle=fsops.send_to_recycle_bin, cancel=None) -> RenameReport` re-checks each item
      (changed source or newly taken target: skipped) and never deletes.
    - New `core/fsops.py` (`send_to_recycle_bin`, `is_same_file`) and `core/cancel.py` (`CancelToken`).
    - Tests never use the real Recycle Bin (they inject a recorder). The parity tests patch the reference
      module's globals and never let it delete: renames are compared with its junk list emptied, junk lists with
      DRY_RUN.
  - `core/rsgain.py`: `scan(files, folder, target, exe, cancel=, on_line=) -> ScanResult`.
    - It runs rsgain with `cwd=folder` and relative names, CREATE_NO_WINDOW and below-normal priority.
    - Reader threads stream its lines; a cancel kills it.
    - A command line over 32,000 characters isn't started.
  - `core/replaygain.py`:
    - `ReplayGainOptions` and `RunLog` (the script's report text).
    - `process_album(folder, ...)` and `process_discography(root, ...)`, with an injectable `scanner`, `exe`,
      `cancel`, `reporter` (Reporter protocol) and `now` clock.
    - Every script step is ported one-to-one through `TaggedFile`.
    - The as-built cancel, crash, unsupported-format and too-long rules are in SPEC §10B.
  - **ReplayGain parity is proven early** (M5's "Done when", at core level): with the real rsgain on
    `replaygain/Fixture Artist/` twins, the app's tags and every `replaygain.log` (album logs plus the
    discography summary) match the script's, with timestamps, durations and paths masked.
  - New fixtures `replaygain/Fixture Artist/`:
    - Album A: a tone plus FLAC/MP3 spikes that clip after gain, with lite/bonus/CD2 subfolders.
    - Album B: a loud FLAC, an ID3v2.3 MP3, and a `Scans/` folder without audio.

    59 fixture files in all. `tests/tagdump.py` is shared by the parity tests.
  - `core/lyrics.py` (port of seek_lyrics' lookup logic):
    - `LyricsOptions` (the script's constants; User-Agent `MusicDesk/<version> (personal music library app)`).
    - `read_track(path) -> Track` (artist, title, album, duration, embedded) through `TaggedFile`, with the
      script's albumartist and file-name fallbacks.
    - `LrclibClient(options, urlopen=, sleep=, jitter=)`.`get()` keeps the script's retries line for line.
      `fetch_lyrics(track, client, today)` returns the script's cache entry shape.
    - `LyricsCache` protocol + `DictCache`, `is_fresh` (30-day not_found rule), and
      `lookup(track, cache, client, offline=, today=)` (errors returned, never cached; a lyrics tag doesn't skip).
    - `CircuitBreaker` only counts failures in a row; M4's LyricsService adds the 10-minute pause.
    - `embed_lyrics(path, entry, only_if_empty=) -> EmbedResult`, and `search_urls()` for the Genius/Google buttons.
    - Tests use `tests/core/lrclib_fake.py` (scripted urlopen); an autouse guard fails any real network call.
      Parity: helpers, `fetch_lyrics` (URLs, sleeps, entries) and `read_song` match the frozen script.
  - `core/playlists.py`: `PlaylistEntry`, `entry_for`, `folder_tracks(folder, recursive)` (natural order, files
    then subfolders), `write_temp_playlist(entries, directory=paths.playlists_dir(), keep=20)` (UTF-8 `.m3u8`, CRLF,
    `play-<timestamp>.m3u8`, prunes only `play-*.m3u8`). New `fsops.natural_key` and `paths.playlists_dir()`.

**Next step:** M2's second task: roots (picker, drop, remove, offline roots). Read SPEC §3, §2 and §16 first.
- Core: add, remove and list roots over `DbWriter` + `connect_read`. A folder inside an existing root only
  navigates there, and adding a parent of existing roots merges them. Offline detection is a root path that
  doesn't exist; the root stays browsable.
- UI: "Add folder…", a drop overlay on the main window ("Add to library"; dropped files get a toast offering their
  folder), and "Remove from library" (index only). pytest-qt tests.
- **Not yet:** the scanner (task 3). Adding a root inserts only its `roots` row and top entry.

Don't start it until Hugo says so.

**Session protocol:**
1. Read this file, then the SPEC sections for the current milestone. Pick up the first unchecked task in Progress.
2. Hugo's global rule: before running anything destructive (deleting files, etc.), tell him the exact command first.
3. Commit each finished task on `main` (local repo; don't add a remote or push unless Hugo asks). Don't install
   global tools without asking. Project dependencies go in the
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
- git repo initialized 2026-10-15 (branch `main`, no remote).
- The venv has mutagen 1.48.1 (newer than the global 1.47.0). Parity tests run the reference scripts inside the
  venv, so both sides use the same mutagen.
- ffmpeg's build has no libspeex encoder, so there is no Speex fixture.

**Sources:**
- `music_tools.py` and `seek_lyrics.py` at the project root: the code to port. Their frozen copies are in
  `tests/reference/` (since 2026-10-15); never edit those. Ruff excludes both locations, and pytest doesn't
  collect `reference/` or `fixtures/`.
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
| 2026-10-15 | git repo created (Hugo's go). Local only, one commit per finished task. |
| 2026-10-15 | Formats ffmpeg can't encode (APE, TAK, Musepack, OptimFROG, DSF/DFF, Speex) get skipped tests until Hugo supplies samples. |
| 2026-10-15 | Fixtures are tagged by `make_fixtures.py` with raw per-family mutagen writes, never through app code, so they stay an independent oracle. |
| 2026-10-15 | `tags.py` deliberate differences from the script (SPEC §17): Vorbis ReplayGain keys written in uppercase, ID3 version kept on save, WMA disc read. Rename text fields are *not* stripped, to match TinyTag 2.0.0 and the script exactly (SPEC §17 wording corrected). |
| 2026-10-15 | Rename preview = the script's *real* run, simulated in walk order (the script's `--dry-run` could differ). A `.lrc` follows only when exactly one audio file has its stem. Selection runs never recycle junk. Details in SPEC §10. |
| 2026-10-15 | ReplayGain core: a cancelled album → STOPPED + warning (later albums CANCELLED, no leveling); a crash is logged as CRASHED, not re-raised; unscannable formats are listed as warnings; an over-long command line isn't run. Details in SPEC §10B. |
| 2026-10-15 | Lyrics core: the HTTP opener, sleep, jitter and clock are injected; the cache is a `LyricsCache` protocol (dict now, SQLite in M2/M4); `lookup` returns errors without caching; the circuit breaker counts in core and pauses/retries in M4's service. Details in SPEC §9. |
| 2026-10-15 | Temporary playlists: `play-<YYYYMMDD-HHMMSS>[-n].m3u8`, UTF-8 with CRLF, `#EXTINF` label "Artist - Title", else the title, else the file stem, and -1 seconds when unknown. Pruning keeps the newest 20 by name and touches only `play-*.m3u8`. Details in SPEC §14. |
| 2026-10-15 | Index: the DbWriter lives in `core/index/store.py` with no Qt (one thread, one transaction per job, futures); the Qt service wrapper comes with the indexer. Root top folders are `entries` rows with `parent_id NULL` named by the full root path. Schema migrations go through `PRAGMA user_version`; a newer DB is refused untouched. Details in SPEC §2. |
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
      tests/                conftest.py, reference/ (frozen original scripts), fixtures/ (make_fixtures.py +
                            generated/), core/, ui/
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
- [x] Freeze the root `music_tools.py` and `seek_lyrics.py` into `tests/reference/`
- [x] `tests/fixtures/make_fixtures.py` (ffmpeg) and the generated fixtures, including the cleanup cases. Missing
      formats go in Open questions.
- [x] `core/formats.py` and round-trip tests per format
- [x] `core/tags.py` family adapters: fields, ReplayGain, lyrics, cover. (rsgain's ReplayGain keys per format are
      verified: SPEC §1 and `tests/core/test_rsgain_keys.py`.)
- [x] `core/rename.py`: plan, apply, validate, junk protections, .cue and .lrc handling, sidecar renames, plus
      parity tests
- [x] `core/rsgain.py` + `core/replaygain.py`: parity tests on RunLog text, plus slow tests with the real rsgain
- [x] `core/lyrics.py`: LRCLIB client and lookups, mocked HTTP tests
- [x] `core/playlists.py`: temporary .m3u8 writer

Done when: pytest passes, including FLAC/MP3 parity against the reference scripts and round trips for every format
with a fixture.

**M2 Index and browser** (read SPEC §2, §3, §4, §5, §14 and §16)
- [x] DB schema, DbWriter, read connection
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
3. Small sample files for the formats ffmpeg can't encode: APE, TAK, Musepack, OptimFROG, DSF/DFF and Speex.
   Their tests are skipped until then (decided 2026-10-15). Drop them in `tests/fixtures/samples/`, and a session
   wires them into the round-trip and rsgain-key tests.
4. ReplayGain for the formats rsgain can't handle (OptimFROG, TTA, DSF/DFF, M4B)?
   - It would mean bundling ffmpeg (about 100 MB) to measure loudness, and its results differ slightly from
     rsgain's.
   - Default: no. Those files are listed as skipped.
5. Before M7: where saved playlists go, and whether they use absolute or relative paths.
