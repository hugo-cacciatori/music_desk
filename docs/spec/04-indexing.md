# 4. Indexing
Indexing runs in the background.
- Phase A, structure: a breadth-first `os.scandir` walk, so the top levels appear first. Inserts go in batches of
  about 500 entries or every 200 ms. The tree is browsable straight away.
- Phase B, tags: mutagen reads new or changed audio files (by size and mtime) in a thread pool of 4 to 8 workers.
  If profiling shows GIL contention, switch to a process pool. Results reach the DB writer in batches. The same
  pass notes whether a same-stem `.lrc` sidecar exists.
- Priority: the folder on screen is scanned and tag-read first.
- Rescans are incremental: at startup, F5 (current folder) and, until 0.8.4, File › Rescan library (every root,
  `LibraryService.rescan_all`, added in 0.2.1). Entries that disappeared are removed during each folder's
  reconciliation (as built below).
- Live updates: watchdog events are debounced (500 ms) and coalesced per folder, then that folder is reconciled.
  Where the watcher fails (network or some removable drives), a folder is revalidated each time it is opened.
- Stale-while-revalidate: opening a folder shows its indexed content at once, then a quick scandir of that one
  folder reconciles any differences without a model reset.
- As built (M2 task 3, `core/index/scanner.py`, `services/indexer.py`):
  - Removal is per folder: each folder's listing is diffed with its indexed children, and vanished entries are
    deleted with their subtrees (cascade). This also works for one-folder scans. `scan_gen` is stamped on
    inserted and updated rows.
  - A file's tags are re-read when its size or mtime differ from `tracks.read_size`/`read_mtime`. An unreadable
    file gets a `tracks` row with `read_error`.
  - An unreachable root isn't scanned, and a folder that can't be listed keeps its index. Junctions and symbolic
    links aren't followed.
  - `folder_stats` count the folder's whole subtree, and are recomputed for the root after each scan, together
    with the `.lrc` sidecar flags.
  - `search` rows use `rowid` = `entries.id` (schema v2), and a delete trigger keeps them in step.
  - One Indexer thread runs one scan at a time from a queue, with a 4-thread tag-reader pool. The on-screen folder
    is served inside a running scan through `request_folder`. Progress is throttled to 10 updates a second.
  - Every root is rescanned at startup and when it comes back online.
  - Fixed before M7 (2026-10-15): whatever the user asks for comes first, not only the folder on screen. F5 and
    Rescan (`request_folder`), a tool's results (`request_path`, recursive over the tool's folder), renames and
    tag saves jump the queue, and during a running root scan their folders are served inside it. Before, they
    waited behind the startup rescan of every root, which can take minutes on a large library, so F5 seemed to
    do nothing (`tests/ui/test_refresh.py` holds a root scan running to check it).
- As built (M2 task 4, `services/watcher.py`):
  - There is one watchdog observer. LibraryService keeps its watches equal to the online roots.
  - Events are coalesced per folder, and flushed after 0.5 s without events or at the latest after 5 s, so a
    long copy shows up while it runs. A folder already covered by a recursive scan in the same flush is
    dropped.
  - A file event reconciles its folder, one level. A created or moved-in folder also gets a recursive scan, and
    the source folder of a move is reconciled too.
  - Paths are resolved to index entries when the job runs, so a path that isn't indexed yet scans its nearest
    indexed ancestor's subtree.
  - A root that can't be watched gets a status message, and relies on revalidation on open
    (`LibraryService.folder_opened`, called on every folder open).
  - Watching doesn't lock folders: they can still be renamed in Explorer. An outside rename is seen as a delete
    plus a create, so that subtree's tags are read again. Renames made in the app will update the index
    directly (§11).
