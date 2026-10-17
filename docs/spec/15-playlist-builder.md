# 15. Playlist builder (M7)
Designed with Hugo on 2026-10-15. It replaces the earlier sketch (named playlists in the sidebar, smart playlists,
import of existing playlists): one playlist in progress, built by dragging music in.

**Opening it**
- A checkable **Playlist builder** button in the toolbar (Ctrl+Shift+P) shows or hides it. (View › Playlist
  builder was retired with the View menu in M16.)
- It is a narrow vertical panel (about 300 px, scrolling vertically) between the file list and the side panel.
  Opening it takes only the file list's spare width. The folder tree, the side panel and every column keep
  their sizes; if the columns no longer fit, the list scrolls sideways. Closing it gives the space back.
- It blocks nothing: browsing, the side panel, search, the tools and jobs all go on while it is open.

**Adding music**
- Drag folders from the folder tree, or folders and songs from the file list or the search results, into the
  panel.
- The selection works as in Explorer: Ctrl+click adds one item, Shift+click a range, Ctrl+A everything.
- A folder brings every song under it, subfolders included, read from the index (no file is read).
- The order on import is alphabetical, as Explorer shows it: natural order, a folder's files before its
  subfolders' files. Several dropped items keep the order they had in the view.
- Songs are inserted where they are dropped, or at the end when dropped on the empty area. A song already in the
  playlist isn't added again; the status bar says how many were skipped.
- The drag carries entry ids in an app-only format, never file paths. A drop in File Explorer still does nothing
  (Ground rule 1).

**Arranging it**
- Drag rows to move them.
- Ctrl+↑ and Ctrl+↓ move the selected tracks up or down. Alt+↑ stays "go up a folder".
- Delete removes the selected tracks from the playlist (the files are untouched). **Clear** empties it after a
  confirmation.
- Columns: #, Title, Artist, Album, Album artist, Track, Disc, Year, Genre, Duration, Format, File name and
  Folder. The header's menu shows or hides any of them, and they can be moved and resized. The default is
  #, Title, Artist, Duration, for the narrow panel. The layout is remembered.
- A click on a column header sorts the playlist by that column, and a second click reverses it. Sorting changes
  the playlist's order itself, since the order is what gets exported.
- A footer gives the count and the total duration.
- When the playlist is empty, the panel shows a placeholder: "Drag folders or songs here to build a playlist".
- A track whose file left the index (deleted or moved outside the app) is dimmed with a tooltip, and is left out
  of exports and playback. In-app renames keep it valid, since entries are tied to index entry ids.

**Using it**
- **Export playlist to .m3u8…** opens a Save dialog; the last folder used is remembered.
  - The file is UTF-8 `.m3u8` with `#EXTM3U` and `#EXTINF` lines, as in §14.
  - A **Relative paths** checkbox next to the button (remembered) chooses the path style. Relative paths are
    relative to the saved file, so the playlist keeps working when the music folder moves together with it.
    Songs on another drive fall back to absolute paths. Unticked, every path is absolute.
  - Writing this file, wherever the user saves it, is a new Ground rule 1 exception; the Save dialog asks before
    replacing a file.
- **Open playlist with default player** plays it through a temporary playlist, as Play selection does (§14):
  foobar2000, or whatever is associated with `.m3u8`.
- The playlist is kept across restarts in the app's data (`%LOCALAPPDATA%\MusicDesk`), until it is cleared.

**Checked by Hugo in the installed app (2026-10-17):** an exported playlist plays the right songs in his music
player, and dragging songs and folders into the builder with the mouse works.

**As built (M7 task 4): export and play** (`ui/playlist_panel.py`, `MainWindow`).
- Under the footer: **Play**, **Export…** and the **Relative paths** checkbox (ticked by default). Play and
  Export… are enabled when at least one track is still in the library. `PlaylistPanel.entries()` gives the
  tracks in the playlist's order, without those gone from the library, from the index (`TrackRow` paths,
  durations, artists and titles).
