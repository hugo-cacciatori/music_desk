# 3. Library roots
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
