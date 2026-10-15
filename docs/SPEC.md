# MusicDesk: detailed spec

Companion to `../CLAUDE.md`, which holds the ground rules, decisions, progress and open questions. CLAUDE.md
points to sections here as "SPEC §n", so keep the section numbers stable. When a decision changes behavior, update
this file and the CLAUDE.md Decisions log together.

1. Formats
2. Data and storage
3. Library roots
4. Indexing
5. Browser
6. Library search
7. Side panel and Metadata tab
8. Lyrics tab
9. Lyrics background service
10. Tools
11. Inline rename
12. Settings
13. rsgain and packaging
14. Playback
15. Playlist builder (M7)
16. Architecture and concurrency
17. Porting reference
18. Testing

## 1. Formats
`core/formats.py` is the single source of truth for this table. A ✓ is the target. At M1, a round-trip test checks
each format, and a row is downgraded (for example to read-only) if mutagen can't do it.

| Extensions        | Container / tag family          | mutagen class        | Tags, lyrics, cover | ReplayGain tool |
|-------------------|---------------------------------|----------------------|---------------------|-----------------|
| .flac             | FLAC / Vorbis comments          | FLAC                 | ✓ (picture block)   | ✓ |
| .ogg .oga         | Ogg Vorbis, Ogg FLAC / Vorbis   | OggVorbis, OggFLAC   | ✓ (METADATA_BLOCK_PICTURE) | ✓ |
| .opus             | Ogg Opus / Vorbis               | OggOpus              | ✓ (METADATA_BLOCK_PICTURE) | ✓ |
| .spx              | Ogg Speex / Vorbis              | OggSpeex             | ✓                   | ✓ |
| .mp3 .mp2         | ID3v2                           | MP3                  | ✓ (APIC)            | ✓ |
| .wav              | RIFF + ID3 chunk                | WAVE                 | ✓ (APIC)            | ✓ |
| .aif .aiff .aifc  | AIFF + ID3 chunk                | AIFF                 | ✓ (APIC)            | ✓ |
| .m4a .mp4         | MP4 atoms (AAC, ALAC)           | MP4                  | ✓ (covr)            | ✓ |
| .m4b              | MP4 atoms                       | MP4                  | ✓ (covr)            | ✗ |
| .ape              | APEv2                           | MonkeysAudio         | ✓ (Cover Art (Front)) | ✓ |
| .wv               | APEv2                           | WavPack              | ✓                   | ✓ |
| .mpc              | APEv2                           | Musepack             | ✓                   | ✓ |
| .tak              | APEv2                           | TAK                  | ✓                   | ✓ |
| .ofr .ofs         | APEv2                           | OptimFROG            | ✓                   | ✗ |
| .tta              | ID3v2 / APEv2                   | TrueAudio            | ✓                   | ✗ |
| .wma              | ASF attributes                  | ASF                  | ✓ (WM/Picture)      | ✓ |
| .dsf              | ID3v2                           | DSF                  | ✓ (APIC)            | ✗ |
| .dff              | DSDIFF + ID3 chunk              | DSDIFF               | ✓                   | ✗ |
| .aac              | raw ADTS (no tag standard)      | AAC                  | stream info only    | ✗ |

- Every extension in the table counts as audio everywhere: browsing, columns, indexing, lyrics and the Rename tool.
  When the ReplayGain tool meets a format rsgain doesn't support, its preview lists those files as
  "skipped: format not supported by rsgain".
- `core/tags.py` has one adapter per tag family: Vorbis, ID3, MP4, APEv2, ASF. Keys always match ignoring case.
- Opus: keep rsgain's default `-o d`, which writes standard REPLAYGAIN_* tags and clears the header gain, so the
  post-processing works the same as for FLAC.
- ID3 formats: rsgain's default `-I keep` keeps the file's ID3v2 version, and the app's own writes do the same.
- ReplayGain keys the bundled rsgain 3.6 writes, verified on the fixtures (2026-10-15,
  `tests/core/test_rsgain_keys.py`, `slow`):
  - Vorbis (FLAC, Ogg Vorbis, Ogg FLAC, Opus): `REPLAYGAIN_TRACK_GAIN`/`_PEAK` comments (mutagen lists Vorbis keys
    in lowercase; match ignoring case). Opus gets no R128 tags with the default `-o d`.
  - ID3 (MP3, MP2, WAV, AIFF): `TXXX:REPLAYGAIN_TRACK_GAIN`/`_PEAK`, uppercase descriptions.
  - MP4 (AAC and ALAC .m4a): freeform `----:com.apple.iTunes:REPLAYGAIN_TRACK_GAIN`/`_PEAK`, UTF-8 bytes.
  - APEv2 (WavPack) and ASF (WMA): `REPLAYGAIN_TRACK_GAIN`/`_PEAK`.
  - Values: gain `"6.75 dB"` (2 decimals), peak `"0.124969"` (6 decimals).
  - rsgain refuses .m4b, .tta and raw .aac ("not of a supported type"), which confirms the ✗ marks above.
    APE, Musepack, TAK and Speex are still unverified (no fixture yet).

Field mapping:

| Field        | Vorbis                                     | ID3                 | MP4      | APEv2              | ASF               |
|--------------|--------------------------------------------|---------------------|----------|--------------------|-------------------|
| Title        | TITLE                                      | TIT2                | ©nam     | Title              | Title             |
| Artist       | ARTIST                                     | TPE1                | ©ART     | Artist             | Author            |
| Album artist | ALBUMARTIST                                | TPE2                | aART     | Album Artist       | WM/AlbumArtist    |
| Album        | ALBUM                                      | TALB                | ©alb     | Album              | WM/AlbumTitle     |
| Track/total  | TRACKNUMBER, TRACKTOTAL (read TOTALTRACKS) | TRCK "n/N"          | trkn     | Track "n/N"        | WM/TrackNumber (no total) |
| Disc/total   | DISCNUMBER, DISCTOTAL (read TOTALDISCS)    | TPOS "n/N"          | disk     | Disc "n/N"         | WM/PartOfSet "n/N" |
| Year         | DATE                                       | TDRC                | ©day     | Year               | WM/Year           |
| Genre        | GENRE                                      | TCON                | ©gen     | Genre              | WM/Genre          |
| Composer     | COMPOSER                                   | TCOM                | ©wrt     | Composer           | WM/Composer       |
| Comment      | COMMENT                                    | COMM (eng, desc "") | ©cmt     | Comment            | Description       |
| Lyrics       | LYRICS (read UNSYNCEDLYRICS)               | USLT (eng, desc "") | ©lyr     | Lyrics             | WM/Lyrics         |
| Cover        | picture block / METADATA_BLOCK_PICTURE     | APIC type 3         | covr     | Cover Art (Front)  | WM/Picture        |
| ReplayGain   | REPLAYGAIN_*                               | TXXX:REPLAYGAIN_*   | ----:com.apple.iTunes:REPLAYGAIN_* | REPLAYGAIN_* | REPLAYGAIN_* |

