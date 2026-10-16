# 14. Playback
v1 plays through the default app; MusicDesk has no player of its own.
- **Open with default app** (double-click on an audio file, or the context menu): `os.startfile(path)`.
- **Play folder**, **Play folder and subfolders**, **Play selection**, and **Play results** (search results):
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
  - As built (M6 task 1): Play results plays the shown search results (up to 5,000) in the list's current sort
    order, through the same path as Play selection.
