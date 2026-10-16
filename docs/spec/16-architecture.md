# 16. Architecture and concurrency
    UI thread (Qt) ◄── queued signals ── services (QObjects in worker threads)
       │ read-only SQLite connection
       ▼
    SQLite (WAL) ◄── DbWriter thread (single writer, queue)
      Indexer:       scandir walker → tag-reader pool → DbWriter
      Watcher:       watchdog → debounce → Indexer.reconcile(folder)
      LyricsService: priority queue → 4 LRCLIB workers → DbWriter (+ TagWriter if auto-embed)
      JobRunner:     one thread per job → core.rename / core.replaygain (one rsgain at a time) / core.cue split
                     (one flac at a time, §22) → progress signals
      TagWriter:     serial writes with per-path locks; jobs take the same locks for their folders

- Threads talk to the UI only through queued signals. Progress signals are throttled to at most 10 per second.
- Models:
  - `FolderTreeModel` (QAbstractItemModel) loads lazily with `canFetchMore`/`fetchMore` from the DB.
  - `FileListModel` (QAbstractTableModel) plus a proxy with precomputed natural-sort keys.
  - Incremental changes use `dataChanged`/`rowsInserted`/`rowsRemoved`, never a full reset, so selection and
    scroll stay put.
- Icons come from a small fixed set (folder, audio, other: Qt's standard icons), never QFileIconProvider, which is
  too slow for big folders.
- Every long task takes a `CancelToken`.
- On shutdown, workers stop gracefully, the DB writer flushes, and a running rsgain is killed, with its job marked
  cancelled.

**Controllers** (M11 task 2, added by Hugo on 2026-10-16; not urgent)
- At 0.2.10 `ui/main_window.py` is 2,043 lines: it owns the window, the browsing, and the wiring of every
  feature (each tool's preview, job and report; the split; Delete; the search page). Each new feature makes it
  longer.
- The plan: one controller per feature, each a `QObject` that gets the window's shared parts (library, settings,
  toast, status bar, shell thread, jobs list, the centre stack) and owns its pages, runner and signals:
  - `ui/controllers/tools.py`: the Tools page, Rename, ReplayGain and Tidy (previews, jobs, reports);
  - `ui/controllers/split.py`: the Split page (§22), its save, its job and report;
  - `ui/controllers/delete.py`: `delete_entries`, its checks, question and refresh;
  - `ui/controllers/search.py`: the search page and its service.
- `MainWindow` keeps its layout, navigation, selection, the side panel and the menus, and delegates. Menu
  entries call the controllers.
- Nothing visible changes. The `ask_*` / `confirm_*` methods tests replace move with their controller, so the
  tests that replace them change only where they reach (for example `window.split.ask_…`).

## Project layout
As of 0.2.10 (moved from CLAUDE.md on 2026-10-16).

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
