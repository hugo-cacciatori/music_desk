# 11. Inline rename
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
  - The hint after a save uses the Rename tool's default masks (from M10, the shared masks of §21), and only appears when the name they give differs
    from the file's.
