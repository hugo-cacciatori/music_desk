# MusicDesk

A Windows desktop app for looking after a local music library: browse it like File Explorer, edit tags, read
lyrics, tidy albums (rename files from their tags, apply ReplayGain), split single-file albums into tracks
from their cue sheet, and trim a track's start or end into a copy.

MusicDesk works on your files where they are. It never copies or moves music, and never deletes anything for
good. Its only changes are the ones you ask for: tag edits, renames, ReplayGain tags, a `replaygain.log` per
album, the playlists you export, the tracks of a split, trimmed copies and the cue sheets you save. What you
delete, a cue sheet you save over, and what you leave checked in the Redundant files window (Clean up, Tidy), go
to the Recycle Bin.

## Features

- **Browse fast, even on slow drives.** Folders open instantly from an index that stays in step with the disk.
  The file list shows tag columns (title, artist, album, year, duration, format), plus ReplayGain and LRCLIB
  status columns.
- **Edit tags** in FLAC, MP3, M4A/ALAC, Opus, Ogg Vorbis, WMA, WAV, AIFF, WavPack, Monkey's Audio, Musepack and
  more (the formats [mutagen](https://github.com/quodlibet/mutagen) can tag), one file or many at once, covers
  included. ID3 versions are kept as they were.
- **Lyrics from [LRCLIB](https://lrclib.net)**, looked up in the background for every track. Synced and plain
  lyrics are shown beside the file, with links to search Genius or Google when LRCLIB has none. Without an
  internet connection, lookups pause and resume by themselves. MusicDesk never writes lyrics into your files.
- **Library search (Ctrl+K).** Type any part of a folder or file name, a title, artist, album or lyric line.
  Names are found as soon as a folder is listed, before its tags are read. Accents and case are ignored, and
  filters such as `artist:`, `year:`, `ext:`, `lrclib:notfound` or `rg:none` narrow it. The matching lyric line
  is shown in the results.
- **Open a folder from File Explorer**: type `musicdesk` in Explorer's address bar (as you would `cmd`).
  MusicDesk opens, or comes to the front, on that folder, and adds it to the library first if it isn't in it.
- **Advanced search** (the toolbar's Advanced search button), a window of its own beside the app: lists of albums to go
  through and fix by hand, such as the albums missing ReplayGain tags or the EAC rips not split yet. Double-click
  an album to open its folder; Refresh search updates the list as you fix them.
- **Preview** rip logs, cue sheets, notes, `.nfo` files and scans beside the list, read-only. Each text file is
  read in its own encoding, such as an EAC log's UTF-16 or an `.nfo`'s DOS code page.
- **Delete** files and folders (Del) from the file list, the folder tree or search results. They go straight to
  the Recycle Bin (no question: restore them from there), never deleted for good, and where Windows has no Recycle Bin (a network
  share, a removable drive) MusicDesk refuses and offers File Explorer instead.
- **Tools**, always one click away on the toolbar's second row: a click starts the tool, and its hover text says
  what it will act on (the open folder or the selection). Their options are in **File > Options…**:
  - **Rename from tags**, with masks such as `{track:02} {title}`. You see a preview first, and `.lrc` files
    follow their audio file. Renaming never deletes anything.
  - **ReplayGain** for an album or a whole discography, with the bundled [rsgain](https://github.com/complexlogic/rsgain).
    An album's own folder runs at once; anything else (subfolders, a selection, a discography) first lists every
    file and what will happen to it, with OK and Cancel. Images still waiting to be split are left alone. Each run
    writes a fresh `replaygain.log`; the ReplayGain tags can also be removed from tracks or folders.
  - **Tidy**: a whole folder tree at once, album by album. Albums still in one file with their cue sheet are split
    into tracks, tracks are renamed to the masks, then ReplayGain runs where it's missing or for another target.
    You see the whole plan first and run it with one button; at the end, the Redundant files window lists the
    images and cue sheets the splits made redundant, and the pictures, videos and empty folders beside the music.
  - **Clean up**: junk files (`.nfo`, `.sfv`, `.txt`, per-track cue sheets…) and, if you tick them in its options,
    pictures and videos beside the music and folders holding nothing else, listed in the Redundant files window.
    In that window every box is checked: uncheck what to keep, then Delete selected sends the rest to the Recycle
    Bin; closing it keeps everything.

  Jobs run in the background, and you can keep browsing meanwhile; the status bar's Jobs button (Ctrl+J) shows them.
- **Split a single-file album**, such as an EAC rip (one FLAC, WAV or AIFF image, its `.cue` and its log), into
  one FLAC file per track: select the `.cue`, then **Split** on its preview to split it at once with the defaults
  (every track, into its folder), or **Edit…** to check it first (**Play** plays the album). The Split page shows the
  whole sheet, editable: which audio file it points to, start times and gaps, titles and the other fields,
  tracks to add or delete, and the text encoding of old sheets. Save writes your edits back into the `.cue`,
  changing only the lines you edited; Save as makes another version of the sheet, so one image can be split in
  several ways; Preview in player plays it. The page only edits: Split, on the sheet's preview, writes every
  track into the album's folder. Each one is cut with the bundled
  [flac](https://xiph.org/flac/), tagged from the sheet with the album's cover, named with your file name masks,
  and checked sample for sample against its part of the original. Nothing is overwritten (a taken name gets a
  number), and the image, its sheet and its log are never touched.

  No sheet yet? Select the audio file and click **New cue sheet…** on the Metadata tab: the Split page opens on a
  new sheet filled from its tags. **Fill from track lengths…** sets the start times from a tracklist's lengths,
  and **Preview in player** opens the sheet as it is, unsaved edits included, in your default player (one that
  reads cue sheets, such as foobar2000), so you can hear each cut before anything is written.
- **Trim a track**: select one FLAC, WAV or AIFF file and click **Trim…** on the Metadata tab. Drag the two
  handles on the waveform (or type the times) to drop a long silence or a hidden track at either end, hear the
  result with Preview in player, then Trim. A FLAC copy goes into a subfolder (`lite` by default) with the
  original's tags, checked sample for sample against its part of the original. The original is never touched.
- **Play** a folder, a selection or search results in your default music player, through a temporary playlist.
- **Build a playlist** (the toolbar's Playlist builder button, Ctrl+Shift+P): drag folders and songs in from the folder tree,
  the file list or search results, in the order Explorer shows them. Rearrange it by dragging, with Ctrl+↑/↓ or
  by sorting on a column, then play it or export it as a `.m3u8`, with paths relative to the playlist so it keeps
  working when it moves with your music. It is kept until you clear it.
- **Says what it does.** Hover any button or field for a one-line description of what happens to which files
  (with its shortcut); the less obvious menu entries describe themselves in the status bar.
- **No accidental changes from the mouse wheel.** Scrolling a page over a number field, a drop-down or a slider
  scrolls the page and never changes the value.

## Install

Download `MusicDesk-Setup-<version>.exe` from the
[latest release](https://github.com/hugo-cacciatori/music_desk/releases/latest) and run it (64-bit Windows 10 or
11). It installs for all users in Program Files, or only for you if you choose that on its first page. The setup
isn't signed, so Windows SmartScreen may warn about it: choose "More info", then "Run anyway".

To uninstall, open the MusicDesk folder in the Start menu and choose "Uninstall MusicDesk" (or type "uninstall
musicdesk" in Start), or use Settings › Apps › Installed apps. Start's own Uninstall button on MusicDesk only opens
that Settings page.

MusicDesk keeps its own data (the library index, settings and logs) in `%LOCALAPPDATA%\MusicDesk`. Uninstalling
asks whether to delete it too. Your music folders are never touched.

## Build from source

You need [uv](https://docs.astral.sh/uv/), and [Inno Setup 6](https://jrsoftware.org/isinfo.php) for the
installer. Python 3.13 is fetched by uv if needed.

```bash
uv sync
uv run python -m music_desk                    # run from source
uv run pytest                                  # the tests the changes need (--all: every test)
uv run ruff check .
uv run python packaging/build_installer.py     # dist/MusicDesk/ and dist/installer/MusicDesk-Setup-<version>.exe
```

The tests run on generated sample files, never on a real library.

## Licence

MusicDesk is free software under the [GNU General Public License, version 3 or later](LICENSE). It bundles
rsgain (BSD-2-Clause) with FFmpeg (LGPL-2.1-or-later) and Fraunhofer FDK AAC, and flac (GPL-2.0-or-later, with
libFLAC under Xiph's BSD-style licence), and uses Qt for Python
(LGPL-3.0), mutagen (GPL-2.0-or-later), watchdog, natsort and Send2Trash. Their licence texts are in
[`resources/licenses/`](resources/licenses/), and the app lists them under Help › About.
