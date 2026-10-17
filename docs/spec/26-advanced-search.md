# 26. Advanced search
Specified with Hugo on 2026-10-17 (M12, built in 0.4.1–0.5.0). It replaces §6 "Later", which had nothing designed.

**The need** (Hugo, 2026-10-17): a separate list of albums to go through and fix by hand, such as the albums still
missing ReplayGain tags or the EAC rips not split yet. Its end function is just that list of clickable addresses.
It has nothing to do with the search bar (Ctrl+K, §6), which stays as it is.

## The window
- The toolbar's **Advanced search** button (beside Tools and Playlist builder, since M16; Action › Advanced
  search… before) opens it. It is a **separate window**, not modal: the rest of the app keeps working beside it.
  One window at a time; the button brings it to the front.
- Top to bottom:
  - **The options**, a choice of one search ("macro-like" actions, Hugo). For now, two:
    - **Find albums without ReplayGain tags**;
    - **Find albums that are not split**.
  - **Start**, which runs the chosen search over the whole library on a worker (Ground rule 2).
  - **The results**: a list of album folders, each one's full path. A double-click shows that folder in
    MusicDesk's own folder view (the main window's tree and file list, as Go to folder does from Ctrl+K), ready to
    fix with the Tools, the Split page or the tags.
  - **Refresh search**, once a search has run, even with no result: it runs the same search again, so the list
    follows the fixes made meanwhile.
  - **Clear** (Hugo, 2026-10-17, M16), beside Refresh search: empties the results and the status line, and
    disables Refresh search until a search runs again. A search still running is dropped. Disabled while there
    is nothing to clear.
- Nothing is changed by the window itself: it only lists folders.
- **Built (M12 task 3, 0.4.3):** `ui/advanced_search.py` `AdvancedSearchWindow`, a `Qt.Window` child of the main
  window (above it, never modal), made on first use (`window.advanced_search`). The options are radio buttons
  (the first chosen at first); the status line beside the buttons reads "Searching…", "N albums found (albums
  without ReplayGain tags)." or "No album found (…)."; Start and Refresh are disabled while a search runs.
  Refresh runs the last search that ran, whatever option is picked meanwhile. `services/album_search_service.py`
  `AlbumSearchService` runs one search at a time on its own thread and read connection; a newer one replaces an
  older one's result. A double-click calls `MainWindow.show_folder` (unsaved Metadata edits ask first, the Ctrl+K
  page is left, the file list shows the folder, and the main window comes to the front); a folder gone from the
  library says so in the status bar.

