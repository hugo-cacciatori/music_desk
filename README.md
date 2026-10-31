# Refrain

A Windows app for looking after a local music library, from File Explorer's right-click menu: find any track or
lyric line, edit tags and covers, read lyrics, and tidy albums (split single-file albums from their cue sheet, rename
files from their tags, apply ReplayGain).

Refrain works on your files where they are. It never copies or moves music, and never deletes anything for good.
Its only changes are the ones you ask for: tag edits, renames, ReplayGain tags and a `replaygain.log` per album,
the tracks of a split, trimmed copies and the cue sheets you save. What you leave checked in Tidy's list of
redundant files, and a cue sheet you save over, go to the Recycle Bin.

## How to use it

Right-click a folder, a folder's empty space, or some files in File Explorer, then **Refrain**. The submenu offers
what fits what you clicked, and each entry opens a small window of its own. Several can run side by side; only the
files one is working on are refused to the others. Each window ends with a report of what was done, which stays
until you close it.

Refrain also starts on its own, from the Start menu, Windows search or its desktop icon: it opens the Search window,
ready for a search.

## Features

- **Open in Refrain** (on a folder or a music file): the Search window, opened on the discography of what you
  right-clicked: a song in its album, an album, an artist's albums, or, for a folder of several artists, the whole
  library with theirs lit. The folder joins the library if it wasn't in it, and opens once it has been read.
- **The discography view** rebuilds your library from its tags, wherever the files are: every artist as a bubble, an
  artist's albums from earliest to latest, an album's songs and their lyrics. The mouse wheel zooms from the library
  into an artist and an album, and back out; drag to move around the library. Names that differ only in case, accents
  or punctuation are one artist, and "CD1", "Disc 2" or "[Remastered]" don't split an album. An info panel on the
  right shows the open artist, album or song (tags, format, ReplayGain, where the file is); right-click anything to
  open it in File Explorer.
- **The player**, a bar along the bottom of the Search window: a click on a song in the discography view plays it,
  and its album goes on, starting over after the last song (or stopping, in Options). Where LRCLIB has timed lyrics,
  the line being sung lights up, and a click on a line plays from there. Refrain reopens on the last song, paused.
  It never plays a file while ReplayGain, Tidy or another task is changing its folder: it stops and says why, or
  skips those songs if you choose that in Options.
- **The Search window**, over your whole library. Type any part of a folder or file name, a title, artist, album or
  lyric line; accents and case are ignored, and filters such as `artist:`, `year:`, `ext:` or `lrclib:notfound`
  narrow it. A click on a result previews it on the right while about 30 seconds of it play (silent while the player
  plays; the speaker button mutes previews): a song's tags, file and lyrics, an album's songs and ReplayGain state,
  an artist's albums, and for a folder a Tidy section that lights up when Tidy has something to do there, saying
  what and to which file, with Run Tidy. Rate songs there with up to five stars, by halves, and add a note; ratings stay in Refrain, not
  in your files, and follow the song's tags. A double click opens the result in the discography view; right-click it
  to open it in File Explorer. The … button adds library folders, and Options' Library page rescans or removes them.
- **Lyrics** from [LRCLIB](https://lrclib.net), looked up in the background for every track. The Search window's
  Lyrics mode shows the matching lines as cards; a click previews the song with its whole lyrics, with links to
  search Genius or Google when LRCLIB has none. Refrain never writes lyrics into your files.
- **Edit tags…** (on music files): the tags of one file or many at once, and the cover, in FLAC, MP3, M4A/ALAC,
  Opus, Ogg Vorbis, WMA, WAV, AIFF, WavPack and more. ID3 versions are kept as they were. Nothing changes until you
  save.
- **Tidy** (on folders): a whole folder tree, album by album. Albums still in one file with their cue sheet are
  split into tracks, tracks are renamed with your file name masks, then ReplayGain runs where it's missing or for
  another target. You see the whole plan first and run it with one button. At the end, the same window lists what
  became redundant (split images and their cue sheets, pictures, videos and empty folders beside the music), every
  box checked: uncheck what to keep, then Delete selected sends the rest to the Recycle Bin.
- **ReplayGain** with the bundled [rsgain](https://github.com/complexlogic/rsgain): a folder as an album or as a
  discography, several folders each as an album, selected tracks as an album or each on its own. The entries show
  your target loudness. Each album run writes a fresh `replaygain.log`; images still waiting to be split are left
  alone. **Remove ReplayGain tags…** (on tracks) asks first.
- **Split this cue sheet…** (on a `.cue`): cuts a single-file album, such as an EAC rip (one FLAC, WAV or AIFF image
  and its `.cue`), into one FLAC file per track. A window lists the tracks it will write first. Each is cut with the
  bundled [flac](https://xiph.org/flac/), tagged from the sheet with the album's cover, named with your file name
  masks, and checked sample for sample against its part of the original. Nothing is overwritten, and the image and
  its sheet are never touched.
- **Edit this cue sheet…** (on a `.cue`) and **New cue sheet…** (on a FLAC, WAV or AIFF file): the audio file it
  points to, start times and gaps, titles and the other fields, tracks to add or delete, the text encoding of old
  sheets. Save changes only the lines you edited. **Fill from track lengths…** sets the start times from a
  tracklist, and **Preview in player** plays the sheet as it is, unsaved edits included, in your default player (one
  that reads cue sheets).
- **Trim this track…** (on a FLAC, WAV or AIFF file): drag the two handles on the waveform, or type the times, to
  drop a long silence or a hidden track at either end, hear the result with Preview in player, then Trim. A FLAC
  copy goes into a subfolder (`lite` by default) with the original's tags, checked sample for sample. The original
  is never touched.
- **Settings** (always): file name masks, ReplayGain's target and keywords, what Tidy renames and offers, how new cue
  sheets are saved, Trim's subfolder, the player, and your library folders.
- **Light and dark**: a light, airy look of soft bubbles, and a dusk version when Windows is in dark mode.

## Install

Download `Refrain-Setup-<version>.exe` from the
[latest release](https://github.com/hugo-cacciatori/refrain/releases/latest) and run it (64-bit Windows 10 1809 or
later, or Windows 11). It installs for all users in Program Files, or only for you if you choose that on its first
page, and adds Refrain to the Start menu and, unless you untick it, a desktop icon. The setup isn't signed, so
Windows SmartScreen may warn about it: choose "More info", then "Run anyway".

Installing over an earlier version keeps your library: the library folders, the LRCLIB results and your settings
come over the first time Refrain starts, and the library is read again. If the Refrain submenu doesn't show, or
still shows the old entries, restart File Explorer (Task Manager › Windows Explorer › Restart) or sign out and in.

To uninstall, open the Refrain folder in the Start menu and choose "Uninstall Refrain", or use Settings › Apps ›
Installed apps. Refrain keeps its own data (the library index, settings and logs) in `%LOCALAPPDATA%\Refrain`;
uninstalling asks whether to delete it too. Your music folders are never touched.

## Build from source

The app is in `app/`: `Refrain` (the WinUI 3 app), `Refrain.Core` (no UI), `Refrain.Tests` and `RefrainMenu` (the
right-click handler). You need the .NET 10 SDK, and for the installer
Visual Studio's C++ build tools (for Native AOT) and [Inno Setup 6](https://jrsoftware.org/isinfo.php).

```powershell
dotnet build app\Refrain.slnx
cd app; dotnet test --project Refrain.Tests; cd ..
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