WMA has no standard track total, so that field is disabled for .wma, with a tooltip explaining why.

## 2. Data and storage
- `%LOCALAPPDATA%\MusicDesk\` holds:
  - `library.db` (index and lyrics cache), `settings.json`, `logs\app.log` (rotating);
  - `playlists\` (temporary .m3u8 files; the last 20 are kept).
- Nothing is stored inside music folders except tags, renames and `replaygain.log`.
- Schema sketch:
  - `roots(id, path, added_at, last_scan, online)`
  - `entries(id, root_id, parent_id, name, is_dir, ext, size, mtime, scan_gen)`, indexed on `parent_id`, with
    `UNIQUE(parent_id, name COLLATE NOCASE)`. Paths are rebuilt from parents, so renaming a folder is one row update.
  - `tracks(entry_id PK, format, tag_family, title, artist, albumartist, album, track, track_total, disc,
    disc_total, date, genre, composer, comment, has_lyrics_tag, has_lrc_sidecar, has_cover, duration, bitrate,
    sample_rate, bit_depth, channels, rg_track_gain, rg_track_peak, rg_album_gain, rg_album_peak, lyrics_key,
    read_size, read_mtime, read_error)`
  - `lyrics(key PK, status, source, lrclib_id, plain, synced, checked, error)`: the script's cache entries.
    The key is `casefold(artist + "\x1f" + title)`, and tracks join to it through `tracks.lyrics_key`.
  - `folder_stats(entry_id, n_tracks, duration, n_with_rg, n_with_lyrics_tag, n_lrclib_found, formats)`,
    recomputed after scans.
  - `search` (FTS5): name, title, artist, albumartist, album, genre, plus lyrics text (from LRCLIB and from tags).
    Until M6 it holds only the tags' lyrics (column `lyrics`, read by the Lyrics tab for its tag note).
    - Planned schema v3 (built at M6, §6 "Implementation notes"):
      - `tokenize='trigram remove_diacritics 1'`, for the substring matching of seek_lyrics' search (§6);
      - a new column `lrclib` holds the LRCLIB plain text;
      - this needs SQLite ≥ 3.45, the version bundled with Python 3.13 (3.45.3 checked).
    - Queries shorter than 3 characters can't use trigram. They fall back to `LIKE` on the worker thread, under
      the same result cap.
    - The trigram index costs about 3× the indexed text, which is accepted for the search it gives.
- One DB writer thread fed by a queue. The UI uses its own read-only connection.
- As built (M2 task 1, `core/index/`):
  - Schema version 1 is in `PRAGMA user_version`. Migrations run in one transaction, and an index from a newer
    app is refused without being touched.
  - A root's top folder is the `entries` row with `parent_id NULL`, and its `name` is the root's full path. A
    partial unique index allows one per root, because NULLs are distinct in UNIQUE.
  - `search` uses the `unicode61 remove_diacritics 2` tokenizer, so "Sigur Ros" finds "Sigur Rós". (Replaced by
    trigram with `remove_diacritics 1` in schema v3, at M6.)
  - Connections: `foreign_keys=ON` and `busy_timeout=5000`. The writer adds `journal_mode=WAL` and
    `synchronous=NORMAL`. Readers open with `mode=ro` and `query_only`, and check the schema version.
  - `DbWriter.submit(fn)` runs each job in its own `BEGIN IMMEDIATE` transaction and returns a Future. A failing
    job is rolled back alone, and `stop()` finishes the queued jobs before closing.
  - `LyricsStore` serves the `lyrics` table as the lookup cache, with the script's entry shape per status.

## 3. Library roots
- Add folders with "Add folder…" (folder picker), or drop one or more folders anywhere on the window. A drop
  overlay shows "Add to library". Dropped files are ignored, and a toast offers to add their folder instead.
- A folder inside an existing root just navigates there. Adding a parent of existing roots merges them.
- "Remove from library" removes the folder from the index only; nothing on disk changes.
- Unavailable roots (an unplugged drive) are shown dimmed as "offline". Their last index stays browsable and
  read-only, and they reconnect automatically when the drive comes back.
- As built (M2 task 2):
  - `core/index/roots.py` returns ADDED, MERGED, ALREADY_ROOT, INSIDE_ROOT, IS_A_FILE or NOT_A_FOLDER. Paths
    are compared ignoring case.
  - A merge keeps the former roots' entries: their top entries become folders under the new root, and the
    folders in between are created.
  - Adding a root writes only its `roots` row and top entry; the scanner fills the rest.
  - Every root action, including the online check, runs on the `LibraryService` worker thread. A dropped path is
    sorted (folder, file or missing) there too, so the UI never touches the file system.
  - "Remove from library" asks for confirmation, because adding the folder back means a full rescan.
  - The online check runs at start and every 30 s. An offline root is listed dimmed, with a tooltip.

## 4. Indexing
Indexing runs in the background.
- Phase A, structure: a breadth-first `os.scandir` walk, so the top levels appear first. Inserts go in batches of
  about 500 entries or every 200 ms. The tree is browsable straight away.
- Phase B, tags: mutagen reads new or changed audio files (by size and mtime) in a thread pool of 4 to 8 workers.
  If profiling shows GIL contention, switch to a process pool. Results reach the DB writer in batches. The same
  pass notes whether a same-stem `.lrc` sidecar exists.
- Priority: the folder on screen is scanned and tag-read first.
- Rescans are incremental: at startup, F5 (current folder) and "Rescan library". Entries that disappeared are
  removed using the `scan_gen` marker.
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

## 5. Browser
Explorer-like, with no copy, paste, delete or new.

    ┌──────────────────────────────────────────────────────────────────────────────────────────┐
    │ ← → ↑ ⟳   H: › Music › Radiohead › OK Computer        [Filter folder]  [⌕ Search library] │
    ├──────────────┬───────────────────────────────────────────────────┬───────────────────────┤
    │ LIBRARY      │ Name ▲              #  Title     Artist     Time RG ♪│ [Metadata] [Lyrics]   │
    │ ▸ H:\Music   │ 📁 Lite                                             │  cover                │
    │ ▾ D:\Rips    │ 🎵 01 Airbag.flac    1  Airbag    Radiohead  4:44 ✓ ✓│  Title  [Airbag    ]  │
    │   ▸ Björk    │ 🎵 02 Paranoid….flac 2  Paranoid… Radiohead  6:23 ✓ –│  Artist [Radiohead ]  │
    │ SMART VIEWS  │                                                     │  …                    │
    │  No lyrics   │                                                     │  [Revert]   [Save]    │
    │  No RG       │                                                     │                       │
    ├──────────────┴───────────────────────────────────────────────────┴───────────────────────┤
    │ Indexing 12,034 files · Lyrics 3,100 / 12,034 ⏸ · LRCLIB ok · Jobs: 1 running ▴           │
    └──────────────────────────────────────────────────────────────────────────────────────────┘

- **Folder tree (left):** roots at the top level, folders only, in Explorer's natural alphabetical order. Nodes
  expand lazily, and the expanded state is remembered. "Hide folders without music" is an option, off by default.
- **File list (center):** folders first, then files, all in natural order. Every file is shown; non-audio files are
  dimmed. Columns: Name, #, Title, Artist, Album, Year, Duration, Format, Bitrate, Size, Modified, RG (✓ track and
  album, ◐ partial, – none), Lyrics tag (✓, ".lrc" when only a sidecar file has them, –), LRCLIB (synced, plain,
  instrumental, not found, pending, error, offline). Columns can be shown, hidden, moved, resized and sorted, and
  the layout persists. Folder rows show their track count and total duration.
- **Address bar:** a breadcrumb where each segment is clickable and has a dropdown of its subfolders. Clicking the
  bar turns it into a text path (Ctrl+L); a pasted path navigates if it lies inside a root.
- **Navigation:** Enter or double-click opens a folder. Backspace or Alt+↑ goes up. Alt+←/→ and the mouse
  back/forward buttons move through history. Typing selects by name. Ctrl/Shift extend the selection. F5 rescans
  the folder. Ctrl+F filters the current folder as you type.
- **File context menu:** Open with default app, Play selection (§14), Open in File Explorer
  (`explorer /select,"<path>"`), Rename (F2), Rename from tags…, Copy path.
- **Folder context menu:** Open, Play folder, Play folder and subfolders, Open in File Explorer, Rename (F2),
  Tidy album…, Rename from tags…, ReplayGain (album)…, ReplayGain (discography)…, Rescan, Remove from library
  (roots only).
- **Advantages over Explorer:**
  - Folders open instantly from the index, even on slow USB or network drives.
  - Library-wide search over file names, tags and lyrics.
  - Tag columns for every supported format, without Windows property handlers.
  - ReplayGain and lyrics health columns.
  - Smart views.
  - The layout and position are remembered.
- As built (M2 task 5):
  - The tree and the list read the index on the UI's read-only connection, and update in place when the indexer
    reports a folder or finishes a scan. Rows are inserted, removed, moved or changed, never reset, so the
    selection stays put. The list is reset only when another folder opens.
  - Folders stay first in both sort directions. Ties sort by name, using natural keys precomputed per row.
  - A folder row shows its subtree's track count in the Title column, and the subtree's total duration.
  - The LRCLIB column (as built at M4):
    - from the index: synced, plain, instrumental, not found, pending;
    - "no tags" for a track without artist or title (never looked up; the script's "no tags" badge);
    - blank for a file whose tags can't be read (its tooltip gives the error).
    - A pending row shows "offline" in offline mode, or "error" when its key's last lookup this session failed
      (`LyricsService.status_for`, through `FileListModel.lrclib_status`). The column repaints when the service's
      state changes or a lookup fails. Sorting uses the index state.
  - Bitrate and Modified are hidden by default. The header menu shows or hides columns and can reset them.
  - The layout (column order, sizes, hidden columns, sort) is saved as the header state in settings.json, with
    the window geometry, the splitter, the expanded folders (by path) and the last open folder, which reopens at
    start.
  - When the open folder leaves the index (deleted or moved outside the app), its nearest surviving ancestor
    opens, as in Explorer.
- As built (M2 task 6):
  - The breadcrumb starts with the root path as one segment; the levels above a root aren't navigable. A "›"
    after each segment drops down its subfolders.
  - Text mode: click the empty part of the bar, or press Ctrl+L. Quotes around a pasted path are stripped. A
    path to a file opens its folder and selects the file. A path outside the library, or not indexed, gives a
    status message.
  - History: back and forward stacks of folders, which skip folders that left the index. The keys are Alt+←/→
    and the mouse back/forward buttons. Opening a new folder clears the forward stack.
  - Up is Alt+↑, the toolbar button, or Backspace (in the list and the tree only, so text fields keep it). It
    selects the folder you came from. F5 rescans the open folder.
  - Typing in the list selects by name, Enter opens a folder, and Ctrl/Shift extend the selection.
- As built (M2 task 7):
  - The context menus show only actions that exist so far. Rename (F2), Rename from tags…, Tidy album… and
    ReplayGain… arrive with M3 and M5.
  - A right-click on the list background gives the open folder's menu, without Open.
  - Double-click or Enter on a file opens it with its default app, whatever the type.
  - "Open in File Explorer" opens a folder itself, and shows a file selected in its folder.
- **Smart views (sidebar):**
  - No lyrics in tags.
  - Found on LRCLIB but not in tags: the candidates for "Save to file tags".
  - Not found on LRCLIB.
  - No ReplayGain.
  - Missing tags (no title, artist or track).
  - Name doesn't match rename mask.
  - Recently modified.

  Each one is a query over the index, shown as a flat list.

## 6. Library search
- Ctrl+K searches the whole library, with a scope toggle for "this folder and below". Matching runs as you type
  (120 ms debounce, stale queries cancelled, worker thread). Free text matches as seek_lyrics' search did (below).
- Field filters:
  - `artist:` `album:` `year:` `genre:` `ext:opus`
  - `lyricstag:yes|no|lrc`
  - `lrclib:synced|plain|instrumental|notfound|pending|error`
  - `rg:none`
  - `"quoted phrase"` keeps spaces and punctuation as typed. The phrase is still matched as a substring, in
    every free-text column, lyrics included.

  Free text searches the free-text columns (below) as substrings.
- Results appear as a flat list with a Folder column (up to 5,000 shown, with the total count). Enter or a
  double-click goes to the file's folder with the file selected. Esc returns to the previous folder. Search
  results and smart views can be played (§14).

### Preserved from seek_lyrics' songs.html search (must not be lost)
songs.html is gone (§17), but its search box was a real feature: type anything, and see every song whose title,
artist, album or lyrics contain it. Library search must keep that behaviour. It lives inside the one Ctrl+K search
of the app's normal workflow, not as a separate lyrics page or a second search UI. Anyone planning or changing §6
checks this subsection first.

**The original** (`tests/reference/seek_lyrics.py`, the page script):
- haystack = title, artist, album and plain lyrics, lowercased;
- a song matches when `haystack.includes(query)`: any substring, of any length, mid-word included, case-insensitive;
- results highlight the match in the title and artist. When only the lyrics match, they also show the first
  matching lyric line as a snippet, with the match highlighted;
- "N of M songs", status chips (All, With lyrics, Missing), an artist checkbox filter, and a detail pane with the
  lyrics (synced or plain) and the Genius/Google links.

**In the app:**
- **Free text** is that rule: a case-insensitive substring over name, title, artist, albumartist, album, genre,
  and lyrics (the LRCLIB plain text and the file's own lyrics tag). "ove" finds "glove" and "lovely", and one or
  two characters work too.
- **Deliberate differences (§17):**
  - accents are ignored ("Sigur Ros" finds "Sigur Rós");
  - every audio file under the roots is searchable, not only the songs a script run saw;
  - the file name, album artist and genre are searched too.
- **Results:**
  - The match is highlighted in the Title and Artist cells.
  - A **Lyrics** column shows the first matching lyric line, with the match highlighted, when the match is only
    in the lyrics. It is empty otherwise.
  - The count reads "N of M tracks".
- **Filters:**
  - The status chips become one-click chips under the search box: All, With lyrics (`lrclib:` synced, plain or
    instrumental) and Missing (the rest).
  - The artist checkboxes become `artist:` filters (repeatable, ORed), which a click on an artist value in the
    results can add.
- **The detail pane is the side panel:** selecting a result shows its lyrics in the Lyrics tab (§8), with the
  timestamps toggle and the Genius/Google buttons. Nothing is duplicated.
- **Acceptance (M6 "Done when"):** a parity test re-implements the page script's rule (lowercased `includes` over
  title, artist, album and plain lyrics). Run on the fixture library with scripted LRCLIB lyrics, it must select
  every track the app's free-text search finds on those fields, and the reverse, apart from the accent
  difference.

### Implementation notes (M6)
Nothing here was needed before M6. The `lyrics` table already keeps every LRCLIB plain text, so the migration can
fill the index at any time and nothing is lost by waiting.
- `core/index/schema.py`, MIGRATIONS v3:
  - Drop and recreate `search` with `tokenize='trigram remove_diacritics 1'` and the columns name, title, artist,
    albumartist, album, genre, lyrics (the tags' text), lrclib (LRCLIB plain).
  - Copy the existing rows from a temporary table, keeping their rowids.
  - Fill `lrclib` from `tracks JOIN lyrics ON key = lyrics_key WHERE status = 'found'`.
  - Recreate the `entries_search_delete` trigger.
  - Add a v2 → v3 migration test, following the existing v1 → v2 test.
- `core/index/lyrics_index.py` `record_lyrics`: `UPDATE search SET lrclib = ? WHERE rowid IN (SELECT entry_id
  FROM tracks WHERE lyrics_key = ?)`, with the plain text when found, else NULL.
- `core/index/scanner.py` `_write_tracks`: the search insert also sets `lrclib` from the `lyrics` table for the
  row's key, so a new copy of an already-found song is searchable at once.
- `core/index/search.py` (new) `text_search(conn, query, *, limit=5000) -> (ids, total)`:
  - with 3 or more characters, `search MATCH` with the query quoted as one FTS phrase (inner quotes doubled), so
    FTS operators and punctuation are literal;
  - with fewer, `LIKE '%q%'` (escaped) over the same columns;
  - results in natural folder and name order.
  The field filters, the scope, the snippet and the UI build on it.
- The snippet is computed for the shown rows only, from `search.lrclib` or `search.lyrics`: the first line whose
  accent-folded, casefolded form contains the query.
- Tests:
  - substring mid-word; case and accents; a match in LRCLIB lyrics and in tag lyrics;
  - 1–2 character queries;
  - FTS syntax and quotes in the query taken literally;
  - a key going from found to not_found drops its text from the matches;
  - the parity test above.

## 7. Side panel and Metadata tab
The side panel sits on the right; it is resizable and collapsible. Tabs: **Metadata** | **Lyrics** (§8). With a
folder selected, the panel shows a folder summary instead: track count, duration, formats, ReplayGain and lyrics
coverage, cover, a Play button and tool shortcuts.

**Metadata tab:**
- Header: the cover (the embedded front cover, else `folder.jpg`, `cover.jpg` or `front.jpg` in the folder, else a
  placeholder), the file name and a format line (e.g. "FLAC 16-bit/44.1 kHz · 4:44 · 32 MB").
- Editable fields: Title, Artist, Album artist, Album, Track # / total, Disc # / total, Year, Genre, Composer,
  Comment, and **Lyrics** (multi-line, LRC timestamps kept as typed). The tag keys per format are in §1.
- Lyrics field: if it is empty and a same-stem `.lrc` file sits next to the track, an "Import from <name>.lrc" link
  fills the field. Saving is the usual Save, and the `.lrc` file itself isn't touched.
- Cover editing: Replace… (file picker, or drop a jpg/png onto the cover) and Remove. It writes the embedded front
  cover, with a warning for images over 1 MB.
- Read-only: path, size, codec details, duration, bitrate, sample rate, bit depth, channels, and a Loudness block
  (track gain and peak, album gain and peak, LUFS comment) managed by the ReplayGain tool.
- **Multi-selection:**
  - Shared fields (Artist, Album artist, Album, Track total, Disc # / total, Year, Genre, Composer, Comment, cover)
    show their value when it is the same on every file. When it differs, they show "(multiple values)" and stay
    untouched unless edited.
  - A clear button per field tells "clear on all" apart from "leave alone".
  - Per-track fields (Title, Track #, Lyrics) are edited one file at a time and are disabled in multi-selection.
  - A cover change applies to every selected file.
- Saving: Ctrl+S saves and Esc reverts, and the tab shows a dirty marker. Changing the selection with unsaved
  edits asks Save, Discard or Cancel. Writes go through TagWriter in the background. On failure, an error is shown
  and the fields stay dirty. Files locked by a running job are read-only, with a tooltip naming the job.
- Preserve the ID3 version on save (v2.3 stays v2.3) and leave all other frames and fields untouched.
- After a save, the index row updates. If the name no longer matches the rename masks, a hint offers
  "Rename to '03 New Title.flac'?".
- The ReplayGain tool writes `LUFS Target : <x>` into Comment. Users may edit it, and the next tool run
  overwrites it.
- As built (M3 task 1):
  - Reads and saves run on one worker thread. Only the changed fields are written, and an empty field clears
    its tag. Lyrics that differ only by CRLF line endings count as unchanged.
  - The format line shows the bit depth for lossless audio and the bitrate for lossy, e.g. "FLAC 16-bit/44.1 kHz
    · 4:44 · 32 MB" or "MP3 320 kbps/44.1 kHz · 3:12 · 7.4 MB". The header says where the cover comes from
    (embedded, or the folder image's name).
  - Raw .aac (no tags) is shown read-only, with a note.
  - Cancel in the unsaved-edits prompt keeps the selection, the open folder, and the window open.
  - Every read that isn't followed by a save (the indexer, the side panel, folder images) opens the file with
    FILE_SHARE_DELETE, so the app never makes a delete or rename in Explorer fail while it reads.
- As built (M3 task 2):
  - Every tag save goes through TagWriter, one at a time on its own thread. It holds a short lock on the file
    while writing.
  - Path locks are one shared registry. A job locks folder trees and a save locks its file; paths compare
    ignoring case. A lock that overlaps another is refused at once, naming the owner. A save under a job's lock
    fails with "<file> is in use by <job>. Try again when it has finished."
  - A file under a job's lock is read-only in the Metadata tab, with "Read-only while <job> is running." as its
    tooltip and note.
  - After a save, only that file is re-read into the index. Its folder totals, up to the root, are adjusted by
    the difference, so no folder rescan is needed.
- As built (M3 task 3):
  - With several files, only the shared fields that were edited or cleared are written, to every file that can
    be tagged.
  - Emptying a "(multiple values)" field by hand leaves it alone. The clear button clears it on all files (the
    placeholder then says "(cleared on all files)"). Emptying a common value clears it, as with one file.
  - Several selected items edit their audio files together. Files without tags, unreadable files and non-audio
    items are left out, with a note.
  - A failure on some files shows "Saved x of n files. Not saved: …" with the reasons, and keeps the edits for a
    retry.
  - When any of the files is under a job's lock, the whole multi-edit is read-only.
- As built (M3 task 4):
  - Replace… takes a JPEG or PNG from a file picker, or one dropped onto the cover (a drop into the app, which is
    allowed). The picture is checked by its signature and decoded on the worker thread. Over 1 MB, a
    confirmation explains that every file carries its own copy.
  - A new or removed cover is pending until Save ("New cover (not saved yet)" or "Cover removed"), and Revert
    drops it.
  - Only the embedded front cover is written or removed; other pictures stay. A folder image is shown when there
    is no embedded cover, never written, and Remove is then disabled.
  - Several files: the header shows their common embedded cover, "(multiple covers)", or "No cover". Replace or
    Remove applies to every file that can be tagged.
  - A field counts as edited only when its text differs from what was loaded, so blank text stored in a tag
    (e.g. "  ") isn't mistaken for a change.
- As built (M3 task 5), the folder summary:
  - It shows for one selected folder, and for the open folder when nothing is selected.
  - Tracks, duration, formats, ReplayGain coverage and lyrics-tag coverage count the whole subtree. "In this
    folder" counts its direct subfolders and files.
  - The cover is the folder image, else the first track's embedded front cover (the first audio file in natural
    order, searched level by level). It is read and decoded on the worker thread.
  - Play folder (enabled when the folder has tracks of its own) and Play with subfolders.
  - LRCLIB coverage appears with M4, and tool shortcuts with M5.
  - It refreshes when the indexer updates the folder.
- As built (M3 task 6), the .lrc import:
  - The sidecar is the same-stem `.lrc` next to the file, matched ignoring case.
  - Its text is decoded as UTF-8 (a BOM is dropped), else Windows-1252; undecodable bytes become U+FFFD instead of
    failing.
  - The link shows for one editable, unlocked file while its Lyrics field is empty, so emptying the field offers
    it again.
  - The import normalizes line endings and trims leading and trailing blank lines. The .lrc file is only read.

## 8. Lyrics tab
The tab shows only externally fetched lyrics: what LRCLIB returns for the selected track. It never shows the
file's own lyrics tag or a `.lrc` sidecar; those belong to the Metadata tab. Every track is looked up, whether or
not it has a lyrics tag.
- **Queued or looking up:** a spinner and "Looking up on LRCLIB…". Selecting a track moves it to the front of the
  queue.
- **Found:** the lyrics, with a timestamps toggle for synced lyrics, and a source line
  ("LRCLIB #id · checked <date>"). Buttons: Copy, and **Save to file tags**, which writes the synced lyrics if
  available, else the plain ones, into the Lyrics tag (the script's `--embed`). It is disabled when the tag
  already holds the same text, and asks before replacing different lyrics.
  - If the file also has a lyrics tag, a note says so, and says whether it differs from LRCLIB.
- **Instrumental, not found, missing artist/title, lookup error, or offline mode:** a plain explanation plus
  **Search on Genius** (`https://genius.com/search?q=<artist title>`) and **Search on Google**
  (`https://www.google.com/search?q=<artist title>+lyrics`), which open in the default browser. Not-found and
  error states also get "Retry now".