## The searches
- **Albums without ReplayGain tags** (Hugo: "any track missing"): every folder holding audio files where at least
  one track whose tags are read has no ReplayGain tags (the RG column's "none"), so the album needs a ReplayGain
  run. Half-done albums are listed too. Folders whose tags aren't read yet aren't judged.
- **Albums that are not split** (Hugo: "cue + image, no tracks"): every folder with a `.cue` whose FILE line points
  to one FLAC, WAV or AIFF file in that folder (a single-file album, §22), and no other audio file beside that
  image: it was never split. The EAC log isn't required.
- Both read the index (§2), not the disk, so a search is quick and works on an offline root's last index. The one
  exception: "not split" opens the `.cue` sheets of its candidate folders, to see which file they play.
- **Built (M12 task 2, 0.4.2):** `core/index/album_search.py`, `run(conn, search)` gives `AlbumResult`s (the
  folder's entry id and path), sorted by path in Explorer's order; `SEARCHES` holds the options' keys and labels.
  - Without ReplayGain: a track counts when its tags are read, it isn't unreadable, its format has tags (a raw
    `.aac` can't be tagged, so it is never listed), and it has none of the four ReplayGain values. A track with
    only some of them (the RG column's "partial") counts as tagged.
  - Not split: the index's candidates are folders with at least one `.cue` and exactly one audio file (any audio
    extension), of a format the split reads; the folder is listed when one of its sheets names only that file
    (ignoring case). A sheet that can't be read, or that names another file, doesn't count. `cancelled` is
    checked between candidates.

## Retired with M12 (Hugo, 2026-10-17)
Hugo never wanted lyrics saved in the lyrics tag: every function that relies on that tag goes. Lyrics already in
files' tags stay where they are; the app just no longer shows, searches, writes or protects them.
- **The Lyrics menu**, entirely:
  - its three Find entries (§5): "Find songs LRCLIB didn't find", "Find songs without lyrics in their tags",
    "Find songs LRCLIB can't look up". Hugo: retire them; `lrclib:notfound` and `lrclib:notags` stay typeable in
    Ctrl+K;
  - **Automatically save found lyrics into tags** (§9), with its background writer;
  - **Offline mode** (§9). Without internet the app shows no error: after 10 failed lookups in a row it pauses
    and retries every 10 minutes by itself ("LRCLIB unreachable, retrying at HH:MM", with Retry now), and the
    status bar's Pause covers a session without lookups. Hugo saw no remaining need. The service keeps its
    `offline` parameter for the tests, which never touch the network; it is no longer a setting.
- **Save to file tags** in the Lyrics tab (§8), and its note about the file's own lyrics tag.
- **The Metadata tab's Lyrics field** and its "Import from <name>.lrc" link (§7).
- **The ♪ (lyrics tag) column** of the file list and search results (§5), and the folder summary's lyrics-tag
  coverage (§7).
- **The `lyricstag:` filter** (§6), and Ctrl+K searching the tag's lyrics: free text searches LRCLIB's lyrics only.
- **The `.lrc` protection** of the Rename tool's junk cleanup (§10): an `.lrc` is junk like the other default
  extensions when that option is ticked. A `.lrc` sidecar still follows its audio file's rename.
- **Action › Show tracks without ReplayGain tags** (§5): Advanced search's album search replaces it.

### Built (M12 task 1, 0.4.1)
- Removed: the Lyrics menu and `main_window.FIND_QUERIES`; `services/lyrics_embedder.py`, `TagWriter.embed_lyrics`
  and `LyricsEmbedded`, `core.lyrics.embed_lyrics`/`embed_text`/`EmbedResult`, `lyrics_index.embed_candidates`;
  the Lyrics tab's Save to file tags and tag note; the Metadata tab's Lyrics field and `.lrc` import
  (`metadata.lrc_sidecar`, `decode_text`, `Snapshot.lrc_name`/`lrc_text`); the ♪ column (`FileRow.lyrics_tag`);
  the folder summary's lyrics-tag row (`FolderSummary.n_with_lyrics_tag`); `lyricstag:`; `rename.has_lyrics` and
  the `.lrc` keep; `Settings.lyrics_offline` and `lyrics_auto_save` (an older settings file loads, and loses
  them at the next save).
- Kept, unused by any feature: the schema's `tracks.has_lyrics_tag`, `search.lyrics` and
  `folder_stats.n_with_lyrics_tag` (no migration). The scanner now writes 0 and NULL there; values from before
  stay until a file is read again, and nothing reads them. `Track.embedded` (core.lyrics, the ported script's
  read) stays for the parity tests. `TaggedFile` still reads and writes the lyrics field generically (the trim and
  the split drop it from their copies).
- `LibraryService(lyrics_offline=…)` and `LyricsService.set_offline` stay for the tests (`lyrics_offline_default`
  keeps a test library off the network). The status bar's text for that state reads "Lyrics: lookups off", and
  the Lyrics tab's "Lyrics lookups are off, so LRCLIB isn't contacted."
- The file list's saved header layout gains a "v2:" prefix: one saved with the ♪ column (by position, as Qt
  restores it) is dropped for the defaults once, so the columns after it don't shift.
- Ctrl+K's seek_lyrics parity test no longer compares songs with a lyrics tag (the page searched those lyrics).

## Tests
- The two searches over a fixture library: a folder with all tracks tagged (not listed), one with one track
  untagged (listed), one with no track tagged (listed), one not read yet (not listed); an EAC-style folder (image,
  cue, log: listed), one with the image and its split tracks (not listed), one with a multi-file cue (not listed),
  a cue whose image is missing (not listed).
- The window: not modal (the main window still takes input), Start runs on a worker, the results, a double-click
  that shows the folder in the main window, Refresh search after a fix, and an empty result.
- The retirements: the Lyrics menu, the ♪ column, the Metadata Lyrics field, Save to file tags and `lyricstag:` are
  gone; Ctrl+K no longer finds a word only in a lyrics tag; an `.lrc` is junk when the option is ticked.
