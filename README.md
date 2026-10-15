# MusicDesk

A Windows desktop app for looking after a local music library: browse it like File Explorer, edit tags, read
lyrics, and tidy albums (rename files from their tags, apply ReplayGain).

MusicDesk works on your files where they are. It never copies, moves or deletes music. Its only changes are the
ones you ask for: tag edits, renames, ReplayGain tags and a `replaygain.log` per album. Junk files go to the
Recycle Bin, and only when you tick that option and confirm.

## Features

- **Browse fast, even on slow drives.** Folders open instantly from an index that stays in step with the disk.
  The file list shows tag columns (title, artist, album, year, duration, format), plus ReplayGain, lyrics-tag and
  LRCLIB status columns.
- **Edit tags** in FLAC, MP3, M4A/ALAC, Opus, Ogg Vorbis, WMA, WAV, AIFF, WavPack, Monkey's Audio, Musepack and
  more (the formats [mutagen](https://github.com/quodlibet/mutagen) can tag), one file or many at once, covers
  included. ID3 versions are kept as they were.
- **Lyrics from [LRCLIB](https://lrclib.net)**, looked up in the background for every track. Synced and plain
  lyrics are shown beside the file. One click saves them into the file's tags, or auto-save can fill empty
  lyrics tags.
- **Library search (Ctrl+K).** Type any part of a title, artist, album, file name or lyric line. Accents and
  case are ignored, and filters such as `artist:`, `year:`, `ext:`, `lrclib:notfound` or `rg:none` narrow it.
  The matching lyric line is shown in the results.
- **Tools** for a folder or a selection:
  - **Rename from tags**, with masks such as `{track:02} {title}`. You see a preview first, `.lrc` files
    follow their audio file, and an optional junk cleanup sends files to the Recycle Bin.
  - **ReplayGain** for an album or a whole discography, with the bundled [rsgain](https://github.com/complexlogic/rsgain).
  - **Tidy**: the rename, then ReplayGain.

  Jobs run in the background, and you can keep browsing meanwhile.
- **Play** a folder, a selection or search results in your default music player, through a temporary playlist.

## Install

Download `MusicDesk-Setup-<version>.exe` from the
[latest release](https://github.com/hugo-cacciatori/music_desk/releases/latest) and run it (64-bit Windows 10 or
11). It installs for all users in Program Files, or only for you if you choose that on its first page. The setup
isn't signed, so Windows SmartScreen may warn about it: choose "More info", then "Run anyway".

MusicDesk keeps its own data (the library index, settings and logs) in `%LOCALAPPDATA%\MusicDesk`. Uninstalling
asks whether to delete it too. Your music folders are never touched.

## Build from source

You need [uv](https://docs.astral.sh/uv/), and [Inno Setup 6](https://jrsoftware.org/isinfo.php) for the
installer. Python 3.13 is fetched by uv if needed.

```bash
uv sync
uv run python -m music_desk                    # run from source
uv run pytest                                  # the tests (add -m "not slow" to skip the rsgain runs)
uv run ruff check .
uv run python packaging/build_installer.py     # dist/MusicDesk/ and dist/installer/MusicDesk-Setup-<version>.exe
```

The tests run on generated sample files, never on a real library. `docs/SPEC.md` is the detailed specification.

## Licence

MusicDesk is free software under the [GNU General Public License, version 3 or later](LICENSE). It bundles
rsgain (BSD-2-Clause) with FFmpeg (LGPL-2.1-or-later) and Fraunhofer FDK AAC, and uses Qt for Python
(LGPL-3.0), mutagen (GPL-2.0-or-later), watchdog, natsort and Send2Trash. Their licence texts are in
[`resources/licenses/`](resources/licenses/), and the app lists them under Help › About.
