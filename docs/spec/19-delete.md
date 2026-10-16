# 19. Delete to the Recycle Bin
Specified with Hugo on 2026-10-16 (M8). The user can delete files and folders from the app as in File Explorer,
except that the app **only ever sends them to the Recycle Bin**. It never deletes anything permanently.

**Where and how**
- In the file list, the search results (§6) and the folder tree (§5): the **Del** key, and **Delete** (shown with
  "Del") in the context menus of files, folders and results.
  - **Shift+Del does the same as Del.** In Explorer it deletes permanently; the app never does, and its dialog
    says "Recycle Bin".
  - The selection works as in Explorer, as it already does: Ctrl+click, Shift+click, Ctrl+A in the list and the
    results, and Ctrl/Shift+click in the tree (M7). Del acts on the whole selection of the view that has the
    focus.
  - Not offered on the list background (Explorer doesn't delete the open folder from there either), and never
    for library roots. "Remove from library" stays the way to drop a root (§3).
  - In the playlist builder, Del keeps removing tracks from the playlist only (§15). The files are untouched.
    Each view's shortcut is a WidgetShortcut, so they don't clash.

**Always asks first** (Hugo: "files and folders, always ask")
- One file: "Send '01 Airbag.flac' to the Recycle Bin?"
- One folder: "Send the folder 'Kid A' and everything in it (12 files) to the Recycle Bin?" The count comes from
  the index (`folder_stats` and the direct files), so no folder is walked on the UI thread.
- Several items: "Send these 5 items to the Recycle Bin?", followed by the names (the first 10, then "and N
  more").
- When a file with unsaved Metadata edits is included, the dialog adds "Unsaved tag edits on it will be lost."
- Buttons: **Send to Recycle Bin** and **Cancel**, with Cancel the default and Esc. The dialog goes through
  `MainWindow.ask_delete(items)`, which tests replace.

**Refused, with the reason** (a toast; nothing in the selection is touched)
- A library root: "<root> is a library folder. Use Remove from library to take it out of the library."
- An item under a job's lock, or a file being saved: "<name> is in use by <job>. Try again when it has
  finished." This is the same wording as tag saves (§7). The check uses `PathLocks`, and the delete holds a
  lock ("a delete") while it runs.
- **A place where Windows can't recycle**: a network share, a removable drive, a file bigger than that drive's
  Recycle Bin allows, or a drive whose Recycle Bin is turned off. The message is "MusicDesk only deletes to the
  Recycle Bin, and <drive> has none, so nothing was deleted. You can delete it in File Explorer.", with **Open in
  File Explorer**.

**Why the last check is needed** (the finding of 2026-10-16; inferred from the code, to be measured in M8 task 1)
- send2trash, which `fsops.send_to_recycle_bin` uses, sets `FOF_NOCONFIRMATION`. Its own progress sink says
  that refusing an item that can't be recycled "does not actual[ly] stop" the operation.
- Where Windows can't recycle, the file would therefore be deleted for good, silently. This also concerns the
  Rename tool's junk cleanup (§10), which uses the same function today.
- **M8 task 1 measures it.** Send a scratch file under `\\localhost\C$\…` (a network path, which has no
  Recycle Bin), then check whether it is gone and absent from the bin. Show Hugo the exact command first. Record
  the result here, labelled as measured.
- Then add `fsops.can_recycle(path) -> str | None` (None, or the reason). Candidates to try, keeping the one the
  measurement proves:
  - `GetDriveTypeW` refusing DRIVE_REMOTE and DRIVE_REMOVABLE;
  - `SHQueryRecycleBinW` on the volume;
  - the file size against the volume's Recycle Bin limit (`BitBucket\Volume\{GUID}` `MaxCapacity`,
    `NukeOnDelete`);
  - or a progress sink whose `PreDeleteItem` aborts the whole operation when `TSF_DELETE_RECYCLE_IF_POSSIBLE` is
    missing.
- `send_to_recycle_bin` checks `can_recycle` first and raises when it fails. The junk cleanup and Delete both go
  through it, so neither can delete permanently.

**The run**
- On the window's shell thread (send2trash initializes COM for each call), with one call for the whole
  selection.
- The status bar reports "Sent 5 items to the Recycle Bin." Per-item failures (for example a file open in another
  program) come in a toast, naming each file and the reason. The others still go.

**Afterwards**
- The parent folders are refreshed at once (`Indexer.request_path`; the watcher would also see it, but later).
- The selection moves to the row after the last deleted one, as in Explorer.
- Existing rules cover the rest:
  - when the open folder itself was deleted (from the tree), its nearest surviving ancestor opens (§5);
  - playlist tracks whose file left the index are dimmed (§15);
  - the side panel shows the new selection.
- There is no Undo in the app; the dialog's wording points to the Recycle Bin.

**Ground rule 1** gains this exception: sending files and folders the user selected to the Recycle Bin, after a
confirmation, and never permanently.

**Tests**
- Tests never touch the real Recycle Bin: the window's recycle function and `can_recycle` are injected, as the
  Rename tool's recorder is today.
- They cover:
  - files, a folder and a mixed selection;
  - roots refused;
  - a locked file;
  - an unrecyclable place;
  - a partial failure;
  - the refresh and the selection afterwards;
  - Del in the playlist panel still only removing from the playlist;
  - Shift+Del.
