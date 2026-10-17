# 6. Library search
- Ctrl+K searches the whole library. There is no scope option (the "this folder and below" toggle is removed,
  Hugo, 2026-10-16). Matching runs as you type (120 ms debounce, stale queries cancelled, worker thread). Free
  text matches as seek_lyrics' search did (below).

### What is searched (Hugo, 2026-10-16)
- **Names, at once:** every folder and every audio file (by extension, `formats.AUDIO_EXTENSIONS`) is found by
  its name as soon as the folder walk (§4 phase A) lists it, before its tags are read. Other files (covers, logs,
  `.cue`) are not results.
- **Tags and lyrics, once read:** title, artist, album artist, album, genre, and the lyrics, both the file's own
  lyrics tag and the LRCLIB text, as soon as the tag read (and the LRCLIB lookup) reach that file. Lyrics are
  always searched; there is no option for them.
- While indexing runs, the live re-run (1 s after a finished scan or LRCLIB result, below) fills files in as their
  tags arrive. A long tag read is acceptable as long as names are searchable meanwhile.
- **No options on the page:** the "All / With lyrics / Missing" chips and the "Only in <folder> and below" box are
  removed, with nothing in their place. The row under the search box keeps the count and Play results.
- **Why names were missing (read in the code, 2026-10-16):** the walk already stores each entry's name in the
  `search` table, but `text_search` joined every result to `tracks`, which a file only gets once its tags are
  read, and a folder never. Whether the tag read also slows the search worker was not measured. Fixed in 0.2.11
  (below, "Matching").
- Field filters:
  - `artist:` `album:` `year:` `genre:` `ext:opus`
  - `lyricstag:yes|no|lrc`
  - `lrclib:synced|plain|instrumental|notfound|pending|error|notags`
  - `rg:none`
  - Kept as they are (Hugo, 2026-10-16: not discussed yet). A filter that needs tags (`artist:`, `album:`,
    `year:`, `genre:`, `lyricstag:`, `lrclib:`, `rg:`) limits the results to audio files whose tags are read;
    `ext:` limits them to files.
  - `"quoted phrase"` keeps spaces and punctuation as typed. The phrase is still matched as a substring, in
    every free-text column, lyrics included.

  Free text searches the free-text columns (below) as substrings.
- Results appear as a flat list with a Folder column (up to 5,000 shown, with the total count). Enter or a
  double-click goes to the file's folder with the file selected; on a folder row, it opens that folder.
- **Folder rows:** a folder icon, its name, its parent in Folder, the other columns empty. Selecting one shows the
  folder summary in the side panel, as the file list does. Play results plays the audio files among the results
  and skips folders.
- **Audio files not read yet:** Name and Folder, the tag columns empty until the live re-run fills them. Esc returns to the previous folder. Search
  results can be played (§14).

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
  - The count reads "N of M results" (folders and audio files).
- **Filters:**
  - The status chips are dropped (Hugo, 2026-10-16): no buttons under the search box. `lrclib:` covers the need
    for those who type it.
  - The artist checkboxes become `artist:` filters (repeatable, ORed), which a click on an artist value in the
    results can add.
- **The detail pane is the side panel:** selecting a result shows its lyrics in the Lyrics tab (§8), with the
  timestamps toggle and the Genius/Google buttons. Nothing is duplicated.
- **Acceptance (M6 "Done when"):** a parity test re-implements the page script's rule (lowercased `includes` over
  title, artist, album and plain lyrics). Run on the fixture library with scripted LRCLIB lyrics, it must select
  every track the app's free-text search finds on those fields, and the reverse, apart from the accent
  difference.

### As built (M6 task 1)
- **Query syntax** (`core/index/search.py` `parse_query`):
  - The unquoted text left after the filters, trimmed, is **one** substring, as the page took the whole box:
    "glove like" doesn't find "like a glove". Each `"quoted phrase"` is another substring; all parts are ANDed.
  - A word before a colon that isn't a filter name stays text ("Re: Stacks"). A filter with an unknown value is
    ignored and named in a note above the results. An empty `artist:` (being typed) is ignored.
  - Every filter is repeatable; a filter's values are ORed, different filters ANDed. Values may be quoted
    (`artist:"Sigur Rós"`). `artist:` covers the album artist too. `year:` is a prefix of the date. `ext:` takes
    "opus" or ".opus". `lyricstag:` follows the ♪ column (yes = a tag, lrc = only a sidecar, no = neither).
    `lrclib:` follows the LRCLIB column (`error` = a key whose lookup failed this session, from the lyrics
    service; `notags` = no artist or title, never looked up). `rg:` takes none, partial or full.