- **Multi-selection:** "Select a single track".
- As built (M4 task 2, `ui/side_panel/lyrics_tab.py`):
  - Data and refresh:
    - The tab reads everything from the index (`lyrics_index.lyrics_info`: the track rebuilt with the file-name
      fallback, its key, the cached entry, and the file's lyrics tag as indexed in `search.lyrics`), so it reads no
      file.
    - It re-renders on `entryReady` for its key, and when the service's state changes.
  - States:
    - looking up (a busy bar);
    - found (plain lyrics by default; "Show timestamps" shows the synced ones and is disabled without them;
      "LRCLIB #id · checked <date>"; Copy);
    - instrumental (the source line);
    - not found (with the checked date);
    - missing tags; unreadable tags (no buttons);
    - lookup error (the message);
    - offline; unreachable ("Lookups resume at HH:MM"); paused.
    Found and instrumental entries show even offline.
  - The tag note says "the same lyrics" (ignoring line endings and edge whitespace, against the synced or the
    plain text), or that the tag differs.
  - Buttons:
    - Genius and Google open in the default browser through the window's shell thread. A search uses the
      artist and title present (`search_urls` skips a missing part).
    - Retry now (not found, error, unreachable) calls `LyricsService.retry(entry_id)`: one track is looked up again
      at once, bypassing a fresh cache entry, and the breaker's pause ends. A lookup error shows over a cached
      not_found.
- As built (M4 task 3), **Save to file tags** in the found state:
  - It writes `embed_text(entry)` (synced, else plain) through `TagWriter.embed_lyrics`, which runs
    `core.lyrics.embed_lyrics` under the save lock, then lets the index catch up.
  - It is disabled, with the reason as a tooltip, when:
    - the tag already holds the same text;
    - the format has no tags (`LyricsInfo.can_tag`);
    - a job's lock covers the file;
    - a save is in progress.
  - It asks (`confirm_replace_lyrics`) before replacing different lyrics.
  - It is refused, with a message, while the Metadata tab has unsaved edits on that file.
  - The result shows under the buttons ("Saved to the file's Lyrics tag.", or why not). A Metadata tab showing
    that file, without unsaved edits, reloads to show the new tag.

## 9. Lyrics background service
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
- At M6 (§6 "Implementation notes"), `record_lyrics` also writes the LRCLIB plain text into `search.lrclib` for
  every track with the key (NULL when the key stops being found), and scanned tracks take it from the `lyrics`
  table. Until then LRCLIB text isn't in the search index; the `lyrics` table keeps it.
- As built (M4 task 3), **auto-save** (`services/lyrics_embedder.py` `AutoEmbedder`, owned by the window):
  - Its toggles, until the Settings dialog (M6), are the **Lyrics** menu's "Offline mode" and "Automatically save
    found lyrics into tags", saved in settings.json.
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

## 10. Tools
This is the port of music_tools. The tools open from the folder context menu, the toolbar's **Tools** button (acts
on the current folder) or the folder summary. The **Tools page** is non-modal and takes the center area with a back
button, so browsing stays possible. Each card starts with a plain-language paragraph saying exactly what will
happen to which files.

