# 9. Lyrics background service
- Starts by itself once tags are indexed, covers every audio file under the roots, and resumes across restarts
  (its state is in the DB).
- Per track:
  1. No artist or title in the tags, even after the script's "Artist - Title" file-name fallback: missing_tags.
  2. A valid cache entry exists (found or instrumental, or not_found checked less than 30 days ago): use it.
  3. Otherwise, look the track up on LRCLIB. An existing lyrics tag doesn't skip the lookup; this is the deliberate
     change from the script.
- LRCLIB rules, identical to the script:
  - `/api/get` first, when album and duration are known; then `/api/search` filtered on duration ±3 s and
    normalized artist containment, preferring synced, then plain.
  - Retries: 429/503 honour Retry-After, up to 10 times. 5xx and network errors get 4 attempts with exponential
    backoff and jitter. Timeout 30 s.
  - 4 concurrent lookups. Tracks with the same key are looked up once.
  - Only artist, title, album and duration are sent. User-Agent: `MusicDesk/<version> (personal music library app)`.
- Circuit breaker: after 10 failures in a row, lookups pause and the status bar says "LRCLIB unreachable,
  retrying in 10 min", with a Retry now button. This replaces the script's stop-the-run.
- Errors are not cached, so they are retried later. Not-found results are retried after 30 days.
- Priority order: the selected track, then the visible folder, then the rest folder by folder.
- Controls:
  - Status bar progress with pause and resume.
    - As built (M4 task 5): a lyrics label and a button next to the indexing label:
      - "Looking up lyrics: N to look up" [Pause];
      - "Lyrics paused: N to look up" [Resume];
      - "LRCLIB unreachable, retrying at HH:MM" [Retry now];
      - "Lyrics: offline mode";
      - "Lyrics: N to retry later" (only failed keys are left);
      - hidden when nothing is left.
    - N is the count of pending keys. The service counts it on its own connection at each background refill
      (`lyrics_index.pending_count`), and it goes down with each result. The UI thread never counts.
      `LyricsProgress.pending` carries it.
    - Pause is for this session only (not saved).
  - Settings: **Offline mode** (`--offline`: no LRCLIB calls), and **Automatically save found lyrics into tags**
    (`--embed`, off by default). Auto-save only fills an empty lyrics tag and never replaces one. Writes go
    through TagWriter.
- As built in core (M1, `core/lyrics.py`): `read_track`, `LrclibClient` (injectable opener, sleep and jitter),
  `fetch_lyrics`, `lookup(track, cache, client, offline=, today=)` over a `LyricsCache` protocol (a dict until the
  `lyrics` table exists), `is_fresh`, `CircuitBreaker` (counting only; the service owns the pause and the timed
  retry), `embed_lyrics(path, entry, only_if_empty=)` returning WRITTEN, SAME_TEXT, HAS_OTHER_LYRICS,
  NOTHING_TO_EMBED or NOT_EMBEDDABLE, and `search_urls` (encoded like JS `encodeURIComponent`). Tracks with the
  same key are deduplicated by the service, not by `lookup`.
- As built (M4 task 2): `LyricsService.retry(entry_id)`, `last_error(key)`, `is_pending(key)`, `resume_at`.
- As built (M4 task 1):
  - `core/index/lyrics_index.py`:
    - `pending_tracks` lists the tracks whose key has no entry or only a stale not_found, one per key, folder by
      folder. The `Track` is rebuilt from the index with the scanner's file-name fallback.
    - `record_lyrics` stores an entry, moves `n_lrclib_found` up to the root when the key becomes found (or stops
      being found), and returns the folders that hold the key.
  - `services/lyrics_service.py` `LyricsService`:
    - 4 worker threads, each with its own read connection, share a three-tier queue (selected track, open folder,
      background batches of 200 from the index). A key in flight or already done isn't looked up twice.
    - Errors aren't cached: a failed key sits out 10 minutes, except when it is the selected track.
    - The breaker pauses every lookup for 10 minutes (`unreachable`). `retry_now()` ends the pause and clears
      the failed keys.
    - Offline makes no call and writes nothing. `pause()`/`resume()` are there for the status bar (task 5).
    - Signals: `entryReady`, `foldersChanged`, `progress`, `unreachable`, `resumed`.
  - `LibraryService` owns it:
    - `start()` on the indexer's idle, `wake()` after each scan;
    - offline from `Settings.lyrics_offline`;
    - stopped before the indexer and the writer.
  - The window:
    - opening a folder prioritizes it, and selecting one audio file prioritizes that track;
    - `foldersChanged` refreshes the open folder's list;
    - a trip shows a toast, "LRCLIB unreachable, retrying in 10 min." with Retry now.
  - `LrclibClient` resolves urllib's opener at call time, so the tests' global network guard covers it. UI tests
    build their libraries offline.
- `record_lyrics` also writes the LRCLIB plain text into `search.lrclib` for every track with the key (NULL when
  the key stops being found), and scanned tracks take it from the `lyrics` table (§2, "As built (M6 task 1)").
- As built (M4 task 3), **auto-save** (`services/lyrics_embedder.py` `AutoEmbedder`, owned by the window):
  - Its toggles are the **Lyrics** menu's "Offline mode" and "Automatically save found lyrics into tags", saved
    in settings.json (there is no Settings dialog, §12).
  - When it is on:
    - every track whose key is found and whose Lyrics tag is empty in the index gets the lyrics (synced first). It
      covers tracks found earlier too, as the script's `--embed` did for every found song: a backfill runs when
      the setting turns on, and at start, in batches of 50;
    - each new FOUND result fills that key's empty tags (`lyrics_index.embed_candidates`).
  - Writes go through `TagWriter.embed_lyrics(only_if_empty=True)`. The file itself is checked, so a tag the index
    thought empty is never replaced. Job-locked files are skipped.
  - Each file is tried once per session; a failure is logged, and tried again at the next start.
  - The status bar says "Saved LRCLIB lyrics into the tags of N files."
  - Unsaved Metadata edits aren't at risk: a Metadata save writes only the fields that were edited.
- When a root is added, a `lyrics_cache.json` at its top (the script's cache) is imported into the DB cache. The
  file itself is left untouched.
- As built (M4 task 4, `core/index/lyrics_import.py`):
  - **When:** `LibraryService` runs `import_cache(writer, root path)` on its worker thread for each ADDED or MERGED
    root, before that root's scan (the scan's folder totals then count the results). It then wakes the lyrics
    service.
  - **Reading:** the file is read through `open_shared`, up to 256 MB, as UTF-8 JSON (a BOM is allowed), and
    never written.
  - **Entries:** the script's keys are used as-is (casefolded; the same `cache_key` over the same fallbacks).
    Only the script's shapes are taken: found (needs plain or synced lyrics; `source` defaults to "lrclib"),
    instrumental, not_found, each with an ISO `checked` date. Errors and anything else are skipped and counted
    as invalid.
  - **Merge rule, per key:**
    - lyrics or instrumental beat not_found, whatever the dates;
    - otherwise the newer `checked` wins, and a tie keeps the index's entry.
    So an import never loses a better or newer result. Winners go through `record_lyrics`, so the folder totals
    follow.
  - **Status bar:**
    - "Imported N lyrics lookups from lyrics_cache.json in <root>." (`LibraryService.notice`);
    - "... had nothing newer than the library's lyrics.";
    - "... couldn't be imported: <reason>" (`failed`).
  - Only a root's top folder is looked at, and only when the root is added (as specified). A cache in a subfolder,
    or added to an existing root later, isn't imported.
