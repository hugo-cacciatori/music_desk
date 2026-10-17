# 8. Lyrics tab
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
- Retired by M12 (Hugo, 2026-10-17; §26 "Retired with M12"): Save to file tags, the note about the file's own lyrics tag, and the offline state.
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