**A. Rename files from tags**
- "Renames every music file in this folder (and its subfolders) from its tags, using the first mask whose fields
  are all present. Characters not allowed in file names are replaced. A file is never renamed over another one;
  files that fit no mask keep their name."
- Options (defaults are the script's, except the file types and the junk list):
  - Masks: an ordered list you can add to, remove from and drag to reorder. A field picker inserts `{track:02}`
    `{track_total}` `{disc}` `{disc_total}` `{title}` `{artist}` `{albumartist}` `{album}` `{year}` `{genre}`
    `{composer}`, and a live example uses the first file's tags. Default: `{track:02} {title}`, `{title}`.
  - Replacement for invalid characters: `-`.
  - Include subfolders: on.
  - **Send junk files to the Recycle Bin**: off. Default extensions: .cue .nfo .sfv .txt .lrc. That's the script's
    list without .m3u, which is a playlist (§14, §15). The list is editable, within the protections below.
  - Advanced: music file types, defaulting to every audio extension in §1.
- **Cleanup protections**, so the cleanup never works against the rest of the app:
  - Validation refuses these extensions in the junk list:
    - every audio extension in §1 (the script already refused overlaps);
    - playlists: .m3u .m3u8 .pls, which players use and M7 imports;
    - cover images: .jpg .jpeg .png, since `folder.jpg`, `cover.jpg` and `front.jpg` are cover sources (§7).
  - The ReplayGain log (its configured file name, `replaygain.log` by default) is never junk, whatever its
    extension.
  - Some files are kept case by case. The preview lists them as "kept", with the reason:
    - A .cue sheet for a single-file album is needed to play that album. That's a sheet whose FILE lines point to
      one audio file in the folder holding several tracks. It is kept, and its audio file is left out of the
      rename, since a new name would break the reference. Status: "kept: used by <name>.cue".
    - Other .cue sheets (one FILE per track, or files that don't exist) are junk, as in the script. When cleanup is
      off and the files they list get renamed, the preview warns that the sheet won't match them anymore.
    - A .lrc file whose audio file (same name stem) has no lyrics tag yet is kept, because recycling it would lose
      those lyrics. Status: "kept: lyrics not in the tags yet". The Metadata tab offers "Import from <name>.lrc"
      for that track (§7).
  - A .lrc sidecar that stays is renamed along with its audio file, here and with F2 (§11), so it stays matched.
    If the sidecar's new name is taken, the audio file is still renamed and the sidecar is left, with a warning.
- Validation is the script's `validate_config` plus the protections above, shown inline. Run stays disabled until
  it passes.
- **Preview** (always shown first; this is the dry run): an "Old name → New name" table with a status per row:
  rename, unchanged, fallback mask (missing: …), missing tags, name taken, unreadable, kept (with the reason). Junk
  files and renamed sidecars are listed separately, the table can be filtered by status, and the counts match the
  script's summary.
- **Apply** executes exactly the previewed plan and re-checks each item; a file changed since the preview is
  skipped with a warning. Junk files go to the Recycle Bin after a confirmation dialog that lists them. The result
  summary has the same sections as the script.
- Also works on a file selection, renaming only the selected files and their sidecars. A selection run never
  sends junk to the Recycle Bin.
- Details pinned at M1 (`core/rename.py`):
  - The preview simulates the renames in the script's walk order, so "name taken" matches what the script's
    *real* run did. The script's own `--dry-run` checked names against the untouched folder and could differ.
  - A `.lrc` follows an audio file only when exactly one audio file in the folder has its stem (ignoring case).
  - A single-file album is a sheet whose FILE lines all name one audio file present in the folder, with at least
    two TRACKs. The sheet shows "kept: needed to play <audio>", and the audio file shows
    "kept: used by <name>.cue".

**B. ReplayGain**
- "Measures loudness with rsgain at the target level, lowers any track gain that would clip, sets the album gain
  to the lowest track gain and the album peak to the highest track peak, and writes 'LUFS Target : X' in the
  comment. It then copies the album values into the lite/bonus/CD2 subfolders, plus the track values into 'lite'
  subfolders for files with the same name. A report is appended to replaygain.log in the album folder."
- Handles every format marked ✓ in the ReplayGain column of §1. Other audio files are listed as skipped.
- Scope:
  - **This album**: the current folder.
  - **Discography**: every folder below that holds music. Each album's lite/bonus/CD2 subfolders are handled by
    that album. Then every album is leveled to the lowest album gain, so the whole discography plays at the same
    level, and a summary goes to the artist folder's replaygain.log.
- Options:
  - Target loudness: −15 LUFS.
  - Advanced (collapsed):
    - safety threshold 0.99999 and safety margin 0.01 dB;
    - album-data subfolder keywords: lite, bonus, cd2, disc 2, cd 2;
    - track-data keywords: lite; excluded keywords: bonus;
    - log file name: replaygain.log; comment text: "LUFS Target : {target}".
- Preview: the album folders that will be processed, with their keyword subfolders and file counts, plus warnings
  (no audio, unsupported formats skipped, or a command line too long for one rsgain call).
- Run:
  - A background job that processes **one album at a time**, with one rsgain process at below-normal priority, so
    neither the UI nor Windows bogs down.
  - Shows per-album progress and live log lines.
  - Cancel ends the current rsgain run and stops before the next album. Leveling is then skipped, and the report
    says so.
  - As built at M1 (`core/replaygain.py`):
    - A cancelled album gets "Scan: CANCELLED" and a warning, and its result is STOPPED. Albums after it show
      CANCELLED in the discography summary, whose gain line reads "none, the run was cancelled before leveling".
    - A crash is written to the album's log as CRASHED. The job goes on; the script re-raised.
    - Audio files rsgain can't scan get a warning in the album's log ("skipped, format not supported by rsgain").
    - An album whose command line is too long is reported as "Scan: FAILED (command line too long)", and rsgain
      isn't started.
- Report:
  - Per album: the RunLog track table (Track, Loudness, Peak, rsgain gain, Final gain, Note), the album values,
    the subfolder lines, warnings and errors, and the result.
  - Discography summary table: Album, Result, Album gain, Leveled to, Files changed, plus notes.
  - Buttons: "Open replaygain.log" and "Open in File Explorer".

**C. Tidy album**: runs A, then B on the final names. This is the script run without flags. The preview covers
the rename step only; as in the script, ReplayGain can't be previewed on names that are about to change.

| Script                | App |
|-----------------------|-----|
| (no flag)             | Tidy album |
| `--rename-only`       | Rename files from tags |
| `--gain-only`         | ReplayGain, scope "This album" |
| `--discography`       | ReplayGain scope "Discography" (also available in Tidy) |
| `--dry-run`           | Preview, always shown before Apply |
| config block          | card options; defaults saved in Settings, "Reset to defaults" per card |
| `RSGAIN_PATH`         | bundled, fixed |
| `PAUSE_AT_END`        | n/a |
| seek_lyrics `--offline` / `--embed` | Settings (§9) and "Save to file tags" (§8) |
| seek_lyrics songs.html | dropped as a file; its search box is preserved in library search (§6, "Preserved from seek_lyrics") |

**Jobs panel** (bottom drawer): running and finished jobs with progress, logs and reports.
- Two jobs can't overlap on the same folder tree.
- Files under a running job are locked against tag edits and renames.
- After a job, the affected folders are reconciled in the index and the selection is kept.

## 11. Inline rename
Applies to files and folders.
- F2, a slow double-click or the context menu, in the list (and in the tree for folders). For files, only the stem
  is selected. Changing the extension asks for confirmation, as Explorer does.
- Validation: not empty; none of the script's forbidden characters (`/ \ ? % * : | " < >` and control characters);
  no trailing space or dot; no reserved names (CON, PRN, AUX, NUL, COM1–9, LPT1–9); path within the limit; no clash
  with a sibling (a case-only rename is allowed).
- Renaming an audio file also renames its same-stem `.lrc` sidecar, as in §10.
- Blocked on library roots and on anything inside a running job.
- After a rename, a toast (naming the sidecar too, if any) offers Undo for 10 s. The index updates in place.
- As built (M3 task 7):
  - F2 renames from the list (on the Name column, wherever the cursor is) and from the tree; a slow click on a
    selected name too. Rename is also in the context menus for one file or one folder (not library roots).
  - Paths are limited to 259 characters (the classic Windows limit). Reserved names are checked on the part
    before the first dot. Undo puts the sidecar back first.
  - The rename runs off the UI thread under a path lock. A running job or a tag save on the path refuses it with
    a message. Unsaved tag edits of the file ask Save, Discard or Cancel first.
  - The index entry keeps its id, so the selection and the panel follow. The folder scan also recognizes a
    rename (same kind, extension, size and mtime, unambiguous), so renames made in Explorer keep the entry and
    its tags too.
  - The hint after a save uses the Rename tool's default masks, and only appears when the name they give differs
    from the file's.

## 12. Settings
- Library: roots, hide folders without music, watch for changes.
- Lyrics: offline mode, auto-save found lyrics into tags.
- Tools defaults: every option from §10.
- Appearance: theme (follow Windows, light or dark), font size.
- Performance: tag-reading workers.
- About: app version, rsgain version (from `rsgain --version`), licenses.

## 13. rsgain and packaging
- `resources/rsgain/` holds `rsgain.exe`, its DLLs, `presets/` and the licenses. `paths.resource_path()` returns
  `sys._MEIPASS` when frozen, else the project's `resources/`.
- **No ffmpeg at runtime.** `rsgain.exe` (3.8 MB) has its FFmpeg decoders built in; its folder only adds the MSVC
  runtime DLLs. So the app never calls ffmpeg, and the installer doesn't ship it. ffmpeg is a dev-only tool for
  regenerating the test fixtures (§18).
- How rsgain is called:
  - `subprocess.Popen([...], cwd=album_folder, creationflags=CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS)`
    with UTF-8 pipes, read incrementally in the job's worker thread.
  - File names are passed relative to that folder, to keep the command line short (limit 32,767 characters). If
    it is still too long, the album is reported as unscannable instead of being split, because album mode needs
    one call.
  - rsgain prints ANSI colour codes, which are stripped as in the script's `strip_ansi`.
- Startup self-check: if `rsgain --version` fails, the ReplayGain tools are disabled with an explanation.
- PyInstaller one-folder `--windowed` build with icon and version info. An Inno Setup installer puts it in
  `%ProgramFiles%\MusicDesk` with a Start menu shortcut. Uninstalling keeps `%LOCALAPPDATA%` data unless the user
  ticks the option.
- Licenses to ship: rsgain (BSD-2-Clause), CRCpp, and the FFmpeg libraries built into rsgain. Those are LGPL or
  GPL depending on how they were built, so check before distributing the installer. Also Qt/PySide6 (LGPL) and
  mutagen (GPL-2.0-or-later).

## 14. Playback
v1 plays through the default app; MusicDesk has no player of its own.
- **Open with default app** (double-click on an audio file, or the context menu): `os.startfile(path)`.
- **Play folder**, **Play folder and subfolders**, **Play selection**, and **Play results** (search results and
  smart views):
  - The app writes a temporary playlist to `%LOCALAPPDATA%\MusicDesk\playlists\` and opens it with
    `os.startfile`, so the default player loads the whole set.
  - Format: UTF-8 `.m3u8` with `#EXTM3U`, one `#EXTINF:<seconds>,<artist> - <title>` per entry, absolute paths,
    audio files only.
  - Order: "Play folder" uses the folder's natural file order, followed by its subfolders' files in natural order.
    Play selection and Play results use the current view's sort order.
  - The last 20 temporary playlists are kept. Older ones are removed; these are the app's own files.
  - As built (M1, `core/playlists.py`): files are named `play-<YYYYMMDD-HHMMSS>[-n].m3u8`, written as UTF-8 with
    CRLF line endings. The `#EXTINF` label is "Artist - Title", else the title, else the file stem; the duration is
    rounded seconds, or -1 when unknown. Pruning keeps the newest 20 by name and only ever touches `play-*.m3u8`
    in that folder. `folder_tracks(folder, recursive)` gives the Play folder order.
  - If no app is associated with .m3u8, a message explains how to set one in Windows, and offers "Open in File
    Explorer" instead.
  - As built (M2 task 7):
    - The play lists come from the index (paths, durations, artist and title), so no file is read on the UI
      thread.
    - Writing the playlist and calling `os.startfile` run on a worker thread.
    - The fallback opens the played folder, or shows the first selected file in Explorer.
    - "Play folder" with no audio files says so in the status bar.

## 15. Playlist builder (M7)
Planned for M7, not in v1. A sketch to refine with Hugo before M7:
- A PLAYLISTS section in the sidebar: create, rename and delete playlists. They are stored in the DB, so deleting
  one only deletes app data.
- Adding tracks:
  - "Add to playlist ▸" in the context menus, for files, folders (recursive), search results and smart views;
  - dragging rows from the file list onto a playlist in the sidebar. This is an internal drag, unlike the
    disabled drag-out.
- Playlist view:
  - an ordered list: reorder by drag, remove entries, show duplicates, total duration;
  - entries are tied to index entry ids, so in-app renames keep them valid, and missing files are flagged.
- Play a playlist through the default app (§14).
- Export to `.m3u8` through a Save dialog, and import existing `.m3u`/`.m3u8` files (read-only). Playlists are
  never junk (§10). Export creates a file, so it becomes a new Ground rules exception when M7 is built.
- Smart playlists: saved search queries (§6) that refresh automatically.

## 16. Architecture and concurrency
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
- Icons come from the app's own set (folder, audio, image, other). QFileIconProvider is too slow for big folders.
- Every long task takes a `CancelToken`.
- On shutdown, workers stop gracefully, the DB writer flushes, and a running rsgain is killed, with its job marked
  cancelled.

## 17. Porting reference
Source: `tests/reference/music_tools.py` and `tests/reference/seek_lyrics.py`, frozen copies of `music_tools.py`
and `seek_lyrics.py` at the project root, made at M1.

**Required refactors:**
- Remove every `os.getcwd()`, `os.chdir` and `contextlib.chdir`. The working directory is process-global, so
  changing it from a worker would break the app. Functions take explicit folder paths, and rsgain gets `cwd=`.
- `print` and `input` → a `Reporter` protocol (info, warn, error, progress). RunLog keeps collecting warnings and
  errors exactly as it does now.
- Config constants → frozen dataclasses `RenameOptions`, `ReplayGainOptions` and `LyricsOptions`, with defaults
  identical to the scripts (except the audio file types and the junk list). `validate_config` → `validate(options)`,
  which adds the cleanup protections.
- The rename step splits into `plan_renames(folder, options) -> RenamePlan` and `apply_plan(plan) -> RenameReport`.
  Dry run means plan only.
- `load_audio`, `get_tag`, `set_tag`, `set_comment` and `find_txxx` (FLAC/MP3 only) → the `core/tags.py` family
  adapters, which cover every format. They keep the same semantics: keys match ignoring case, and ReplayGain keys
  are written in uppercase.
- Rename fields are read through mutagen instead of TinyTag, normalized to TinyTag 2.0.0's values (track, disc and
  totals as int, MP4's missing total as 0; text exactly as stored, **not stripped**, so "  Title  " keeps its
  spaces in the new name as with the script; full dates such as "2024-05-01" kept whole). Blank text is dropped,
  as `read_fields` does. `TaggedFile.rename_fields()` implements this; parity tests run the script's `read_fields`
  on TinyTag for every fixture TinyTag reads (not WavPack, which it can't).
- The lyrics cache moves to SQLite with the same entry shape, and every result is committed (`CACHE_SAVE_EVERY`
  goes away).

**Behaviors to preserve exactly:**

*Rename:*
- mask fields and number formats; the first complete mask wins;
- `sanitize` with FORBIDDEN_CHARACTERS and the replacement character;
- lowercased extension; no-op when the name is unchanged; Windows case-only rename through `samefile`;
- never overwrite; fallback and missing-tag reporting; recursion.

*ReplayGain:*
- rsgain arguments `custom -s i -l <target> <files…>` (track mode), with rsgain's defaults for `-o` and `-I`.
- Clipping:
  - a track clips if `10^(gain/20)·peak > threshold`; its gain becomes `round(20·log10(threshold/peak) − margin, 2)`;
  - a peak of 0 gives a warning.
- Album values: album gain = min track gain, album peak = max track peak.
- LUFS comment: written into the Comment field. For MP3, that is COMM, and the old TXXX "comment" frame is removed.
- Subfolder copy rules: album data goes to SUBFOLDER_KEYWORDS folders. Track data goes to TRACK_DATA keywords
  unless excluded, for files with the same name in the album folder.
- RunLog text format and result states: OK, DONE WITH ERRORS, STOPPED, CRASHED. The "Files:" line keeps the
  script's exact `N (x FLAC, y MP3)` text, and other formats are appended only when present, e.g.
  `12 (8 FLAC, 0 MP3, 4 OPUS)`.
- Discography:
  - album discovery: sorted `os.walk`, keyword subfolders pruned;
  - only albums that are OK or DONE WITH ERRORS and have an album gain get leveled, to the minimum album gain;
  - files without an album gain are skipped;
  - one leveling line per album log, and the summary goes to the root log.

*Lyrics:*
- tag keys for artist, title and album (generalized per family through `core/tags.py`); the file-name fallback;
- duration rounding; cache key; statuses (found, instrumental, not_found, missing_tags, not_looked_up, error);
- matching (±3 s, normalized containment); the LRC timestamp regex and strip;
- embed writes synced lyrics, else plain, into the Lyrics tag.

**Deliberate changes from the scripts:**
- Formats: every format in §1 instead of FLAC/MP3 (music_tools) and FLAC/MP3/WAV (seek_lyrics).
- Lyrics: every track is looked up on LRCLIB, even with a lyrics tag. The tag is no longer a lyrics "source"; the
  Lyrics tab shows LRCLIB only.
- Auto-embed only fills empty lyrics tags.
- LRCLIB circuit breaker: pause and auto-retry instead of stopping.
- Junk cleanup:
  - opt-in, and files go to the Recycle Bin;
  - .m3u is gone from the default list;
  - the protections and case-by-case keeps of §10 apply, and kept `.lrc` sidecars follow their audio file's rename.
- No songs.html. Its search behaviour is kept in library search (§6), with these differences:
  - accents are ignored;
  - every audio file under the roots is searchable;
  - the file name, album artist and genre are searched too.
- Tags (verified by `tests/core/test_tags_parity.py`):
  - Vorbis ReplayGain keys are written in uppercase (`REPLAYGAIN_TRACK_GAIN`); the script writes the lowercase key
    it is given. Vorbis keys are case-insensitive, so readers see no difference.
  - Saving keeps the ID3 version (v2.3 stays v2.3); the script's `save()` upgraded files to v2.4.
  - WMA disc number and total are read from WM/PartOfSet; TinyTag 2.0.0 ignored them.
- `%` stays forbidden in new names, as in the script, even though Windows allows it.

## 18. Testing
- `pytest` runs:
  - core unit tests (no Qt);
  - LRCLIB tests on mocked HTTP (tests never call the real API);
  - pytest-qt tests for the models, navigation, side panel save and revert, and rename validation.
- Fixtures:
  - tiny 1-second files per format, with varied tags: missing title, ID3v2.3, lowercase TXXX ReplayGain,
    lite/bonus/CD2 subfolders, a name clash;
  - cleanup cases: a single-file album with its .cue, a per-track .cue, .lrc sidecars with and without a lyrics
    tag, `replaygain.log`, `folder.jpg`, a `.m3u`.

  Tests copy them to `tmp_path`.
- Fixture generation:
  - `tests/fixtures/make_fixtures.py` generates the fixtures with ffmpeg (dev-only, found on PATH or through an
    `FFMPEG` environment variable), and the generated files are committed, so running the tests doesn't need
    ffmpeg.
  - ffmpeg can encode FLAC, Vorbis, Opus, MP3, MP2, WAV, AIFF, AAC and ALAC in M4A, WavPack, TTA and WMA. It can't
    encode APE, TAK, Musepack, OptimFROG or DSF/DFF, and Speex depends on the build. Those need sample files from
    Hugo; until then, their tests are skipped with a reason.
- Parity: run the reference functions and the ported core on identical copies of FLAC/MP3 fixtures, then compare
  file names, tags and RunLog text with timestamps and durations masked. Other formats get their own expected
  values.
- `slow` marker: the real bundled rsgain on fixtures, for every format rsgain supports.
- Performance: a synthetic tree of 100k entries checks index time and UI responsiveness. Debug builds log any
  event-loop stall above 50 ms through a QTimer watchdog.
- Manual checklist per milestone, on a copied album:
  - browse while indexing; edit tags, including multi-file edits and the cover; rename a file and a folder;
  - preview and run each tool, including the cleanup protections, then compare `replaygain.log` with a script run;
  - go through every Lyrics tab state, including offline mode;
  - play a folder through the default app.
