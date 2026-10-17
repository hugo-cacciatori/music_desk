# MusicDesk: detailed spec (index)

The spec is split into one file per section in `docs/spec/`. "SPEC §n" means `docs/spec/NN-*.md`. Read only the
files the current milestone lists in `../CLAUDE.md`; each one stands alone.

Rules:
- Section numbers are stable; a new section takes the next number.
- When a decision changes behaviour, update the section and add a dated row to `DECISIONS.md`, together.
- Sections hold the behaviour as specified, then "As built" notes for how it was implemented. When an as-built
  note supersedes an earlier plan, remove the plan.
- Measurements: the responsiveness figures for this machine (M2, re-measured 2026-10-15) are in §18
  "Performance"; search in §6, the scanner in `HISTORY.md`.

| §  | File | Covers |
|----|------|--------|
| 1  | [01-formats.md](spec/01-formats.md) | Formats, tag families, the field mapping per format, rsgain's ReplayGain keys |
| 2  | [02-data-storage.md](spec/02-data-storage.md) | App data, the SQLite schema (v3), DbWriter and read connections |
| 3  | [03-library-roots.md](spec/03-library-roots.md) | Adding, merging, removing roots; offline roots |
| 4  | [04-indexing.md](spec/04-indexing.md) | Scanner phases, priorities, rescans, the watcher, revalidation on open |
| 5  | [05-browser.md](spec/05-browser.md) | Tree, file list, columns, address bar, navigation, context menus |
| 6  | [06-library-search.md](spec/06-library-search.md) | Ctrl+K search, filters, the seek_lyrics parity requirement |
| 7  | [07-side-panel.md](spec/07-side-panel.md) | Side panel, Metadata tab, multi-file edits, covers, folder summary, .lrc import |
| 8  | [08-lyrics-tab.md](spec/08-lyrics-tab.md) | Lyrics tab states, Save to file tags |
| 9  | [09-lyrics-service.md](spec/09-lyrics-service.md) | LRCLIB lookups, queue, breaker, auto-save, lyrics_cache.json import |
| 10 | [10-tools.md](spec/10-tools.md) | Tools page: Rename (junk rules, .cue/.lrc), ReplayGain, Tidy, jobs panel |
| 11 | [11-inline-rename.md](spec/11-inline-rename.md) | F2 renames, sidecars, Undo, the rename hint |
| 12 | [12-settings.md](spec/12-settings.md) | No Settings dialog; where each preference lives; About |
| 13 | [13-packaging.md](spec/13-packaging.md) | rsgain, flac, PyInstaller, the installer (uninstall shortcut), licences |
| 14 | [14-playback.md](spec/14-playback.md) | Default app, temporary .m3u8 playlists |
| 15 | [15-playlist-builder.md](spec/15-playlist-builder.md) | The playlist builder panel, drag and drop, export |
| 16 | [16-architecture.md](spec/16-architecture.md) | Threads, signals, models, shutdown, controllers (M11) |
| 17 | [17-porting.md](spec/17-porting.md) | What was ported from the scripts, preserved behaviours, deliberate changes |
| 18 | [18-testing.md](spec/18-testing.md) | Test layers, fixtures, parity, slow tests, performance, static types (M11) |
| 19 | [19-delete.md](spec/19-delete.md) | Delete to the Recycle Bin (M8), and the recyclability check |
| 20 | [20-file-preview.md](spec/20-file-preview.md) | Read-only preview of text and image files in the side panel (M9) |
| 21 | [21-name-masks.md](spec/21-name-masks.md) | The shared file name masks and the File names card (M10) |
| 22 | [22-cue-split.md](spec/22-cue-split.md) | Cutting single-file albums into FLAC tracks from their .cue, with flac.exe (M10); new sheets, Preview in player, Fill from track lengths (M13) |
| 23 | [23-trim.md](spec/23-trim.md) | Trimming one track's start and end into a FLAC copy in a subfolder (M13) |
| 24 | [24-hover-texts.md](spec/24-hover-texts.md) | A short hover text on every button and control (later, not scheduled) |
| 25 | [25-scroll-wheel.md](spec/25-scroll-wheel.md) | The mouse wheel never changes an input's value (Ground rule 7; later, not scheduled) |
