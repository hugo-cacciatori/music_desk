# 16. Architecture and concurrency
    UI thread (Qt) ◄── queued signals ── services (QObjects in worker threads)
       │ read-only SQLite connection
       ▼
    SQLite (WAL) ◄── DbWriter thread (single writer, queue)
      Indexer:       scandir walker → tag-reader pool → DbWriter
      Watcher:       watchdog → debounce → Indexer.reconcile(folder)
      LyricsService: priority queue → 4 LRCLIB workers → DbWriter (+ TagWriter if auto-embed)
      JobRunner:     one thread per job → core.rename / core.replaygain (one rsgain at a time) → progress signals
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
