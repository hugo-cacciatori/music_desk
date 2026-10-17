# 29. Opening MusicDesk from File Explorer's address bar
Specified with Hugo on 2026-10-17 (M18).

**The need** (Hugo): as typing `cmd` in File Explorer's address bar opens a terminal in that folder, a keyword
there opens MusicDesk on that folder: the app starts, or comes to the front if it is already open, and shows the
folder, adding it to the library first when it isn't in it.

## The keyword: `musicdesk`
- File Explorer runs what is typed in its address bar as a program, with the shown folder as the working
  directory, and finds programs by the **App Paths** registry key (`…\CurrentVersion\App Paths\<name>.exe`), as
  for `wordpad`. The installer registers `musicdesk.exe` there. `md` was rejected: it is the console's mkdir.
- The key points to **`MusicDeskHere.exe`**, a second small program installed beside `MusicDesk.exe`, not to
  the app itself. It reads its working directory and starts `MusicDesk.exe "<that folder>"`. So only the keyword
  opens the working directory: a start from the Start menu, the desktop, Win+R or anything else behaves as before
  (no guess from the working directory, which those starts set to the app's folder or the user's).
- The key is written for all users (HKLM) on an admin install and for the user (HKCU) on a per-user one (Inno's
  `HKA`), and removed by the uninstaller.
- `MusicDesk.exe "<folder>"` (and `python -m music_desk "<folder>"`) also works by itself: a folder argument
  opens that folder, the same way.

## One MusicDesk at a time
- MusicDesk becomes **single-instance** (a second copy on the same index and settings would fight over them): a
  second start, whatever started it, hands its folder (or nothing) to the running one through a local socket
  (`QLocalServer`, a name per Windows user) and exits at once. The running window comes to the front (restored
  if minimized) and opens the folder.
- A run from source uses its own name, so it never hands over to an installed MusicDesk.

## Opening the folder
- **Indexed**: the file list shows it, as a double-click in Advanced search does (`show_folder`): unsaved
  Metadata edits ask first, the Ctrl+K page and the Tools page are left.
- **Inside a library folder, not indexed yet** (made since the last scan, or the scan hasn't reached it): that
  path is rescanned first, then shown.
- **Outside the library**: it is added, as the sidebar's Add folder… does (a parent of library folders merges them, §3).
  The status bar says so ("Added D:\Music\New to the library."), and the folder opens as soon as the scan has
  indexed it.
- The wait for a scan lasts at most 2 minutes; a newer request replaces it. A path that isn't a folder (gone, a
  file) says so in the status bar, and nothing is added.
- Ground rule 1 is unchanged: adding a folder to the library only reads it.

## As built (M18 task 1, 0.6.3)
- `music_desk/here.py` `main()` (entry `packaging/here_launcher.py`): `MusicDesk.exe` beside its own exe, started
  with the working directory as its one argument (`subprocess.Popen`, cwd the app's folder), after
  `foreground.allow_foreground()`. The spec builds it as a second `EXE` in the same `COLLECT`, from its own
  `Analysis` that leaves Qt and the other libraries out.
- `music_desk/instance.py`: `server_name()` ("MusicDesk-<user>", "-dev" from source), `hand_over(folder)` (a
  `QLocalSocket`, 500 ms to connect, one JSON line) and `InstanceServer` (`folderRequested`). `app.main()` hands
  over and exits when another copy answers, else listens; `folder_argument` takes the first argument that isn't
  an option.
- `MainWindow.open_from_outside(path)`: `bring_to_front()`, then `show_folder` when indexed; otherwise
  `add_folders([path])` (outside every root) or `indexer.request_path(path)` (inside one), and
  `_waiting_folder` is retried on the indexer's progress, folderUpdated and scanFinished and on rootsChanged,
  for `WAIT_FOR_INDEX_S` (120 s).
- Measured: the hand-over between two processes works; inside one process it can't (the blocking client and the
  server share the thread), so the test starts a real second process.
- Not checked here: Explorer's address bar on the installed app (the App Paths key only exists after an install).

## Tests
- The folder argument: an indexed folder opens; one inside a root but not indexed opens after its rescan; one
  outside the library is added, then opens; a missing path says so.
- Single instance: a second start's message (from a real second process) reaches the running server, which opens the
  folder; with no folder, it only comes to the front.
- MusicDeskHere: it starts MusicDesk.exe with its working directory (a fake launcher).
- Packaging: the spec builds both programs; the installer script registers the App Paths key.
