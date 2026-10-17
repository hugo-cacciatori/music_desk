# 5. Browser
Explorer-like, with no copy, paste or new. Delete sends to the Recycle Bin only (§19, from M8).

    ┌──────────────────────────────────────────────────────────────────────────────────────────┐
    │ ← → ↑ ⟳   H: › Music › Radiohead › OK Computer        [Filter folder]  [⌕ Search library] │
    ├──────────────┬───────────────────────────────────────────────────┬───────────────────────┤
    │ LIBRARY      │ Name ▲              #  Title     Artist     Time RG ♪│ [Metadata] [Lyrics]   │
    │ ▸ H:\Music   │ 📁 Lite                                             │  cover                │
    │ ▾ D:\Rips    │ 🎵 01 Airbag.flac    1  Airbag    Radiohead  4:44 ✓ ✓│  Title  [Airbag    ]  │
    │   ▸ Björk    │ 🎵 02 Paranoid….flac 2  Paranoid… Radiohead  6:23 ✓ –│  Artist [Radiohead ]  │
    │              │                                                     │  …                    │
    │              │                                                     │  [Revert]   [Save]    │
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
  (`explorer /select,"<path>"`), Rename (F2), Tools…, Remove ReplayGain tags…, Copy path, Delete (Del, §19).
  There is no split entry: the split starts from a `.cue`'s preview (§20, §22; Hugo, 2026-10-16).
- **Folder context menu:** Open, Play folder, Play folder and subfolders, Open in File Explorer, Rename (F2),
  Tools…, Remove ReplayGain tags…, Rescan, Delete (Del, §19; not on roots), Remove from library (roots only).
- New items arrive with their milestones: Delete with M8.
  - As built (M5): the planned per-tool items (Tidy album…, Rename from tags…, ReplayGain (album)… and
    (discography)…) became one "Tools…", which opens the Tools page with the folder or selection as its scope
    (§10).
- **Advantages over Explorer:**
  - Folders open instantly from the index, even on slow USB or network drives.
  - Library-wide search over file names, tags and lyrics.
  - Tag columns for every supported format, without Windows property handlers.
  - ReplayGain and lyrics health columns.
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
  - A right-click on the list background gives the open folder's menu, without Open.
  - Double-click or Enter on a file opens it with its default app, whatever the type.
  - "Open in File Explorer" opens a folder itself, and shows a file selected in its folder.
- **Smart views: dropped** (Hugo, 2026-10-15). A sidebar section of library-wide lists would give them more weight
  than they have:
  - not every track needs lyrics in its tags, since the app shows LRCLIB's lyrics anyway;
  - a name that doesn't match the rename masks depends on each user's masks;
  - missing ReplayGain matters inside a folder (the RG column) and is overwhelming library-wide;
  - "recently modified" doesn't answer a need.

  What remains useful, finding the songs that need a lyrics action, is three **Find** entries in the Lyrics
  menu. Each opens library search (§6) with a ready-made query over the whole library:
  - "Find songs LRCLIB didn't find": `lrclib:notfound` (to look them up on Genius or Google);
  - "Find songs without lyrics in their tags": `lyricstag:no lyricstag:lrc`;
  - "Find songs LRCLIB can't look up (no artist or title)": `lrclib:notags`.

  They stay as they are until the advanced search (§6, "Later") replaces them (Hugo, 2026-10-16: these lyrics
  lists don't really answer a user need).
  - Retired by M12 (Hugo, 2026-10-17; §26 "Retired with M12"): the whole Lyrics menu (these entries included), the ♪ column, and the Action
    menu's "Show tracks without ReplayGain tags" (replaced by Action › Advanced search…).
- **Action menu** (Hugo, 2026-10-16): between Lyrics and Help (File, View, Lyrics, Action, Help), for actions over
  the whole library. Disabled with no library, as Lyrics is.
  - "Show tracks without ReplayGain tags" opens library search (§6) with `rg:none`, through `MainWindow.find` as
    the Find entries do: the tracks whose tags were read and that carry no ReplayGain tag. Partial tags aren't
    listed (`rg:partial` stays typeable).
  - It is on demand: the smart-views reasoning above still keeps it out of the sidebar, but Hugo wants the list
    as an explicit action.
  - As built (0.2.12): `main_window.ACTION_QUERIES`, as `FIND_QUERIES` for the Lyrics menu. Tested in
    `tests/ui/test_action_menu.py` against the files' own tags.