- `MainWindow.play_playlist` goes through `_play` (§14's temporary playlist, its .m3u8 fallback toast included).
- `MainWindow.export_playlist`: the Save dialog (`choose_export_file`, replaced in tests; Windows' dialog asks
  before replacing a file) suggests `Playlist.m3u8` in the last export folder (`Settings.playlist_export_dir`,
  else the user's Music folder); `.m3u8` is added when no extension is typed. `playlists.export_playlist` runs
  on the shell thread; `playlistExported` gives "Exported N tracks to <file>." plus how many tracks not in the
  library were left out, and a failure gives a toast offering the folder in Explorer.
- `Settings.playlist_relative` keeps the checkbox.

**As built (M7 task 3): drag and drop** (`ui/entry_drag.py`, `ui/playlist_panel.py`).
- `ENTRY_IDS_MIME` ("application/x-musicdesk-entry-ids"): a JSON list of entry ids, the drag's only format (no
  URLs, no text), offered as a copy only. `EntryDragView` (a QTreeView) drags its `drag_ids()`, the selection in
  the order shown; the file list, the search results and the folder tree use it (`DragOnly`: nothing can be
  dropped on them). A drop in File Explorer therefore does nothing (Ground rule 1, tested).
- The folder tree allows several folders to be selected (Ctrl/Shift+click); the current one is still the open
  folder. Its drag gives the selected folders in the tree's order, leaving out any under a collapsed folder.
- `PlaylistModel.dropMimeData`: entry ids go through `add_entries` (`track_ids_for`, then `track_rows` for the
  paths) at the drop row, or at the end on the empty area; `added(added, skipped)` gives the status bar message
  ("Added N tracks to the playlist. M already in it were skipped.", or "Nothing to add…"). The playlist's own
  rows drag in a second format (`PLAYLIST_ROWS_MIME`) and are moved by the model itself (`move_rows_to`), as a
  copy, so Qt never removes rows after the drop; the moved rows stay selected.
- Ctrl+↑/↓ (`move_selected`) and Delete (`remove_selected`) are shortcuts on the list. **Clear** sits beside the
  title, enabled when the playlist isn't empty, and asks first (`ask_clear`, replaced in tests).

**As built (M7 task 2): the panel** (`ui/playlist_panel.py`).
- `PlaylistPanel` (title, list, footer) wraps `PlaylistModel` over a `Builder`. It is the splitter's third pane,
  between the centre and the side panel.
  - `MainWindow.set_playlist_visible` takes its width (`Settings.playlist_width`, 300 px by default) from the
    centre only, and gives it back when hidden. The splitter's state is saved with the panel hidden; its
    visibility, width and column layout (`playlist_visible`, `playlist_width`, `playlist_header`) are kept apart.
  - The toggle is a checkable toolbar action, "Playlist builder" (Ctrl+Shift+P).
- Columns as listed above. The defaults #, Title, Artist and Duration fit the 300 px without scrolling. The
  header menu shows any other column; Title always stays.
- A header click sorts the playlist itself, missing tracks last. **"#" sorts by the order tracks were added in**,
  so it undoes a sort by another column. Sorts give the same order however often they are asked, since
  `QTreeView.sortByColumn` asks twice.
- A track not in the index shows its file name as the title, dimmed, with a tooltip. The footer reads "N tracks ·
  duration", plus "· n not in the library". The placeholder is painted on the empty list.
- The playlist is saved in `playlist.json` next to `settings.json`, 0.5 s after each change and when the window
  closes, and restored at start. `PlaylistModel.refresh` follows the index (folder updates and finished scans,
  1 s debounced): renames update paths through the ids, and a rebuilt index finds tracks again by path.
- Found with the new toolbar button: a long path made the address bar push the search field into the toolbar's
  overflow menu. The breadcrumb now shrinks (minimum 160 px), hiding its leading folders as Explorer does; the
  open folder always shows, and the tooltip and Ctrl+L give the whole path.

**As built (M7 task 1): the core.**
- `core/playlist_builder.py` `Builder`: an ordered list of `Item(entry_id, path)`.
  - The id keeps an item valid through in-app renames. The path finds it again after an index rebuild, and keeps
    a file that left the library listed (`entry_id` None, shown dimmed).
  - `add(items, position)` inserts in order and skips tracks already there (by id, else by path ignoring case),
    returning (added, skipped).
  - `move_rows(rows, ±1)`: every selected row moves one step, nothing moves when the selection touches the edge.
    `move_to(rows, position)` is the drag.
  - `sort(key, reverse)` is stable. `resolve(lookup)` brings items up to date with the index.
  - `save` / `load`: JSON (`{"version": 1, "items": [{"id", "path"}]}`), written atomically; anything malformed
    is dropped, never an error.
- `queries.track_ids_for(conn, ids)`: dropped items in the order given. A folder brings its tracks in Explorer's
  order (`folder_entry_ids`, shared with Play folder); a track stays a track; anything else is left out.
- `queries.track_rows(conn, ids)` → `TrackRow` (path, name, folder, title, artist, album, album artist, track,
  disc, year, genre, duration, format); ids that left the index are absent.
- `playlists.export_playlist(entries, target, relative)`, through `render(relative_to=)` and `playlist_path`.
  Relative paths are relative to the file's folder, and a file on another drive (no relative path exists) stays
  absolute. The file is written atomically.
