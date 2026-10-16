# MusicDesk: build history

Moved verbatim from CLAUDE.md on 2026-10-16 (sessions 1–11, M0–M7), to keep CLAUDE.md small. Not loaded
automatically: read it only to find why or how something was built. Newest first. The spec (`spec/`) and
the decisions log (`DECISIONS.md`) hold what is current; git history holds the rest.

## Session notes

**Session 11, last (2026-10-16): the SPEC review, version 0.2.1.** Hugo asked for a full check of `docs/SPEC.md`
against the code. Every milestone was built; these gaps were found and fixed:
- the ReplayGain card still said the report "is appended" to replaygain.log (it replaces it since 0.1.x);
- WMA's track total wasn't disabled (a save failed instead): `metadata_tab.WMA_TRACK_TOTAL`, also for a
  multi-selection holding a .wma (`test_wma_has_no_track_total`);
- File › Rescan library was missing: `LibraryService.rescan_all`, `MainWindow.rescan_library` /
  `rescan_library_action` (`test_rescan_library_rescans_every_root`);
- the event-loop stall log of §18 was missing: `ui/stall_watch.py` `StallWatch`, on when running from source
  (`tests/ui/test_stall_watch.py`).
Outdated SPEC lines were brought in line (§1, §2, §4, §5 menus, §10 B and the junk list, §16 icons, §18). Pushed, and
published as the GitHub release v0.2.1 with `MusicDesk-Setup-0.2.1.exe` (Hugo's go, 2026-10-16). 941 tests pass, ruff is clean.

**Current state (2026-10-16, end of session 11): M7 is complete, and so is the project as planned (M0–M7).**
Version 0.2.0 is on `main`, pushed, and published as the GitHub release v0.2.0 with
`MusicDesk-Setup-0.2.0.exe` (Hugo's go, 2026-10-16). The README has the playlist builder. M7's "Done when" was
checked end to end with the real window, a scratch `LOCALAPPDATA` and a copied fixture library (two folders
selected in the tree landed in natural order; drag, Ctrl+Down and a header sort reordered them; the relative
.m3u8 export's 21 paths all resolve; the playlist and panel came back after a restart). 938 tests pass (slow ones included), ruff is clean. Left to Hugo: opening an
exported playlist in a real player, and a mouse drag in the installed app.

**Session 11 (2026-10-16), later: M7 task 4 is done (export and play), version 0.1.2.** Details in SPEC §15,
"As built (M7 task 4)": the panel's Play, Export… and Relative paths (`PlaylistPanel.entries`, `play_button`,
`export_button`, `relative_paths`, `playRequested`, `exportRequested`); `MainWindow.play_playlist`,
`export_playlist`, `choose_export_file` (replaced in tests), `playlistExported`; `Settings.playlist_relative`
and `playlist_export_dir`. Ground rule 1 now lists the export. Tests: `tests/ui/test_playlist_export.py`. 938
tests pass (slow ones included), ruff is clean. Not rebuilt, not pushed.

**Session 11 (2026-10-16): M7 task 3 is done (drag and drop), version 0.1.1.** Details in SPEC §15, "As built
(M7 task 3)": `ui/entry_drag.py` (`ENTRY_IDS_MIME`, `entry_mime`, `entry_ids`, `EntryDragView`), the tree's
multi-selection (`FolderTreeView.selected_ids`), `PlaylistModel.dropMimeData` / `add_entries` / `added` /
`remove_rows` / `move_rows` / `move_rows_to` / `clear`, `PlaylistPanel.move_selected` / `remove_selected` /
`clear` / `ask_clear` / `clear_button`, `MainWindow.show_playlist_added`. Tests: `tests/ui/test_playlist_dnd.py`,
and `test_no_drag_out` rewritten (entry ids only, copy only). Hugo's versioning from now on: a version bump after
each step, 0.1.1 (this task), 0.1.2 (task 4), 0.2.0 (task 5, the release). The setup was not rebuilt (that is
task 5); nothing pushed. 931 tests pass (slow ones included, none skipped), ruff is clean.

**Session 10 (2026-10-16):** everything is committed and pushed (`main` = origin), the full
suite passes (923 tests, slow ones included, none skipped), ruff is clean, and the setup
`dist/installer/MusicDesk-Setup-0.1.0.exe` is rebuilt from the latest code (Hugo installs it over the old one; the
version is still 0.1.0, no new GitHub release). Done this session, newest first:
- **ReplayGain log and removal** (Progress, "ReplayGain: log and removal"; SPEC §10 B, §17): each run replaces
  `replaygain.log`; "Remove ReplayGain tags…" in the track, folder and search-result menus removes every
  REPLAYGAIN_* tag and the LUFS comment lines (`core/replaygain.remove_replaygain`, `lufs_comment_pattern`,
  `TaggedFile.remove_replaygain`, `TagWriter.remove_replaygain` → `replaygainRemoved`,
  `MainWindow.remove_replaygain` / `ask_remove_replaygain`). Tests: `tests/core/test_replaygain_remove.py`,
  `tests/ui/test_replaygain_remove_ui.py`.
- **Tool button names** (Progress, "Tool button names"; SPEC §10): "Run rename…", "Run ReplayGain", "Run Tidy…";
  the rename preview's "Apply renames" and "← Back to options". The cards' buttons are `run_button` /
  `runRequested` (no more `preview_button`).
- **What "Tidy" is** was explained to Hugo (he didn't remember it): the script run without flags, rename then
  ReplayGain. He kept it as is.
- **M7 task 2 is done (the panel).** Details in SPEC §15, "As built (M7 task 2)": `ui/playlist_panel.py`
  (`PlaylistPanel`, `PlaylistModel`, `PlaylistView`, `COLUMNS`); `MainWindow.set_playlist_visible`,
  `playlist_action`, `save_playlist`, `playlist_file` (None in tests without a settings path); `Settings.playlist_*`.
  Also fixed: the address bar shrinks (hiding leading folders) so a long path can't push the search field into
  the toolbar's overflow. Tests: `tests/ui/test_playlist_panel.py`, plus a navigation test. 888 tests pass (slow
  included). Checked visually with real fonts (empty placeholder, filled list).
  - **The intermittent failure** seen twice in full runs is probably `tests/ui/test_watcher.py::
    test_live_folder_changes` (a Windows `PermissionError`, seen once in a stopped `-x` run; it passes alone and in
    the saved full run). Investigate if it shows up again.
- **The ReplayGain slowness fixes are complete on this machine** (Progress, "ReplayGain slowness fixes"): a test
  now covers the page's elapsed time; the full suite passes (878, slow tests included, none skipped); the setup
  is rebuilt (`dist/installer/MusicDesk-Setup-0.1.0.exe`, same version, upgrades in place). Hugo confirmed on
  his `F:` drive: an album takes a few seconds again. Everything is pushed.

**Earlier (2026-10-15, end of session 9):**
- **M7 task 1 is done (the core).** `core/playlist_builder.py` `Builder` of `Item(entry_id, path)`: add (at a
  position, duplicates skipped), remove, clear, move_rows (Ctrl+↑/↓), move_to (drag), sort, resolve, and the
  JSON save/load. `queries.folder_entry_ids` (shared with Play folder), `track_ids_for` (dropped items → tracks,
  Explorer's order), `track_rows` → `TrackRow`. `playlists.render(relative_to=)`, `playlist_path`,
  `export_playlist`. Tests: `tests/core/test_playlist_builder.py`.
  - **Watch for:** one full non-slow run had one failure whose output wasn't kept; the rerun and a saved full run
    were clean. If it happens again, keep the output (`uv run pytest > out.txt`) to name the test.
- **The tweaks before M7 are done** (Progress, "Tweaks and fixes before M7").
- **M6 is complete.** Both "Done when" conditions hold (the installer installs the app and it runs from Program
  Files with the bundled rsgain; the seek_lyrics search parity test passes). Also done at the end of the session:
  - the folder summary's LRCLIB coverage row (planned for M4, missed until now);
  - the root `music_tools.py` and `seek_lyrics.py` deleted (`git rm`, Hugo's go; the frozen copies stay);
  - the v0.1.0 GitHub release with the setup.
- **M6 task 5 is done (the installer).** `packaging/installer.iss` and `packaging/build_installer.py`; details in
  SPEC §13, "As built (M6 task 5)". Both M6 "Done when" conditions hold. MusicDesk 0.1.0 is installed for all
  users in `C:\Program Files\MusicDesk` on Hugo's machine (Start menu shortcut). Left in M6: the README and
  retiring the root scripts.
- **The app's licence is GPL-3.0-or-later** (`LICENSE`, `pyproject.toml`, About). Every bundled third-party notice
  is shipped in `resources/licenses/` (the FDK AAC NOTICE was downloaded with Hugo's go).
- **M6 task 4 is done (theme and icons).** `packaging/make_icon.py` draws `resources/icons/music_desk.ico` (9 sizes)
  and `.png`; `paths.icon_path()`; `app.py` sets the window icon and the Windows AppUserModelID (so a run from
  source shows the app's icon in the taskbar); the PyInstaller spec embeds the icon and `packaging/version_info.txt`
  and bundles `resources/icons`. The unused `theme` setting is removed. FFmpeg's licence is checked (LGPL, plus
  Fraunhofer FDK AAC); `FFmpeg-LGPL-2.1.txt` is shipped, and `FDK-AAC-NOTICE.txt` was downloaded with Hugo's go. Tests: `tests/test_packaging.py`.
- **M6 task 3: the Settings dialog was dropped** (Hugo's review, Decisions log). In its place:
  - Help › About MusicDesk (`ui/about_dialog.py` `AboutDialog`; `MainWindow.open_about`, and `show_about`,
    which tests replace);
  - `music_desk/licenses.py` `COMPONENTS`, with the texts in `resources/licenses/` (bundled by the PyInstaller
    spec);
  - `watch_for_changes` and `tag_workers` removed from `Settings`.
  Tests: `tests/ui/test_about.py`, `tests/test_licenses.py`, `tests/test_settings.py`. The LGPL-3.0 text was
  downloaded from gnu.org (Hugo's go). Still missing from `resources/licenses/`: FFmpeg's notice (Next step).
- **M6 task 2: smart views were dropped** (Hugo's review, Decisions log). In their place, the Lyrics menu has
  three Find entries (`MainWindow.FIND_QUERIES`, `MainWindow.find(query)`): songs LRCLIB didn't find, songs
  without lyrics in their tags, and songs LRCLIB can't look up (the new `lrclib:notags` filter). Tests are in
  `tests/ui/test_lyrics_find.py`. 843 tests pass (the slow ones included), 8 are skipped, ruff is clean, and the
  app exits 0 offscreen.
- **M6 task 1 is done (library search).** Details are in SPEC §6, "As built (M6 task 1)", and §2.
  - Index: schema v3 (trigram `search` with an `lrclib` column; the v2 → v3 migration is tested).
    `record_lyrics` and `scanner._write_tracks` keep `lrclib` current.
  - `core/index/search.py`:
    - `parse_query` → `SearchQuery` (text parts, filters, errors);
    - `text_search(conn, query, scope=, chip=, error_keys=, columns=, limit=)` → `SearchResult(ids, total)`;
    - `result_rows` → `SearchRow` (a `FileRow` plus folder, snippet and highlight spans);
    - `fold` / `fold_text` / `spans` / `snippet`.
    `queries.LISTING_SELECT` and `listing_fields` are shared with `folder_listing`.
  - `services/search_service.py` `SearchService(db_path, prepare=)`: one worker, interrupt on a new query, latest
    result only. `LyricsService.error_keys()` is new.
  - `ui/search_view.py`: `SearchView`, `SearchResultsModel` (sorts itself on precomputed per-column ranks from
    `sort_keys`, no proxy; in-place updates for the same query), `HighlightDelegate`, `ElideLeftDelegate`.
    `FileListModel` now takes its `columns` from a class attribute, and `_apply(rows)` is split out of `refresh`.
  - MainWindow: the toolbar's `search_field`, Ctrl+K `open_search`, `close_search` (Esc), `go_to_result`,
    `result_menu`, `add_artist_filter`, `showing_search`, `search_limit` (tests), `search_shown` (tests wait on
    it). `_selected_item` gives the side panel either page's rows. `showing_tools` excludes the search page.
  - Found on the way: `NOT (x IN (...))` with a NULL lyrics status is NULL, so Missing dropped pending tracks
    (fixed with `coalesce`, and tested). A big regex class for combining marks cost 400 µs per lyrics text; it is
    now `str.translate` with an ASCII fast path.
  - Measured on 5,000 tracks with 1.5 KB of lyrics each: the worker takes about 1 s at the 5,000-row cap
    (matching, rows with snippets, sort keys), and the UI thread about 20 ms plus a repaint.
  - Tests: `tests/core/test_search.py`, `tests/core/test_search_parity.py` (the M6 "Done when" parity test,
    passing), `tests/ui/test_library_search.py`. The UI test file can't be named `test_search.py`: pytest
    refuses two test modules with the same name.
  - Checked visually with real fonts: the results with highlights and snippets, and the Lyrics tab beside them.
- **M5 is complete.** Its "Done when" holds:
  - the app's replaygain.log, from a job run through the window with the real rsgain, matches the script on a copy
    of the same album, timestamps masked (`tests/ui/test_jobs_panel.py`, slow);
  - every cleanup fixture case gets its expected status (`test_rename.py`, `test_rename_tool.py`).
- **M5 task 4 is done (the jobs panel).** Details are in SPEC §10, "Jobs panel", As built.
  - `services/jobs.py` `JobList` (`Job`: kind, folder, state, status, progress, reports).
  - `ui/jobs_panel.py` `JobsPanel` (Open, Stop, Clear finished; `rows()` for tests), in a bottom
    `QDockWidget` (`jobs_dock`). It is toggled by View › Jobs panel (Ctrl+J) and the status bar's `jobs_button`.
  - MainWindow:
    - `_start_job`, `show_job`, `stop_job`, `confirm_quit_with_jobs` (replaced in tests);
    - `_rename_job` / `_rg_job` track the running job of each tool;
    - Tidy moves its job from the rename step to the ReplayGain step.
  - Behaviour changes:
    - pages of running jobs can be left (`close_tools` no longer refuses);
    - `run_tool` refuses a second job on a busy tool and shows the running job;
    - `RenameRunner.running`;
    - `rg_report.short_result` gives the panel's outcome line.
  - Tests are in `tests/ui/test_jobs_panel.py`, with a `BlockingScanner` that holds a job running.
    - Found while writing them: pytest-qt closes the test's widgets before fixture finalizers run. A fixture can't
      clean up a running job before the window closes, so the test must end it, or pre-answer
      `confirm_quit_with_jobs`.
  - 764 tests pass (the slow ones included), 8 are skipped, ruff is clean, and the app exits 0 offscreen.
- **M5 task 3 is done (the ReplayGain jobs and Tidy).** Details are in SPEC §10, "As built (M5 task 3)".
  - Core (`core/replaygain.py`): `preview()` → `AlbumPreview` (files, unsupported, keyword subfolders,
    `too_long`, `warnings`), and `process_discography(on_album=)`.
  - `services/replaygain_job.py` `ReplayGainRunner(locks, scanner=, exe=)`: `preview` and `run` (raises
    `LockedError` at once), `cancel`, `close`, `running`; the signals `previewed`, `lines` (batched), `progress` and
    `finished` (`Finished`: album_logs, result, cancelled, error, nothing, log_path).
  - `ui/tools/rg_report.py` `ReplayGainView`: the previewing, preview, running, result and error states;
    `shown_rows()` and `summary_rows()` are for tests.
  - MainWindow:
    - `preview_replaygain`, `run_replaygain(request, title)`, `open_log`, `_unsaved_edits_under(folder)`
      (shared with the rename tool);
    - Tidy chains `preview_rename` → Apply → `run_replaygain` (`RenamePreview.show_planning(tidy=True)`);
    - the runner is closed on exit (it kills rsgain).
  - Tests:
    - `tests/core/rsgain_fake.py` (FakeScanner and VALUES, moved out of `test_replaygain.py`);
    - preview tests in `test_replaygain.py`;
    - `tests/ui/test_replaygain_tool.py`: album, discography, Stop, a lock conflict, the report buttons,
      nothing to do, Tidy (with and without renames), unsaved edits, and a `slow` run of the real rsgain through
      the window.
  - Checked visually with real fonts: the preview, and a discography report.
  - 756 tests pass (the slow ones included), 8 are skipped, ruff is clean, and the app exits 0 offscreen.
- **M5 task 2 is done (the rename preview and apply).** Details are in SPEC §10, "As built (M5 task 2)".
  - Core (`core/rename.py`):
    - `plan_renames(..., cancel=)` raises `PlanCancelled`;
    - `RenameReport.steps` / `recycled`, `preview_report(plan)`, `count_files`;
    - `summary_sections(report, dry_run)`: the script's wording (parity-tested), plus the app's sections.
  - `services/rename_job.py` `RenameRunner(locks, recycle=)`: `plan`, `apply` (raises `LockedError` at once),
    `cancel`, `close`, and the `planned` / `applied` signals. Tests replace `recycle`; the real Recycle Bin is
    never used.
  - `ui/tools/rename_preview.py` `RenamePreview`: the planning, preview, applying, result and error states;
    `visible_rows()` is for tests.
  - MainWindow:
    - `preview_rename`, `apply_rename`, `ask_recycle` (replaced in tests);
    - the index catches up through `apply_renames(report.steps)` and a rescan of the recycled files' folders;
    - `center` has a third page; `close_tools` won't leave a running rename; the runner is closed on exit.
  - Tests:
    - `tests/core/test_rename_preview.py`, and a summary-wording parity test in `test_rename_parity.py`;
    - `tests/ui/test_rename_tool.py` (preview, filter, apply, junk choices, the lock during the run, a lock
      conflict, changed-since-preview, a selection, back and unsaved edits, tags untouched).
  - Checked visually: the preview and result pages, with real fonts.
  - 743 tests pass, 8 are skipped, and ruff is clean.
  - **Responsiveness on this machine (2026-10-15):** `tests/ui/test_responsiveness.py` now measures about
    3.5 s of indexing and stalls of about 50–80 ms, with rare outliers (160 ms, 233 ms) that failed its 150 ms
    browsing bound once. The pre-M4 code (7e3a183, run from a worktree) measures the same today (3.4 s, about
    50–70 ms), so this is the machine, not a regression. M2's 2.1 s and 45 ms were measured in quieter
    conditions.
  - The comparison's worktree was removed afterwards (`git worktree remove`, with Hugo's go).
- **M5 task 1 is done (the Tools page and cards).** Details are in SPEC §10, "As built (M5 task 1)".
  - `ui/tools/`:
    - `tools_page.py`: `ToolsPage`, `ToolScope`, `ToolRequest`;
    - `rename_card.py`: `Card` and `RenameCard`;
    - `replaygain_card.py`: `ReplayGainCard` and `TidyCard`.
  - Core:
    - `replaygain.validate`, `TARGET_RANGE` (rsgain's −30 to −5 LUFS);
    - `options_to_json` / `options_from_json` in `rename` and `replaygain`;
    - `Settings.rename_options` / `replaygain_options`.
  - `TagIO.read_rename_fields` → `renameFields`.
  - MainWindow:
    - `center` (a QStackedWidget: the file list or the Tools page), `open_tools(ids)`, `tool_scope(ids)`,
      `close_tools`, `showing_tools`;
    - the toolbar's Tools action, "Tools…" in the menus, and the folder summary's "Tools…";
    - `run_tool` is a placeholder toast until tasks 2–3.
  - The page was checked visually with real fonts, rendered off screen (an offscreen grab only shows boxes): it
    wraps cleanly from about 480 px.
  - **Fix found on the way (M4 code):** turning auto-save off didn't stop a backfill queued just before. That
    backfill could still fill an empty Lyrics tag, which raced the Lyrics tab's own save (a flaky window test).
    `AutoEmbedder._find` now checks the setting before and after reading the index. A test failed on the old code
    and passes now.
  - Tests: `tests/core/test_tool_options.py` and `tests/ui/test_tools_page.py`; the menu tests list "Tools…".
  - 729 tests pass (the slow ones included), 8 are skipped, ruff is clean, and the app exits 0 offscreen.
  - **Watch for:** after the fix, one full run had one unidentified failure; its output wasn't saved. Three
    further full runs with saved output were clean. If a full run fails again, keep the output
    (`uv run pytest > out.txt`) to name the test.
- **M4 is complete.** Its "Done when" holds:
  - every Lyrics tab state is reachable with mocked LRCLIB (`tests/ui/test_lyrics_tab.py`);
  - a real run on a copied folder works (Hugo's go, 2026-10-15).

  **The real run:**
  - It used the app's own `LibraryService` and `MainWindow`, offscreen, with the real LRCLIB client, on a scratch
    copy of `tests/fixtures/generated/album`. The app data was in a scratch `LOCALAPPDATA`.
  - Two 0.2 s tones were tagged Radiohead "Karma Police" and Queen "Bohemian Rhapsody". At 0 s, LRCLIB's duration
    filter doesn't apply.
  - Both came back found with synced lyrics (LRCLIB #36841208 and #36902193). The LRCLIB column showed "synced",
    and the Lyrics tab showed the lyrics and the source line. The 9 fixture keys came back not_found, as
    expected.
  - Three runs, 17–31 s each:
    - In the first, 3 of the 9 fixture keys ended as lookup errors. The status bar said "Lyrics: 3 to retry later",
      the intended behaviour. The log file wasn't on for that run, so the cause is unknown; most likely transient
      LRCLIB failures.
    - The second and third runs (the third with the log file on) had no errors.
  - The script is not in the repo (it was in the session's scratchpad). It is easy to recreate from this
    description if needed.
- **M4 task 5 is done (the lyrics columns and status bar progress). M4 waits only for its real run** (Next step).
  - `FileRow.lyrics_key`. `queries._lrclib_state` gives "no tags" (no key), blank (unreadable), or the index
    state. `FileListModel.lrclib_status` and `refresh_column` show "offline" or "error" for pending rows from
    `LyricsService.status_for(key)`.
  - `LyricsProgress.pending`: the service counts pending keys at each refill (`lyrics_index.pending_count`) and
    decrements per result.
  - The status bar's `lyrics_label` and `lyrics_button` (Pause, Resume, Retry now) are driven by
    `MainWindow.show_lyrics_progress`.
  - Tests are in `tests/ui/test_lyrics_status.py`, plus additions to the core and service tests. 694 tests pass, 8
    are skipped, ruff is clean, and the app exits 0 offscreen.
- **M4 task 4 is done (the lyrics_cache.json import):**
  - `core/index/lyrics_import.py`:
    - `import_cache(writer, folder) -> ImportReport | None`, with `read_cache` and `merge_cache`;
    - `better(new, old)` is the merge rule: lyrics or instrumental beat not_found; otherwise the newer `checked`
      wins, and a tie keeps the index's;
    - invalid entries are skipped and counted.
  - `LibraryService._import_lyrics_cache` runs for ADDED/MERGED roots before their scan, then wakes the lyrics
    service. The new `LibraryService.notice` signal gives the status bar message.
  - Tests (`tests/core/test_lyrics_import.py`) build the cache with the frozen script's own `scan`, `cache_key`
    and `save_cache`, so the keys and the format are the script's. They show the imported keys cover every
    indexed track. 688 tests pass, 8 are skipped, and ruff is clean.
- **M4 task 3 is done (Save to file tags, and auto-save):**
  - `TagWriter.embed_lyrics(items, only_if_empty=)` → `lyricsEmbedded(LyricsEmbedded)`, around
    `core.lyrics.embed_lyrics`, under the save lock; `on_written` follows each WRITTEN file. It is callable from
    any thread (tokens are locked).
  - The Lyrics tab:
    - a "Save to file tags" button in the found state, disabled with the reason as a tooltip (same text, no
      tags, a job's lock, saving);
    - `confirm_replace_lyrics` (replaced in tests) before replacing different lyrics;
    - `can_write` (set by SidePanel) refuses while the Metadata tab has unsaved edits on that file;
    - the result shows in `save_status`.
  - SidePanel reloads a clean Metadata tab after lyrics are written into its file. `LyricsInfo.can_tag` is new.
  - `services/lyrics_embedder.py` `AutoEmbedder(db_path, lyrics, writer, enabled=, batch=50)`:
    - a backfill of the found tracks with empty tags (`lyrics_index.embed_candidates`) when it is turned on, and
      at start;
    - the key's tracks on each FOUND result;
    - each file tried once per session.
  - MainWindow: a **Lyrics** menu (Offline mode, "Automatically save found lyrics into tags") writes `Settings`
    and drives the service and the embedder. The embedder closes before the TagWriter.
  - Tests are in `tests/ui/test_lyrics_save.py`. 671 tests pass (the slow ones included), 8 are skipped, and ruff
    is clean. The app starts and exits 0 offscreen.
- **Spec (docs only, no code):** seek_lyrics' songs.html search box (substring over title, artist, album and
  lyrics) is now an explicit parity requirement of library search (SPEC §6, "Preserved from seek_lyrics"). The
  index groundwork for it (schema v3 trigram `search` with an `lrclib` column, `record_lyrics` indexing the text,
  and `core/index/search.py`) is written down in SPEC §6, "Implementation notes (M6)". It waits for M6, since
  nothing is lost meanwhile.
- **M4 task 2 is done (the Lyrics tab):**
  - `ui/side_panel/lyrics_tab.py` `LyricsTab(conn, service)` covers every SPEC §8 state (`tab.state`
    constants), from the index only (`lyrics_index.lyrics_info`), and follows `entryReady` and state changes.
  - Found: plain lyrics, a "Show timestamps" toggle for synced ones, "LRCLIB #id · checked <date>", Copy, and a
    note comparing the file's lyrics tag.
  - Genius/Google emit `urlRequested`, which `MainWindow.open_url` launches on the shell thread.
  - Retry now calls the new `LyricsService.retry(entry_id)`: it bypasses a fresh cache entry, ends the breaker's
    pause, and the tab shows looking up until the result arrives.
  - `SelectedItem.entry_id` is new. `SidePanel(..., lyrics_service=)` shows one track, or "Select a single
    track…" for several items.
  - Also changed: `search_urls` skips a missing artist or title; `store.read_lyrics`; `lyrics_index.track_for_entry`.
  - The scripted LRCLIB (`ScriptedLrclib`, `found_for`, `unreachable`, `collect`) moved to
    `tests/core/lrclib_fake.py`. The tests are in `tests/ui/test_lyrics_tab.py`.
  - 661 tests pass (the slow ones included), 8 are skipped, and ruff is clean. The app starts and exits 0
    offscreen.
- **M4 task 1 is done (LyricsService):**
  - `core/index/lyrics_index.py`:
    - `pending_tracks(conn, retry_before, folder_id=, entry_id=, skip=, limit=)`: one track per pending key,
      folder by folder;
    - `record_lyrics(conn, key, entry) -> folders`: the upsert plus `n_lrclib_found` deltas through the
      scanner's now-public `adjust_folder_stats`;
    - `retry_before`.
    `store.upsert_lyrics` is shared with `LyricsStore.put`.
  - `services/lyrics_service.py` `LyricsService`:
    - 4 workers with a three-tier queue (selected track, open folder, background batches), dedupe by key, and
      10-minute cooldowns for keys that failed;
    - the circuit breaker pause (`unreachable`, `retry_now`, auto-resume, `resumed`);
    - offline (no calls); `pause`/`resume`;
    - signals: `entryReady(key, entry)`, `foldersChanged`, `progress(LyricsProgress)`.
  - `LibraryService.lyrics`:
    - `start()` on the indexer's idle, `wake()` on `scanFinished`, and stopped in `close()`;
    - `lyrics_client=`, `lyrics_offline=` (app.py passes `Settings.lyrics_offline`), and the
      `lyrics_offline_default` class attribute.
  - MainWindow:
    - `prioritize_folder` on open, and `prioritize_track` for one selected audio file;
    - `lyrics_updated` refreshes the open folder's list;
    - `lrclib_unreachable` shows a toast with Retry now.
  - Found and fixed: `LrclibClient`'s default opener was bound when the module was imported, so the test network
    guard never covered it. It is now resolved at call time. The guard is autouse for every test
    (`tests/conftest.py`), and the UI tests' libraries are offline (`tests/ui/conftest.py`). pytest has
    `pythonpath = ["tests/core"]` so any test can import `lrclib_fake`.
  - Tests: `tests/core/test_lyrics_index.py` and `tests/ui/test_lyrics_service.py`. In service tests, use
    `collect(signal)` and `waitUntil` rather than `qtbot.waitSignal`: a worker thread can emit while the blocker
    is being set up.
  - 646 tests pass (the slow ones included), 8 are skipped, and ruff is clean. The app starts and exits 0
    offscreen.
- **M3 is complete** (its "Done when" holds: every fixture format round-trips through the UI, and ID3v2.3 is kept):
  - `core/metadata.py`: `read_snapshot(path) -> Snapshot` (fields, stream details, RG values, the cover with its
    source, the ID3 version, `format_line` such as "FLAC 16-bit/44.1 kHz · 4:44 · 32 MB", with bit depth for
    lossless and kbps for lossy), `folder_cover` (folder.jpg, cover.jpg, front.jpg, ignoring case) and
    `write_fields(path, changes)`, which writes the changed fields only and returns the fresh snapshot.
  - `services/tag_io.py` `TagIO`: one `tag-io` worker thread for reads and saves, with a token per request (a
    stale result is ignored). Covers are decoded into a scaled QImage there.
  - `ui/side_panel/panel.py` `SidePanel` (Metadata tab, plus a Lyrics placeholder) shows messages for nothing
    selected, a folder, several items or a non-audio file.
  - `ui/side_panel/metadata_tab.py` `MetadataTab`:
    - the cover header, editable fields (validated numbers, multi-line lyrics), and read-only details with
      the Loudness block;
    - `changes()` holds only the differences; an empty field clears it, and CRLF lyrics count as unchanged;
    - the dirty marker ("Metadata •"), Ctrl+S, and Esc to revert;
    - a save error is shown with the fields kept dirty, and raw .aac is read-only with a note;
    - `can_leave()` asks Save, Discard or Cancel (`ask_leave`, replaced in tests).
  - MainWindow:
    - three panes (tree, list, side panel); the panel follows `FileView.selectionChanged`;
    - Cancel restores the previous selection, and blocks a folder change or closing the window;
    - after a save, `Indexer.scan_path(folder)` updates the list row;
    - closing waits for a save in progress.
  - Found and fixed: reading tags or a folder image held the file open, so Windows refused a delete or rename in
    Explorer at that moment. `fsops.open_shared` (CreateFileW with FILE_SHARE_DELETE, through `open(…, opener=)`,
    so mutagen still sees the file name, which it needs for raw AAC) is now used by `TaggedFile(path, shared=True)`
    for every read that isn't saved: the scanner, the side panel and folder covers.
  - TagWriter and locks (task 2):
    - `core/path_locks.py` `PathLocks`:
      - `acquire(paths, owner)` / `release(token)` / `lock(...)` (a context manager) / `owner(path)`;
      - a folder lock covers its tree, paths compare ignoring case, overlapping locks are refused with
        `LockedError(path, owner)`;
      - listeners run after every change, and a broken listener can't break a lock.
      `LibraryService.locks` is the shared instance; M5's jobs must take it for their folders.
    - `services/tag_writer.py` `TagWriter(locks, on_written)`:
      - every tag save goes through it, one at a time on its own thread;
      - each save holds a short lock on its file (owner "a tag save", which `locked_by` ignores);
      - a job's lock refuses the save with "<file> is in use by <job>. Try again when it has finished.";
      - `locksChanged` relays lock changes to the UI.
      `TagIO` now only reads.
    - The Metadata tab makes a file under a job's lock read-only, with the tooltip and note "Read-only while
      <job> is running.", and Save disabled.
    - After a save, `Indexer.update_file(path)` runs `Scanner.update_file`: it re-reads that one file into
      `entries`, `tracks` and `search`, and adjusts `folder_stats` up to the root by the difference (the formats
      list is left to scans). There is no folder rescan, and the list row updates on `folderUpdated`.
  - Multi-file editing (task 3):
    - `TagIO.load_many(paths)` emits `loadedMany(LoadedMany(token, snapshots, failed))`.
      `TagWriter.write_many([(path, changes)])` emits `batchWritten(BatchWritten(token, saved, failed))`; a file
      that fails doesn't stop the others.
    - `MetadataTab.show_files(paths, skipped)`:
      - the shared fields (`SHARED_FIELDS`) show the common value, or a "(multiple values)" placeholder;
      - a clear button per shared field (a trailing QLineEdit action) marks "clear on all";
      - Title, Track # and Lyrics are disabled ("Edited one file at a time.");
      - the header shows "N files selected" and the format counts; the details are hidden;
      - notes list the files left out (no tags, unreadable, non-audio items in the selection).
    - `_multi_changes()` keeps only the shared fields that were edited or cleared. Emptying a "(multiple values)"
      field by hand leaves it alone, while emptying a common value clears it.
    - A partial failure shows "Saved x of n files. Not saved: …" and keeps the edits dirty for a retry. Success
      reloads the snapshots; the window shows "Saved the tags of N files."
    - Any file under a job's lock makes the whole multi-edit read-only.
    - `SidePanel.show_selection`: one item opens the single-file editor; several items edit their audio files
      together, even one audio file among folders.
  - Cover editing (task 4):
    - `metadata.write_fields` takes `changes["cover"]` (`metadata.COVER`): bytes set the embedded front cover,
      and None removes it. Other pictures are kept.
    - Also added: `embedded_cover(snapshot)` and `read_image(path)` (.jpg/.jpeg/.png only, checked by
      signature, read through `open_shared`).
    - `TagIO.read_image(path)` emits `imageRead(ImageRead)` with the bytes and the decoded QImage.
      `LoadedMany.cover` is the common embedded cover, decoded when every file has the same one.
    - In the tab:
      - `CoverLabel` accepts a dropped jpg/png (a drop into the app);
      - Replace… (`choose_cover_file`, replaced in tests) and Remove;
      - a cover over 1 MB asks first (`confirm_large_cover`);
      - the pending change is part of the dirty state, shown as "New cover (not saved yet)" or "Cover removed";
        Revert drops it;
      - Remove is enabled only when an embedded cover exists. A folder image is shown, never written.
    - Several files: the header shows "The same embedded cover on every file", "(multiple covers)" or "No
      cover"; a replace or remove applies to every file that can be tagged, and the common cover is read back
      after the save.
    - Job locks disable the cover buttons and the drop. `has_cover` follows in the index through
      `update_file`.
    - Found and fixed: a file whose tag holds blank text (edge.flac's artist is padding) made the tab dirty with
      no edit. A field now counts as changed only when its text differs from what was loaded.
  - Folder summary (task 5):
    - `queries.folder_summary(conn, id) -> FolderSummary`: `folder_stats` (the subtree), the direct subfolder and
      file counts, and `first_track` (natural order, walked level by level so a huge root stays cheap).
    - `metadata.folder_picture(folder, first_track)`: the folder image, else the first track's embedded cover.
      `TagIO.load_folder_cover` emits `folderCover(token, QImage, source)`.
    - `ui/side_panel/folder_summary.py` `FolderSummaryView`: name, path, cover with its source, Tracks,
      Duration, Formats, ReplayGain and Lyrics-tag coverage ("all 12 tracks" or "3 of 12 tracks"), "In this
      folder" (subfolders and files), and Play folder / Play with subfolders.
      - LRCLIB coverage isn't shown until M4 fills the lyrics table.
      - Tool shortcuts are left out until the tools exist (M5).
    - `SidePanel` is now a QWidget stacking `tabs` (Metadata, Lyrics) and `summary`; its tab texts are on
      `panel.tabs`. `show_folder(id)`, `refresh_folder()` and `showing_folder`.
    - MainWindow shows the summary for one selected folder row, and for the open folder when nothing is selected
      (also right after opening a folder). It refreshes on `folderUpdated` and `scanFinished`, and Play goes to
      `play_folder`.
    - Invalid images (the test library's 3-byte `cover.jpg`) give "No cover".
  - .lrc import (task 6):
    - `metadata.lrc_sidecar(path)` finds the same-stem `.lrc`, ignoring case, read through `open_shared`.
      `decode_text` tries UTF-8 (BOM dropped), else cp1252 with replacement characters, so it never fails.
    - `Snapshot.lrc_name` and `lrc_text`.
    - The tab shows an "Import from <name>.lrc" link under Lyrics: one file only, when the Lyrics field is empty
      and the file is editable and not locked.
    - The import fills the field (CRLF normalized, edge newlines stripped) and makes the tab dirty. Saving is
      the usual Save; the .lrc bytes are never touched (tested).
  - Inline rename (task 7):
    - `core/inline_rename.py`:
      - `validate_name(path, new)`: empty, the script's forbidden characters, a trailing space or dot, reserved
        names, longer than 259 characters, or a clash (case-only renames allowed);
      - `rename_entry(path, new, locks)` holds a "a rename" lock, renames the .lrc too when exactly one audio
        file has its stem (and the target is free), and returns a `RenameResult` with steps and warnings;
      - `undo(result)` goes back sidecar first. A `RenameError` carries the user message.
    - `Scanner.apply_renames(steps)` updates `entries.name`/`ext` and `search` in place, so ids are kept and a
      folder's subtree follows. It also refreshes the folder's sidecar flags. A change between audio and
      non-audio drops the track row or reads the tags, then recomputes the totals.
      `Indexer.apply_renames(steps)` queues it.
    - Reconcile now detects renames: a vanished entry and a new one with the same kind, extension, size and mtime
      (one of each) keep their row, id and tags. Explorer renames therefore no longer re-read tags, and an
      in-app rename racing a revalidation scan keeps its id.
    - UI:
      - the Name cell is editable (a slow click; F2 through a shortcut on the list), and the `RenameDelegate`
        selects only the stem;
      - tree folders are editable (F2, slow click), roots aren't;
      - "Rename\tF2" in the list menus (one file or folder) and in the tree menu (not roots).
    - `MainWindow.rename_entry(id, name)`:
      - refuses roots; changing the extension asks first (`confirm_extension_change`); unsaved tag edits ask
        first;
      - renames on the shell thread, then `renameDone`/`renameFailed`; the index is updated in place;
      - a 10 s toast (naming the .lrc) offers Undo;
      - the open folder's path and breadcrumb follow a folder rename (`_refresh_location`), and the panel
        reloads a renamed file.
    - Rename hint: `Snapshot.suggested_name` holds what the default masks give (`metadata.suggested_name`).
      After a single-file save that changes it, the tab shows 'Rename to "01 New Title.flac"?', which renames
      through `rename_path`.
    - `store.consistent_read(conn)`: `folder_summary` now reads one snapshot (it raced a root removal).
    - Tests: an autouse `no_real_dialogs` fixture in `tests/conftest.py` makes any real QMessageBox/QFileDialog
      fail the test, instead of waiting for a click (one test had opened the real extension prompt). Tests
      answer through the `confirm_*`, `ask_*` and `choose_*` methods.
  - 603 tests pass, 8 are skipped, the 23 `slow` tests pass, and ruff is clean. The app starts and exits cleanly
    offscreen. The UI tests open real windows, which flash on screen; running them offscreen
    (`QT_QPA_PLATFORM=offscreen`) is an option not tried yet, since some tests rely on window focus.
- **M2 is complete** (its "Done when" holds: measured on a copied 10k-file tree, see the end of this block):
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
  - `core/index/roots.py`: `add_root(writer, path) -> AddResult` (ADDED, MERGED, ALREADY_ROOT, INSIDE_ROOT,
    IS_A_FILE, NOT_A_FOLDER), `remove_root`, `list_roots`, `check_online`. They block (file system + writer), so
    the app calls them off the UI thread.
    - Roots never nest. Adding a parent of existing roots merges them: their top entries become ordinary folders
      under the new root (intermediate folders created), so their index is kept.
    - Adding a root inserts only its `roots` row and top entry. The scanner (task 3) fills the rest.
  - `services/library.py` `LibraryService` (Qt) owns the DbWriter and the UI's read connection, and runs root work
    on one `library-fs` worker thread. Its signals are `rootsChanged`, `addFinished(list[AddResult])` and `failed(str)`.
    It checks roots online at start and every 30 s.
  - UI:
    - `ui/sidebar.py`: the LIBRARY list (offline roots dimmed with a tooltip), "Add folder…", and a context menu
      with "Remove from library". The folder tree (task 5) grows from it.
    - `ui/toast.py`: a non-modal notice with an action button.
    - `MainWindow(library)`: File > Add folder… (Ctrl+O), the drop overlay, a toast offering a dropped file's
      folder, and confirmation before removing a root. Without a library (the index failed to open) it runs
      disabled.
    - `app.py` creates the service and closes it on quit.
  - Schema v2 (the v1 → v2 migration is tested): `search` rows are keyed by `rowid` = `entries.id`, and an
    `AFTER DELETE` trigger on `entries` removes them, cascades included.
  - `core/index/scanner.py` `Scanner(writer, workers=4, batch_rows=500, batch_seconds=0.2, on_progress=, on_folder=)`:
    - `scan_root(root_id, cancel)`: phase A is a breadth-first scandir walk that reconciles each folder against
      its indexed children (insert, update on size/mtime/name case, delete with cascade), in DbWriter batches.
      Phase B reads tags with `read_file` (through `TaggedFile`) in a thread pool, into `tracks` and `search`, for
      new or changed files only. Then come the `.lrc` sidecar flags, `folder_stats` (subtree totals) and
      `last_scan`.
    - `scan_folder(entry_id)` does one level (F5, revalidation). `request_folder(entry_id)` serves a folder
      inside a running scan.
    - Safety: an unreachable root isn't scanned, and an unlistable folder keeps its index, so an unplugged drive
      never empties the index. Junctions and symlinks aren't followed.
    - Timing on this machine: 10k entries take 1.3 s for the first scan (with 500 tag reads) and 0.15 s to
      rescan; 100k entries take 3.4 s for phase A (a `slow` test).
  - `core/lyrics.track_from_fields()` is split out of `read_track`, so the scanner reads each file once.
  - `services/indexer.py` `Indexer(writer)`: one thread, a queue of root and folder scans (no duplicates),
    `request_folder` (inside a running scan, or at the front of the queue), `cancel_root` (used before a removal),
    and `stop`. Signals: `progress` (at most 10 a second), `folderUpdated`, `scanFinished`, `idle` and `failed`.
  - `LibraryService` owns the indexer. It scans added or merged roots, rescans every root at startup and roots
    that come back online, and cancels a root's scan before removing it. `roots()` returns [] once closed (a
    queued signal may arrive after close).
  - The status bar shows "Indexing <root>: N folders, M files" and then "Reading tags in <root>: x / y", and hides
    the label when the indexer is idle.
  - Watcher (task 4):
    - `services/watcher.py` `Watcher(on_flush, debounce=0.5, max_delay=5, observer_factory=Observer)`: one
      watchdog observer, and `sync({root_id: path})` watches exactly the online roots.
    - Events are coalesced per folder and flushed after 0.5 s of quiet, or at the latest after 5 s.
    - A file event reconciles its folder (one level). A created or moved-in folder also gets a recursive scan,
      and the moved-from parent is reconciled too.
    - A watch that fails emits `unwatched(root_id, reason)`, which LibraryService shows as "Live updates are
      off…".
    - Scanner additions:
      - `scan_folder(entry_id, recursive=)`.
      - `scan_path(path, recursive=)`: resolved at run time with `find_folder`; a path not yet indexed scans its
        nearest indexed ancestor's subtree.
      - `ScanReport.folder_id`. `on_folder` now fires only for folders served from the priority queue.
    - `Indexer.scan_path(path, recursive)` jobs; `folderUpdated` is emitted for every folder and path job.
    - `LibraryService`:
      - owns the watcher, and re-syncs it after adds, removals and online changes (on its worker thread, with
        its own read connection);
      - `folder_opened(entry_id)` is the revalidation on open, which the browser (task 5) must call on every
        folder open.
    - Checked: watching locks nothing. Folders under a watched root, and the root itself, can still be renamed.
      An outside folder rename is seen as a delete plus a create, so that subtree's tags are read again.
  - Browser (task 5):
    - `settings.py` `Settings`: a typed dataclass saved to JSON (atomic replace). Unknown keys and values of the
      wrong type fall back to the defaults. It holds the SPEC §12 fields plus UI state: geometry, splitter, file
      list header state, expanded folders (as paths) and the last folder.
    - `core/index/queries.py`:
      - `child_folders(conn, id, only_music=)` in natural order, and `folder_listing(conn, id) -> list[FileRow]`
        (entries, tracks, folder_stats and lyrics joined, with RG full/partial/none, lyrics tag/lrc/none and
        LRCLIB synced/plain/instrumental/not found/pending);
      - `folder_path_ids`.
    - `ui/folder_tree.py`:
      - `FolderTreeModel` is lazy (`canFetchMore`/`fetchMore`). Updates diff children by entry id (remove,
        move, insert, dataChanged; no reset) through `refresh(id)`, `refresh_loaded()`, `set_roots()` and
        `set_only_music()`.
      - `FolderTreeView` remembers the expanded folders by path and re-expands them as rows load;
        `select_entry(ids)` reveals a folder.
    - `ui/file_view.py`:
      - `FileListModel` is reset only on a folder change, and updated in place by `refresh()`.
      - `FileSortProxy` keeps folders first in both directions, using precomputed natural keys.
      - `FileView` adds Ctrl+F filtering by name, a header menu to show or hide columns (and reset them), and
        saves and restores the header state (order, sizes, hidden columns, sort). Bitrate and Modified are
        hidden by default.
    - `MainWindow(library, settings)`:
      - The sidebar holds the tree; the file list is in the centre. View > Hide folders without music.
      - Opening a folder lists it from the index, then calls `folder_opened` (revalidation).
      - `folderUpdated` and `scanFinished` refresh the tree and the list in place.
      - When the open folder leaves the index, its nearest surviving ancestor opens, as in Explorer. This is
        checked before the tree drops the row, which would select a neighbour.
      - State is saved on close and restored at start, last folder included. `app.py` loads `Settings.load()`.
    - Robustness fixes:
      - `scan_root` and `scan_folder` return None for a root or entry that left the index.
      - UI handlers ignore signals once the library is closed (`LibraryService.closed`; the models' connection
        provider returns None).
      - `LibraryService.close()` closes the writer last, so no `-wal`/`-shm` files are left behind.
    - Smoke check: `app.main()` starts offscreen, with `LOCALAPPDATA` pointed at a scratch folder, and exits 0.
  - Navigation (task 6):
    - `ui/address_bar.py` `AddressBar(subfolders)`: a breadcrumb (the root path, then a segment per folder),
      each segment clickable, with a "›" dropdown of subfolders after it. Clicking the empty part, or Ctrl+L,
      gives a text path. Enter navigates and Esc returns; quotes around a pasted path are stripped.
    - MainWindow:
      - a Navigation toolbar with Back (Alt+←), Forward (Alt+→), Up (Alt+↑), Rescan folder (F5) and the address
        bar;
      - Backspace goes up, from the list and the tree only;
      - the mouse back/forward buttons work through an application event filter;
      - `open_folder(id, record=True)` keeps the history stacks, and back/forward skip folders that left the
        index;
      - Up selects the folder you came from;
      - `go_to_path` opens an indexed folder, or a file's folder with the file selected; otherwise it shows a
        message.
    - New queries: `folder_crumbs`, `parent_folder` and `find_child`.
    - Found: Qt's standard Back/Forward keys are already Alt+←/→ on Windows (binding them twice made them
      ambiguous, so they never fired), and they also bind Backspace. The keys are now set explicitly.
    - Qt makes the tree's first row current when the window opens, so the root folder opens at start when there
      is no last folder.
    - Shared UI fixtures moved to `tests/ui/conftest.py` (with `library_dir`, `service` and `window`; a module can
      override them), plus `tests/ui/browser_helpers.py`.
  - Context menus (task 7):
    - `core/fsops.py`: `open_with_default_app(path, launcher=os.startfile)`, `explorer_command(path, is_dir)`
      (a folder opens itself; a file uses `explorer /select,"…"`) and `open_in_explorer(path, is_dir, run=)`.
    - `queries.playlist_for_folder(conn, id, recursive)` (natural order, files then subfolders) and
      `playlist_for_ids(conn, ids)`. Both read the index only, so no file is read on the UI thread.
    - `MainWindow.list_menu(ids)`:
      - files: Open (one file), Play selection, Open in File Explorer, Copy path(s);
      - a folder: Open, Play folder, Play folder and subfolders, Open in File Explorer, Copy path, Rescan;
      - the background: the open folder's menu, without Open.
      The tree uses the same folder actions, through `Sidebar.folder_menu_provider`, plus Remove from library
      (roots only) and Add folder…. Double-click or Enter on a file opens it with the default app.
    - Launching (`os.startfile`, Explorer, writing the playlist) runs on a `shell` worker thread. An `OSError`
      shows a toast: for playlists, how to set a .m3u8 app in Windows, with "Open in File Explorer" on the
      folder or the first file instead.
    - `MainWindow.launch`, `show_in_explorer` and `playlist_dir` are replaced in tests, which never launch
      anything. The real Explorer command line hasn't been run (that would open Explorer).
    - `FileView.selected_ids()` returns the view's order. A right-click on an unselected row selects it.
    - Drag-out stays disabled: no drag flags, `NoDragDrop`, and a test covers it.
  - M2 "Done when", measured by `tests/ui/test_responsiveness.py` (`slow`) on a 500-folder copy with 10,000
    files and 1,000 tracks:
    - indexing takes 2.1 s while 56 folders are opened meanwhile, the worst open 6 ms;
    - the worst event-loop stall is 45 ms while indexing and 47 ms while browsing the indexed tree (the target
      is 50 ms);
    - reopening an indexed folder takes 3 ms (median), 4 ms at worst.
  - 446 tests pass, 8 are skipped, the 23 `slow` tests pass (rsgain, the 100k scan and the responsiveness
    check), and ruff is clean. A
    test run that takes 30 s or more is pytest deleting an old 100k-file temp tree, not a hang.
- The spec is complete and approved (`docs/SPEC.md`).
- **M0 is complete.** `src/music_desk/` has `paths.py`, `logging_setup.py`, `core/rsgain.py` (only the
  `--version` self-check), an empty `MainWindow` whose status bar shows the rsgain version, and empty `services/`
  and `ui/` packages. `uv run pyinstaller --noconfirm packaging/music_desk.spec` builds `dist/MusicDesk/`
  (about 104 MB); the built exe logs `rsgain 3.6` from its own bundle.
- **M1 is complete** (its "Done when" holds: full pytest, slow rsgain tests included, passes):
  - git repo (branch `main`, remote `origin` = github.com/hugo-cacciatori/music_desk, public), one commit per
    finished task. `.gitattributes` keeps
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

**Done in session 9 (the original M6 task 1 brief, kept for reference):** the schema v3 migration and the
`lrclib` column, `text_search` with the parity test, then the Ctrl+K UI (results with a Folder column, up to
5,000 plus the total; filters, chips and the artist filter; snippets and highlights; the scope; Enter and Esc;
the Lyrics tab as the detail pane; Play results). Two commits: the core, then the UI.

**Done in session 7 (the original task 1 brief, kept for reference):**
- `services/lyrics_service.py`:
  - one priority queue of tracks (the selected track, then the open folder, then the rest folder by folder) and
    4 worker threads running `core.lyrics.lookup` with an `LrclibClient`;
  - the cache is the `lyrics` table (`core.index.store.LyricsStore`, one read connection per worker);
  - tracks with the same `lyrics_key` are looked up once;
  - it starts once tags are indexed and resumes across restarts, since its state is the `lyrics` table (tracks
    whose `lyrics_key` has no fresh entry are pending).
- Circuit breaker: `core.lyrics.CircuitBreaker`. After 10 failures in a row, pause and retry in 10 minutes, with a
  "LRCLIB unreachable" status and Retry now. Offline mode (`Settings.lyrics_offline`): no calls at all.
- Index: after each result, the folder totals (`n_lrclib_found`) and the LRCLIB column follow, by deltas as in
  `Scanner.update_file`, or a small helper.
- Tests: mocked HTTP only (`tests/core/lrclib_fake.py`, and the autouse network guard). Priority order,
  deduplication, the breaker pausing and resuming, offline mode, and a restart resuming the pending tracks.
- All five M4 tasks are done; only the real run of "Done when" is left.

## Progress checklists (finished work plan blocks)
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
- [x] Roots: picker, drop, remove, offline roots
- [x] Scanner phases A and B, incremental, on-screen folder first
- [x] Watcher, plus revalidation on open as a fallback
- [x] Folder tree (lazy, natural order) and file list (columns, sort, persisted layout, Ctrl+F filter)
- [x] Address bar, history, keyboard navigation
- [x] Context menus: Open with default app, Play folder/selection, Open in Explorer, Copy path. Drag-out disabled.

Done when: a copied 10k+ file tree browses fluidly while it is being indexed, and folders reopen instantly from
the index.

**M3 Metadata side panel** (read SPEC §1, §7 and §11)
- [x] Metadata tab for a single file, every format
- [x] TagWriter and path locks
- [x] Multi-file editing (per-track fields disabled)
- [x] Cover display, replace and remove
- [x] Folder summary
- [x] Import from a .lrc sidecar
- [x] Inline rename of files and folders, sidecars included, with Undo; the rename hint after a save

Done when: every format with a fixture round-trips through the UI, and ID3 versions are preserved.

**M4 Lyrics** (read SPEC §8 and §9)
- [x] LyricsService: priority queue, 4 workers, circuit breaker, offline mode
- [x] Lyrics tab, every state
- [x] Save to file tags, and the auto-embed setting
- [x] `lyrics_cache.json` import
- [x] Lyrics tag and LRCLIB columns, status bar progress

Done when: every tab state is reachable with mocked LRCLIB, and a real run on a small copied folder works.

**M5 Tools** (read SPEC §10 and §13)
- [x] Tools page and cards (Rename, ReplayGain, Tidy), options, validation
- [x] Rename preview and apply: kept files with reasons, sidecar renames, junk files to the Recycle Bin
- [x] ReplayGain album and discography jobs (one album at a time), cancel, reports
- [x] Jobs panel, locks, index reconciliation

Done when: the app's `replaygain.log` matches a script run on the same copied album (timestamps masked), and every
cleanup fixture case gets the expected status.

**M6 Polish** (read SPEC §5, §6, §12 and §13)
- [x] Library search (FTS5, filters, scope), Play results, with seek_lyrics search parity: read SPEC §6
      "Preserved from seek_lyrics" and "Implementation notes (M6)" first
- [x] Smart views: dropped (Hugo, 2026-10-15); three Find entries in the Lyrics menu instead
- [x] Settings dialog: dropped (Hugo, 2026-10-15); Help › About with the versions and licences instead
- [x] Theme and icons: the app icon and the exe's version info; light and dark follow Windows (no theme setting)
- [x] Inno Setup installer; check the licenses of the FFmpeg libraries built into rsgain
- [x] README.md: what the app does, its features, install (the installer), build from source, licence
- [x] Retire the root `music_tools.py` and `seek_lyrics.py` (deleted with `git rm` on 2026-10-15, Hugo's go;
      `tests/reference/` keeps the frozen copies the parity tests use)

Done when:
- the installer installs the app, and it runs from Program Files with the bundled rsgain;
- the seek_lyrics search parity test (SPEC §6) passes.

**Tweaks and fixes before M7** (Hugo's report, 2026-10-15)
- [x] ReplayGain: the card's **Run ReplayGain** starts the job at once, with no preview page (the preview code
      is removed: `replaygain.preview`, `AlbumPreview`, `ReplayGainRunner.preview`, the page's preview table)
- [x] Refreshes come first: F5, Rescan, a tool's results, renames and tag saves jump the indexer's queue and are
      served inside a running root scan (`Indexer.request_path`, `MainWindow.refresh_after_tool`); they used to
      wait behind the startup rescan, so F5 didn't show a new `replaygain.log`
- [x] Every tool refreshes its whole folder afterwards, and a recursive rename's subfolders reach the index (the
      renames were applied as one batch tied to the first folder)

**ReplayGain slowness fixes** (Hugo's report, 2026-10-15; M7 on hold meanwhile)
- [x] Background indexing pauses while a ReplayGain job runs (`Scanner.pause/resume`, `Indexer.pause`,
      `MainWindow._pause_indexing`); requested folders are still served. Cause found: rsgain took 66 s for an
      album that takes seconds alone, while a fresh index of the same drive was being read.
- [x] The ReplayGain page shows the album's elapsed time: rsgain holds back its output when it writes into a
      pipe (all lines arrive at exit), so long albums looked frozen.
- [x] The ReplayGain card's number fields ignore the mouse wheel (`NoWheelSpinBox`).
- [x] Rebuilt the setup (2026-10-16) after a test for the elapsed time and a full green run (878 tests).
- [x] Hugo's check on his drive (2026-10-16): with the new setup, ReplayGain on an album takes a few seconds
      again. That fits the cause found (indexing read the same drive meanwhile) but doesn't prove it: the first
      indexing may simply have finished by then. Proof would be a quick album while the status bar shows
      "(paused while ReplayGain runs)".

**Tool button names** (Hugo, 2026-10-16)
- [x] The tools are run from their cards: "Run rename…", "Run ReplayGain", "Run Tidy…" ("…" = a preview to check
      comes first). The rename preview: "Rename: check the changes", "Apply renames" (Tidy: "Apply renames, then
      run ReplayGain", or "Run ReplayGain" with nothing to rename) and "← Back to options" beside it (also at the
      top; the ReplayGain page's back button says the same). No Close in the preview. SPEC §10.

**ReplayGain: log and removal** (Hugo, 2026-10-16)
- [x] Each run replaces `replaygain.log` instead of appending (a discography's leveling line and summary follow
      that run's report). Deliberate change, SPEC §17.
- [x] "Remove ReplayGain tags…" for tracks, folders (recursive) and search results: the REPLAYGAIN_* values and
      the LUFS comment lines, after a confirmation, through the TagWriter. `core/replaygain.remove_replaygain`,
      `TaggedFile.remove_replaygain`, `TagWriter.remove_replaygain`. SPEC §10 B.

**M7 Playlist builder** (read SPEC §15, designed with Hugo on 2026-10-15; also §5 and §14)
- [x] Core (`core/playlists.py`, no Qt): the builder's model (entry ids, the natural import order, insert at a
      position, skip duplicates, move, remove, sort by a field), saving and restoring it in the app's data,
      and the `.m3u8` export with relative or absolute paths (another drive falls back to absolute). Tests.
- [x] The panel (`ui/playlist_panel.py`): a narrow vertical column between the file list and the side panel,
      shown or hidden by the toolbar's checkable "Playlist builder" button and View › Playlist builder
      (Ctrl+Shift+P). Opening it takes only the file list's width. Columns with a header menu, header-click
      sort that reorders the playlist, the empty-state placeholder, the footer (count, duration), missing
      tracks dimmed.
- [x] Drag and drop (0.1.1): an internal MIME type carrying entry ids (never file URLs: a test checks it) from the folder
      tree, the file list and the search results, with their Explorer-style multi-selection. Drop to insert,
      drag to reorder, Ctrl+↑/↓ to move, Delete to remove, Clear with a confirmation.
- [x] Export to .m3u8 (0.1.2) (a Save dialog, the remembered Relative paths checkbox and folder) and Open playlist with
      default player (the §14 temporary playlist). Add the export to Ground rule 1's exceptions.
- [x] Docs, README, version 0.2.0 and a release built locally (2026-10-16; published as v0.2.0 the same day).

Done when: folders dragged from the tree (a multi-selection) land in natural order, can be reordered by drag,
by keyboard and by a header sort, and export to a relative .m3u8 that a player opens; the playlist survives a
restart.

Moved from CLAUDE.md's work plan on 2026-10-16 (finished blocks, verbatim):

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

**M10 Shared masks and cue split**, tasks 1–4 (0.2.7–0.2.10):
- [x] 1. Masks: `core/masks.py` (moved out of `rename.py`; the parity tests stay unchanged),
      `Settings.name_masks` / `name_replacement` with the migration, the File names card, and the hint using the
      user's masks.
- [x] 2. `core/cue.py`: parsing, encodings, samples, boundaries and the hidden track, names and clashes, tags;
      the split fixtures, with audio that never repeats.
- [x] 3. flac: bundle `resources/flac` (PyInstaller spec, `licenses.py`, `resources/licenses/`, About), the
      `flac --version` self-check, the runner and the proof, `slow` tests.
- [x] 4. UI: the Split card, "Split into tracks…" (menus, `.cue` preview), the preview page with its encoding
      picker, the job (lock, Stop, partial cleanup, recycling, ReplayGain afterwards) and its report.
      *Redesigned with Hugo on 2026-10-16 (SPEC §22): the card, the folder scan, the menus and the saved options
      go; tasks 5 and 6 replace them.*

**Search: names at once** (Hugo, 2026-10-16; read SPEC §6). Small, so it goes first.
- [x] 1. `text_search` finds folders and audio files by name before their tags are read; folder rows; remove the
      chips and the scope box with their wiring; "N of M results" (SPEC §6 "What is searched").

Done when: with tag reading held back (a stub reader), a file's and a folder's names are found; a lyrics word is
found through the tag and through the LRCLIB text; the page shows no chips and no scope box; the suite passes.

**Action menu** (Hugo, 2026-10-16; read SPEC §5 "Action menu").
- [x] 1. The Action menu between Lyrics and Help, with "Show tracks without ReplayGain tags" (`rg:none` through
      `MainWindow.find`); a test that it opens the search with that query.

Done when: the menu shows, and its entry lists exactly the fixture tracks without ReplayGain tags.

**M10 Shared masks and cue split** (read SPEC §21, §22, §13, and §10)
- Tasks 1–4 are done (0.2.7–0.2.10, HISTORY.md). Tasks 5 and 6 replace the 0.2.10 Split card (SPEC §22).
- [x] 5. Core: the editable sheet, `format_cue` and its round trip, Save / Save as, planning with ticked tracks,
      the per-track proof (SPEC §22 "Planned").
- [x] 6. UI: remove the 0.2.10 card, menus, folder scan and `Settings.split_options`; the button on every `.cue`
      preview; the Split page; the one-album job and report (SPEC §22 "In the app", "Replaced").
- [x] 7. README, version 0.3.0 and the installer build (0.3.0 pushed 2026-10-17; the release and the
      installed-app check are not part of the task: Hugo, 2026-10-17).

Done when:
- every split fixture gets its expected result, and every fixture sheet saved unedited is byte-identical;
- the slow test proves the per-track lengths and the MD5 on the FLAC and WAV images, and fails on a one-sample
  shift; extracting only track 3 proves its length and MD5 against the source's range;
- Save sends the old `.cue` to the (injected) Recycle Bin, and is refused where Windows can't recycle.

Optional, for later (not needed for completion): the installed app, on a copied album, saves a sheet with one
start time moved and splits it with its bundled flac.

## Former CLAUDE.md sections (as of 2026-10-16)

**Session protocol:**
1. Read this file, then the SPEC sections for the current milestone. Pick up the first unchecked task in Progress.
2. Hugo's global rule: before running anything destructive (deleting files, etc.), tell him the exact command first.
3. Commit each finished task on `main`. Push to `origin` only when Hugo asks. Don't install
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
- Inno Setup 6.7.3 is installed at `C:\Program Files\Inno Setup 6\` (winget, Hugo's go, 2026-10-15), for the
  installer build only.
- ffmpeg is installed at `C:\Program Files\ffmpeg\ffmpeg-master-latest-win64-gpl-shared\bin\ffmpeg.exe`. It is
  only for regenerating test fixtures (SPEC §18). The app never needs it, because rsgain has its decoders built in
  (SPEC §13).
- git repo initialized 2026-10-15 (branch `main`). Pushed to the public GitHub repo
  https://github.com/hugo-cacciatori/music_desk on 2026-10-15 (created with `gh`, logged in as hugo-cacciatori).
- The venv has mutagen 1.48.1 (newer than the global 1.47.0). Parity tests run the reference scripts inside the
  venv, so both sides use the same mutagen.
- ffmpeg's build has no libspeex encoder, so there is no Speex fixture.

**Sources:**
- `music_tools.py` and `seek_lyrics.py`: the code that was ported. Only the frozen copies in `tests/reference/`
  remain (the root copies were deleted on 2026-10-15, with Hugo's go); never edit those. Ruff excludes
  `tests/reference`, and pytest doesn't collect `reference/` or `fixtures/`.
- The original rsgain download (`rsgain-3.6-win64/`, with the legacy `scan_library_full.py`) is no longer on disk;
  `resources/rsgain/` is the only copy. The legacy script was superseded and isn't ported.
- `music_tools.py` resolves rsgain as `<script folder>/rsgain-3.6-win64/rsgain.exe`, which doesn't exist anymore.
  Before running the script for a comparison (M5), run it on a copy whose `RSGAIN_PATH` points at
  `resources/rsgain/rsgain.exe`.
- The legacy script pointed at a library on `H:\`. Never point development runs at a real library.

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
    uv run python packaging/build_installer.py                  # both builds -> dist/installer/MusicDesk-Setup-<v>.exe
    uv run python packaging/make_icon.py                        # redraw resources/icons/ after changing the icon

## Project layout
An early version; the current one is in SPEC §16 "Project layout".

    music_desk/             C:\Code\music_desk
      CLAUDE.md, pyproject.toml, uv.lock, README.md, LICENSE (GPL-3.0)
      docs/SPEC.md          detailed feature spec
      src/music_desk/
        __main__.py, app.py   entry point: logging, the rsgain self-check, the window
        paths.py            resource_path(), rsgain_path(), icon_path(), app data and logs (%LOCALAPPDATA%\MusicDesk)
        logging_setup.py    rotating app.log, plus stderr when not frozen
        settings.py         typed settings, JSON persistence (settings.json; playlist.json sits beside it)
        licenses.py         the bundled third-party components and their licence files (Help > About)
        core/               (no Qt)
          formats.py        the SPEC §1 table: extensions, tag family, mutagen class, capabilities
          tags.py           one adapter per tag family: fields, ReplayGain values (and their removal), lyrics, cover
          metadata.py       snapshots for the Metadata tab, field writes, covers, .lrc sidecars
          rename.py         rename step: plan / apply, junk rules, sidecars
          inline_rename.py  F2 renames with their .lrc, and Undo
          replaygain.py     ReplayGain step, RunLog, discography, remove_replaygain
          rsgain.py         subprocess wrapper (cwd, CREATE_NO_WINDOW, below-normal priority, cancel)
          lyrics.py         LRCLIB client, matching, lookups, embed
          playlists.py      .m3u8 writing: temporary playlists, and the builder's export (relative or absolute)
          playlist_builder.py  the playlist builder's model (Builder, Item), JSON persistence
          fsops.py          natural sort key, shared opens, open in Explorer, default app, Recycle Bin
          path_locks.py     PathLocks: folder and file locks shared by jobs and saves
          cancel.py         CancelToken
          index/            schema.py (v3), store.py (DbWriter, read connections), scanner.py, queries.py,
                            search.py, roots.py, lyrics_index.py, lyrics_import.py
        services/           (Qt glue: QObject workers and signals)
          library.py (owns the writer, indexer, watcher, lyrics service), indexer.py, watcher.py,
          lyrics_service.py, lyrics_embedder.py, search_service.py, tag_io.py, tag_writer.py, jobs.py,
          rename_job.py, replaygain_job.py
        ui/
          main_window.py, sidebar.py, folder_tree.py, file_view.py, address_bar.py, search_view.py,
          playlist_panel.py, entry_drag.py, stall_watch.py, jobs_panel.py, about_dialog.py, toast.py
          side_panel/       panel.py, metadata_tab.py, lyrics_tab.py, folder_summary.py
          tools/            tools_page.py, rename_card.py, replaygain_card.py, rename_preview.py, rg_report.py
      resources/rsgain/     rsgain.exe 3.6 + DLLs + presets + licenses (the only copy left)
      resources/icons/      music_desk.ico / .png (drawn by packaging/make_icon.py)
      resources/licenses/   the third-party licence texts and their README
      tests/                conftest.py, reference/ (frozen original scripts), fixtures/ (make_fixtures.py +
                            generated/), core/ (with lrclib_fake.py, rsgain_fake.py), ui/ (conftest.py,
                            browser_helpers.py), test_settings.py, test_licenses.py, test_packaging.py
      packaging/            music_desk.spec, launcher.py (PyInstaller entry), installer.iss, build_installer.py,
                            make_icon.py, version_info.txt