- **Matching** (`text_search`, 0.2.11): folders (not a root's top entry, whose name is the root path) and audio
  files by extension, read or not: `tracks` is a LEFT JOIN, so a name is found from the walk on. A filter on
  tags (`TAG_FILTERS`) adds `t.entry_id IS NOT NULL`, so `rg:none` never takes a folder's or an unread file's
  missing tags for "none". Every part of 3 or more characters goes into one FTS5 `MATCH`
  (FTS5 allows one per table per query), as a quoted phrase with column filters; shorter parts use `LIKE` over
  the same columns. For a short non-ASCII part, a Python function `md_contains` folds both sides, since SQLite's
  LIKE folds ASCII only. Known limit: a 1–2 character ASCII query keeps accents ("jo" doesn't find "Jóga").
- **Order and cap:** every match is sorted in Python by (folder path, name), natural order (a folder's files
  before its subfolders'; a folder row sorts by its parent, among its siblings' files), then cut at 5,000; the
  count reads "N of M results". The header can sort by any
  column; the Folder column's order is the result order (the default).
- **Rows** (`result_rows`): file list rows (a folder's track count and date left out, so its tag columns are
  empty; the Title column sorts a folder with the empty titles) plus the folder path, highlight spans for Title and Artist (free text,
  and the `artist:` values) and the snippet: the first line of the LRCLIB text, else of the tags' lyrics
  (timestamps stripped), containing a part that matches none of name, title, artist, album artist, album or
  genre. `fold()` (NFD, marks dropped, casefold, with offsets) places the highlights; `fold_text()` is the fast
  form for long texts.
- **Service** (`services/search_service.py` `SearchService`): one worker with its own read connection. A new
  query interrupts the running one (`sqlite3` interrupt) and stale results are never emitted. The rows' sort keys
  are computed there too (`search_view.sort_keys`, as per-column ranks), so the UI thread only swaps rows.
  Measured on 5,000 tracks with 1.5 KB of lyrics each: about 0.1–0.2 s to match, 0.5 s to build 5,000 rows with
  snippets and 0.35 s for the keys, all on the worker; showing them costs the UI thread about 20 ms plus a repaint.
- **Page** (`ui/search_view.py` `SearchView`, a page of the window's centre):
  - The query field is in the toolbar ("Search library (Ctrl+K)"). Typing opens the page and runs after 120 ms.
    Enter in the field moves to the results; Esc (field or results) returns to the file list as it was. The
    query is kept for the next Ctrl+K.
  - Under it: the count and Play results (the chips and "Only in <folder> and below" were removed in 0.2.11).
    Columns: Name, Title, Artist, Lyrics (the snippet), Album, Folder (elided on the left), then
    the file list's other columns. The model sorts itself (no sort proxy) and isn't editable;
    since M7 task 3 its rows drag as entry ids into the playlist (§15).
  - Enter or a double-click goes to the file's folder with the file selected, or opens a folder row's folder.
    Play results plays the audio files, read or not (`queries._entries` picks them by extension), and skips
    folders. The context menu has Go to folder,
    Open, Play selection, Open in File Explorer, Copy path(s), and "Filter by <artist>"; Ctrl+click on an artist
    does the same (the page's artist checkboxes).
  - Opening the search switches the side panel to the Lyrics tab once. A result's selection drives the side
    panel as the file list does, unsaved edits included (Cancel keeps the result, and the search).
  - Live results: folder updates, finished scans and LRCLIB results re-run the query after 1 s while the page
    shows. The same query's results are applied in place (removals in runs, changed rows, additions, then the
    current sort), so the selection stays; a new query resets the list.

### Advanced search: §26
- Specified with Hugo on 2026-10-17 (M12) as a separate window of album lists to fix by hand, unrelated to this
  search bar. The Lyrics menu's Find entries it was first meant to replace are retired instead.
- Retired by M12 (Hugo, 2026-10-17; §26 "Retired with M12"): the `lyricstag:` filter, and free text searching the files' own lyrics tags (LRCLIB's lyrics only).
