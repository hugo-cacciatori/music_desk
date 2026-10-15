# MusicDesk: CLAUDE.md

MusicDesk (working name) is a Windows desktop app for browsing a music library, editing tags, tidying albums
(renaming from tags, ReplayGain with the bundled rsgain) and looking up lyrics in the background. It replaces
`music_tools.py` and `seek_lyrics.py`, which sit at the project root (`C:\Code\music_desk\`).

This file is the working summary: state, rules, decisions, progress and open questions. The detailed feature spec
is in [docs/SPEC.md](docs/SPEC.md), referred to as "SPEC §n". It isn't loaded automatically: before working on a
milestone, read the SPEC sections listed under it in Progress. A new session must be able to continue from these
two files alone.

## Resume here
**Current state (2026-10-15, end of session 8):**
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

**Next step:** M6 task 1: library search (FTS5, filters, scope), Play results, with seek_lyrics' search
parity. Read SPEC §6 in full first: "Preserved from seek_lyrics" (the behaviour that must not be lost) and
"Implementation notes (M6)" (the schema v3 trigram `search` with an `lrclib` column, `record_lyrics` and the scanner
filling it, `core/index/search.py` `text_search`, the snippet, and the tests). Also read SPEC §2 (search,
schema v3) and §14 (Play results).
- The groundwork comes first: the schema v3 migration with its test, then the `lrclib` column kept up to date,
  then `text_search` with the parity test against the script's haystack rule.
- Then the Ctrl+K UI:
  - a flat result list with a Folder column (up to 5,000 rows, plus the total);
  - the field filters and the script's chips (All, With lyrics, Missing) and artist filter;
  - the snippet column and the highlighted matches;
  - the scope toggle; Enter goes to the file; Esc returns;
  - selecting a result shows its lyrics in the Lyrics tab;
  - Play results.
- It may be worth splitting into two commits (the index and search core, then the UI).

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
- ffmpeg is installed at `C:\Program Files\ffmpeg\ffmpeg-master-latest-win64-gpl-shared\bin\ffmpeg.exe`. It is
  only for regenerating test fixtures (SPEC §18). The app never needs it, because rsgain has its decoders built in
  (SPEC §13).
- git repo initialized 2026-10-15 (branch `main`). Pushed to the public GitHub repo
  https://github.com/hugo-cacciatori/music_desk on 2026-10-15 (created with `gh`, logged in as hugo-cacciatori).
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
| 2026-10-15 | No songs.html export (its search box survives in library search: see the songs.html search row below). |
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
| 2026-10-15 | Roots: never nested; adding a parent merges the roots inside it, keeping their index; a folder inside a root returns INSIDE_ROOT (the app will navigate there once the browser exists). Removing a root asks for confirmation (it costs a rescan). Offline = the root folder isn't reachable, checked at start and every 30 s off the UI thread. Dropped files get a toast offering their folder. Details in SPEC §3. |
| 2026-10-15 | Scanner: vanished entries are deleted per folder during reconciliation (cascade) instead of by a final `scan_gen` sweep, which also works for one-folder scans; `scan_gen` is still stamped on inserted and updated rows. An unreachable root or unlistable folder never loses its index. Links and junctions aren't followed. `folder_stats` count whole subtrees. Search rows are keyed by entry id (schema v2). Details in SPEC §4. |
| 2026-10-15 | Watcher: one watchdog observer over the online roots; per-folder coalescing with a 0.5 s debounce and a 5 s cap; jobs carry paths resolved when they run; new or moved-in folders are scanned recursively. Every folder open triggers a revalidation (stale-while-revalidate), which is also the fallback when a root can't be watched. Details in SPEC §4. |
| 2026-10-15 | Browser: the tree and list read the index on the UI's read connection (instant), update in place on indexer signals, and reset only when another folder opens. Folders stay first in both sort directions. Folder rows show their subtree's track count (Title column) and total duration. Layout persists through `QHeaderView.saveState` in settings.json. When the open folder disappears, its nearest surviving ancestor opens. Settings live in `settings.py` (typed, JSON). Details in SPEC §5. |
| 2026-10-15 | Navigation: the breadcrumb starts at the root path (one segment), and the levels above a root aren't navigable. Back and forward are Alt+←/→ and the mouse buttons; Backspace means Up, and only in the list and the tree. Up selects the folder you came from. A typed path to a file opens its folder and selects it. Details in SPEC §5. |
| 2026-10-15 | Context menus show only what exists: Rename, Tidy and ReplayGain items arrive with their milestones. Shell hand-offs (default app, Explorer, playlist writing) run on a worker thread, and a failure becomes a toast with an Explorer fallback. Play lists are built from the index, not by reading files. "Open in File Explorer" opens a folder itself, and shows a file selected in its folder. M2 is complete. Details in SPEC §5 and §14. |
| 2026-10-15 | Metadata tab: tag reads and saves run on one worker thread (TagIO); only changed fields are written; Cancel in the unsaved-edits prompt keeps the selection, the folder and the window. Reads that aren't saved open files with FILE_SHARE_DELETE, so the app never stops a delete or rename in Explorer. The format line shows bit depth only for lossless audio. Details in SPEC §7. |
| 2026-10-15 | Locks: one shared `PathLocks` (in core, no Qt). Jobs lock folder trees, saves lock single files, and overlaps are refused at once (no waiting) with the owner's name. TagWriter writes serially. A save updates the index for that one file and adjusts the folder totals by deltas. Details in SPEC §7 and §16. |
| 2026-10-15 | Multi-file editing: only shared fields that were edited or cleared are written. Emptying a "(multiple values)" field by hand leaves it alone, and only the clear button clears it on all files. A batch save reports per-file failures and keeps the edits for a retry. Any job-locked file makes the multi-edit read-only. Details in SPEC §7. |
| 2026-10-15 | Covers: Replace (picker or a dropped jpg/png) and Remove change only the embedded front cover, and other pictures stay. A change is pending until Save, and Revert drops it. Over 1 MB asks first. With several files, a change applies to all, and the header says whether they share one cover. Folder images are shown, never written. Details in SPEC §7. |
| 2026-10-15 | Folder summary: shown for one selected folder, or for the open folder when nothing is selected. It is built from the index (subtree totals) plus one cover read on the worker thread: the folder image, else the first track's embedded cover. Tool shortcuts wait for M5, and LRCLIB coverage waits for M4. Details in SPEC §7. |
| 2026-10-15 | .lrc import: any same-stem .lrc (ignoring case) is offered while the Lyrics field is empty, for one file. Its text is decoded as UTF-8, else Windows-1252 (never an error). The .lrc file itself is never modified. Details in SPEC §7. |
| 2026-10-15 | Inline rename: on disk off the UI thread under a path lock, then the index is updated in place (ids kept). Reconcile also matches renames by kind, extension, size and mtime, so outside renames keep ids and tags. Roots can't be renamed. Changing the extension asks first. The hint uses the Rename tool's default masks. Tests may never open a real dialog. Details in SPEC §11. |
| 2026-10-15 | The repo is on GitHub, public: hugo-cacciatori/music_desk. Push only when Hugo asks. |
| 2026-10-15 | LyricsService: three tiers (selected track, open folder, background folder by folder), with one lookup per key. Its state lives only in the `lyrics` table, so a restart resumes. A failed key waits 10 minutes, unless it is selected. The breaker pauses 10 minutes, and Retry now ends it. Offline makes no call. Lookups start at the first indexer idle. UI tests build offline libraries, and a global guard blocks urllib's opener in every test. Details in SPEC §9. |
| 2026-10-15 | Lyrics tab: it reads only the index (the file's lyrics tag comes from the indexed search text), so selecting a track reads no file. Found and instrumental entries show even offline. Retry now looks the one track up again whatever its cache says, and ends the breaker's pause. A lookup error shows over an older cached not_found. Web searches are launched on the shell thread. Details in SPEC §8. |
| 2026-10-15 | seek_lyrics' songs.html search is a parity requirement of library search: free text is a case-insensitive substring match over title, artist, album and lyrics (LRCLIB and tags), accent-folded. There is one Ctrl+K search, and the Lyrics tab is the detail pane. The plan is a trigram FTS5 index (schema v3, with an `lrclib` column) and a `LIKE` fallback under 3 characters, all built at M6. Details in SPEC §6. |
| 2026-10-15 | Saving lyrics into tags: one path, `TagWriter.embed_lyrics` (the save lock and index catch-up as any save). Save to file tags asks before replacing, and is refused while the Metadata tab has unsaved edits on the file. Auto-save, like the script's `--embed`, covers every found track, earlier ones included (a backfill at start and when turned on), but only fills empty tags (checked in the file). Locked files are skipped, and each file is tried once per session. Until the Settings dialog, a Lyrics menu holds Offline mode and Auto-save. Details in SPEC §8 and §9. |
| 2026-10-15 | lyrics_cache.json import: only at a root's top, when the root is added or merged, before its scan. The file is read-only. Merge rule per key: lyrics or instrumental beat not_found; otherwise the newer `checked` wins, and a tie keeps the index's. Entries the script never wrote (errors, malformed ones) are skipped and counted. Details in SPEC §9. |
| 2026-10-15 | LRCLIB column: "no tags" for tracks that can't be looked up, blank for unreadable files, and "offline" or "error" over "pending" from the service's state. The pending count for the status bar is kept by the lyrics service on its own connection (counted at refills, decremented per result), never on the UI thread. The status bar button is Pause, Resume or Retry now. Details in SPEC §5 and §9. |
| 2026-10-15 | Tools page: it replaces the file list in the centre, with Back; opening another folder returns to the list. Scope: one folder, else the selected music files (Rename only), else the open folder. ReplayGain gets its own validation (rsgain's target range, threshold, margin, lowercase keywords, log name, comment). Card options are saved on every change, and a well-typed invalid saved value is kept for validation to show. Details in SPEC §10. |
| 2026-10-15 | Rename tool run: the preview page replaces the Tools page. Junk asks "Rename and recycle", "Rename only" or Cancel. The folder tree is locked ("the Rename tool") for the run, and a conflict is refused at once. The result uses the script's summary wording (parity-tested), with "sent to the Recycle Bin" for "deleted". The index follows through in-place renames (ids kept), plus a rescan of folders that lost junk. Details in SPEC §10. |
| 2026-10-15 | ReplayGain tool: a preview from the file system (album folders, keyword subfolders, skipped formats, a command line too long), then a job holding the folder lock. Live lines are batched; the report shows each album's RunLog as written, plus the discography table. After the job, the index rescans the folder recursively. Tidy is the rename preview, then ReplayGain started automatically after Apply, even with nothing to rename (no ReplayGain preview, as in the script). Details in SPEC §10. |
| 2026-10-15 | Jobs panel: a bottom dock listing this session's jobs (not saved), with Open, Stop and Clear finished. Jobs go on while you browse, and their pages reopen from the panel. Each tool runs one job at a time: a preview on a busy tool shows the running job instead, since a new preview would otherwise cancel it. Rename and ReplayGain may run together on different folders, with the locks refusing an overlap. Tidy is one job. Quitting with a running job asks, then stops it. M5 is complete. Details in SPEC §10. |
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
- [ ] Library search (FTS5, filters, scope), Play results, with seek_lyrics search parity: read SPEC §6
      "Preserved from seek_lyrics" and "Implementation notes (M6)" first
- [ ] Smart views
- [ ] Settings dialog
- [ ] Theme and icons
- [ ] Inno Setup installer; check the licenses of the FFmpeg libraries built into rsgain

Done when:
- the installer installs the app, and it runs from Program Files with the bundled rsgain;
- the seek_lyrics search parity test (SPEC §6) passes.

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
