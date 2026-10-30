# Refrain

A Windows app for looking after a local music library: find any track or lyric line, edit tags and covers, read
lyrics, and tidy albums (split single-file albums from their cue sheet, rename files from their tags, apply
ReplayGain), mostly from File Explorer's right-click menu.

Refrain works on your files where they are. It never copies or moves music, and never deletes anything for good.
Its only changes are the ones you ask for: tag edits, renames, ReplayGain tags and a `replaygain.log` per album,
the tracks of a split, trimmed copies and the cue sheets you save. What you leave checked in Tidy's list of
redundant files, and a cue sheet you save over, go to the Recycle Bin.

## Features

- **Right-click in File Explorer**: a **Refrain** submenu offers what fits what you clicked: Open in Refrain,
  ReplayGain (a folder as an album or as a discography, several albums, selected tracks as an album or each on its
  own), Remove ReplayGain tags, Tidy, Split or Edit a cue sheet, Trim a track, and Settings. Each opens a small
  window of its own, whether Refrain is open or not; several can run side by side, and only the files one is
  working on are refused to the others.
- **Library search (Ctrl+K).** Type any part of a folder or file name, a title, artist, album or lyric line.
  Accents and case are ignored, and filters such as `artist:`, `year:`, `ext:` or `lrclib:notfound` narrow it. In
  Lyrics mode the results are cards showing the matching lines.
- **Browse fast, even on slow drives.** Folders open instantly from an index that stays in step with the disk.
  The file list shows tag columns (title, artist, album, year, duration, format), plus ReplayGain and LRCLIB
  status. The **Folders** button (Ctrl+Shift+F) shows the folder tree.
- **Edit tags and covers** in FLAC, MP3, M4A/ALAC, Opus, Ogg Vorbis, WMA, WAV, AIFF, WavPack and more, one file or
  many at once. ID3 versions are kept as they were.
- **Lyrics from [LRCLIB](https://lrclib.net)**, looked up in the background for every track. Synced and plain
  lyrics are shown beside the file, with links to search Genius or Google when LRCLIB has none. Refrain never
  writes lyrics into your files.
- **Preview** rip logs, cue sheets, notes and scans beside the list, read-only, each text file in its own encoding.
- **Tidy** a whole folder tree, album by album: albums still in one file with their cue sheet are split into
  tracks, tracks are renamed with your file name masks, then ReplayGain runs where it's missing or for another
  target. You see the whole plan first and run it with one button. At the end, the same window lists what became
  redundant (split images and their cue sheets, pictures, videos and empty folders beside the music), every box
  checked: uncheck what to keep, then Delete selected sends the rest to the Recycle Bin.
- **ReplayGain** with the bundled [rsgain](https://github.com/complexlogic/rsgain). Each album run writes a fresh
  `replaygain.log`. Images still waiting to be split are left alone.
- **Split a single-file album**, such as an EAC rip (one FLAC, WAV or AIFF image and its `.cue`), into one FLAC
  file per track: right-click the `.cue` (Split this cue sheet…), or **Split** on its preview. A window lists the
  tracks it will write. Each is cut with the bundled [flac](https://xiph.org/flac/), tagged from the sheet with
  the album's cover, named with your file name masks, and checked sample for sample against its part of the
  original. Nothing is overwritten, and the image and its sheet are never touched.
- **Edit a cue sheet** (Edit this cue sheet…, or **Edit…** on its preview): the audio file it points to, start
  times and gaps, titles and the other fields, tracks to add or delete, the text encoding of old sheets. Save
  changes only the lines you edited. No sheet yet? **New cue sheet…** on an audio file's Metadata tab opens the
  editor on a new one filled from its tags; **Fill from track lengths…** sets the start times from a tracklist,
  and **Preview in player** plays the sheet as it is, unsaved edits included, in your default player (one that
  reads cue sheets).
- **Trim a track**: right-click one FLAC, WAV or AIFF file (Trim this track…), or **Trim…** on its Metadata tab.
  Drag the two handles on the waveform, or type the times, to drop a long silence or a hidden track at either end,
  hear the result with Preview in player, then Trim. A FLAC copy goes into a subfolder (`lite` by default) with
  the original's tags, checked sample for sample. The original is never touched.
- **Play** a folder, with or without its subfolders, or a selection in your default music player.
- **Open a folder from File Explorer**: type `refrain` in Explorer's address bar (as you would `cmd`). Refrain
  opens, or comes to the front, on that folder, and adds it to the library first if needed.
- **Options** (Settings in the right-click menu, or the … button): file name masks, ReplayGain's target and
  keywords, what Tidy renames and offers, how new cue sheets are saved, Trim's subfolder.

## Install

Download `Refrain-Setup-<version>.exe` from the
[latest release](https://github.com/hugo-cacciatori/refrain/releases/latest) and run it (64-bit Windows 10 1809 or
later, or Windows 11). It installs for all users in Program Files, or only for you if you choose that on its first
page. The setup isn't signed, so Windows SmartScreen may warn about it: choose "More info", then "Run anyway".

Installing over an earlier version keeps your library: the library folders, the LRCLIB results and your settings
come over on the first start, and the library is read again.

To uninstall, open the Refrain folder in the Start menu and choose "Uninstall Refrain", or use Settings › Apps ›
Installed apps. Refrain keeps its own data (the library index, settings and logs) in `%LOCALAPPDATA%\Refrain`;
uninstalling asks whether to delete it too. Your music folders are never touched.

## Build from source

The app is in `app/`: `Refrain` (the WinUI 3 app), `Refrain.Core` (no UI), `Refrain.Tests`, `RefrainMenu` (the
right-click handler) and `RefrainHere` (the address-bar keyword). You need the .NET 10 SDK, and for the installer
Visual Studio's C++ build tools (for Native AOT) and [Inno Setup 6](https://jrsoftware.org/isinfo.php).

```powershell
dotnet build app\Refrain.slnx
dotnet test app\Refrain.Tests
powershell -ExecutionPolicy Bypass -File app\packaging\build_installer.ps1
```

The installer build checks itself first: a copy outside the repository runs its self-test and starts its window
off-screen; any failure stops the build. The tests run on sample files in `app/Refrain.Tests/Fixtures`, never on a
real library.

## Licence

Refrain is free software under the [GNU General Public License, version 3 or later](LICENSE), with an
[additional permission](resources/licenses/GPL-3.0-exception.txt) to combine it with the Windows App SDK and the
Windows SDK projection for .NET, whose source isn't published. It bundles rsgain
(BSD-2-Clause) with FFmpeg (LGPL-2.1-or-later) and Fraunhofer FDK AAC, and flac (GPL-2.0-or-later, with libFLAC
under Xiph's BSD-style licence); their licence texts are in [`resources/licenses/`](resources/licenses/). It is
built on .NET (MIT) and the Windows App SDK (Microsoft Software License Terms), and uses TagLib# (LGPL-2.1) and SQLite (public domain).
