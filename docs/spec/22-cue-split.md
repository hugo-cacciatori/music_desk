# 22. Cue split
Specified with Hugo on 2026-10-16 (M10), redesigned with him the same day (see "In the app"). Some albums are
ripped as **one audio file plus a `.cue` sheet** that says where each track starts. From one selected `.cue`, the
app shows a Split page where the sheet can be edited, then cuts the file into one FLAC file per ticked track, named
with the shared masks (§21) and tagged from the sheet, and proves the cut lost nothing.

**The `.cue` is the split's only source of truth** (Hugo, 2026-10-16). The app stores no split state: no
remembered options, no remembered edits. Edits are kept only by saving them into a `.cue`.

## The cue sheet
A `.cue` is a small text file. An example, as EAC writes them:

    REM GENRE "Alternative Rock"
    REM DATE 1997
    REM DISCNUMBER 1
    REM TOTALDISCS 2
    REM DISCID 8C0B6A0B
    REM COMMENT "ExactAudioCopy v1.6"
    PERFORMER "Fixture Artist"
    TITLE "Fixture Album"
    FILE "Fixture Artist - Fixture Album.flac" WAVE
      TRACK 01 AUDIO
        TITLE "Opening"
        SONGWRITER "A. Writer"
        ISRC GBAYE9700001
        INDEX 01 00:00:00
      TRACK 02 AUDIO
        TITLE "Second Song"
        FLAGS DCP
        INDEX 00 04:41:50
        INDEX 01 04:43:37
      TRACK 03 AUDIO
        TITLE "Duet"
        PERFORMER "Fixture Artist & Guest"
        INDEX 01 09:01:05

| Line | Meaning | Used for |
|------|---------|----------|
| `REM GENRE`, `REM DATE` | album genre and year | genre, year |
| `REM DISCNUMBER`, `REM TOTALDISCS` | disc n of N (foobar2000 and EAC conventions) | disc, disc total |
| `REM DISCID`, `REM COMMENT`, `CATALOG`, `FLAGS` | the rip's identity, the ripper, the CD's flags | not used |
| `PERFORMER` / `TITLE` before the first TRACK | album artist and album title | albumartist, album; artist when a track has none |
| `FILE "<name>" WAVE` | the audio file the times refer to | the source |
| `TRACK nn AUDIO` | a track (`MODE1/2352` and other data tracks are skipped) | track number; the count gives the track total |
| `TITLE`, `PERFORMER`, `SONGWRITER`, `ISRC` in a TRACK | that track's title, artist, composer, ISRC | title, artist, composer, ISRC |
| `INDEX 01 mm:ss:ff` | where the track starts (`ff` = 1/75 s, a CD frame) | the cut points |
| `INDEX 00 mm:ss:ff` | where the gap before it starts | not a cut point (see below) |
| `PREGAP`, `POSTGAP` | silence that isn't in the file | ignored |

**Which sheets can be split:** a sheet whose FILE lines all name one audio file present in the `.cue`'s folder,
with at least two tracks (the single-file album rule of §10 A). Any `.cue` opens the Split page, so a wrong FILE
line can be fixed there; other sheets (one FILE per track, a missing file) can be edited and saved, but Split stays
disabled with the reason. Cue sheets embedded inside FLAC files are not read (not chosen, 2026-10-16).

**Sources:** FLAC, WAV and AIFF, which is what flac.exe reads (Hugo's images are FLAC or WAV, 2026-10-16). Any
other format is refused, naming it: "APE isn't supported: the split reads FLAC, WAV and AIFF." 32-bit float WAV,
which FLAC can't hold, is refused the same way. **The tracks are always FLAC.**

## Cutting
- Times become samples: samples = round((mm·60 + ss)·rate + ff·rate/75). At 44.1 kHz a frame is exactly 588
  samples, and at 48 kHz it is 640.
- **Gaps go to the end of the previous track** (EAC's default, "append gaps"): each track runs from its INDEX 01
  to the next track's INDEX 01, and the last one to the end of the file. INDEX 00 is never a cut point.
- **Audio before track 1's INDEX 01** (a hidden track, or an extra pregap) is prepended to track 1 when it is
  under 2 s or is digital silence. Otherwise it becomes track 0, titled "Hidden track", named with the masks like
  the others. Silence is checked by decoding just that range.
- Every sample of the source belongs to exactly one track, in order. With every track ticked, the proof below
  checks that nothing was lost; with some unticked, it checks each written track against its range.
- Not splittable, naming the reason: times that don't increase, a time past the end of the audio, and fewer than
  two audio tracks. On the Split page these are live validation errors (below).

## Text encoding
- Cue sheets have no declared encoding. **Auto** tries a BOM (UTF-8, UTF-16), then UTF-8, then Windows-1252, the
  same order as the file preview (§20).
- Old Cyrillic, Japanese or Chinese sheets come out garbled that way. The Split page therefore has a picker, with
  Auto (naming what it chose), UTF-8, Windows-1252 (Western), Windows-1251 (Cyrillic), Shift-JIS (Japanese), GBK
  (Chinese, simplified), Big5 (Chinese, traditional) and EUC-KR (Korean). Changing it re-reads the sheet; with
  unsaved edits it asks first, since they would be lost.
- When Auto had to fall back to Windows-1252, the page shows "If the titles look wrong, pick the cue sheet's
  encoding."
- Parsing happens in `core/cue.py` (no Qt): `read_cue_sheet(path, encoding=None) -> CueSheet` (album fields,
  tracks with their fields and INDEX times, the FILE name, the encoding used, warnings). `rename.read_cue` stays
  for the Rename tool's FILE/TRACK count.

## Tags and names
- Each track's tags:
  - title;
  - artist: the track's PERFORMER, else the album's;
  - albumartist and album (the album PERFORMER and TITLE);
  - track and track total;
  - disc and disc total;
  - year, genre, composer (SONGWRITER) and ISRC (written as the Vorbis comment `ISRC`).
- Album-level values missing from the sheet are taken from the source file's own tags (album, album artist, year,
  genre). The source's embedded **front cover** is copied into every track. Nothing else is copied: its comment,
  lyrics and ReplayGain values described the whole file.
- Track and track total are the sheet's numbering, whatever tracks are ticked: extracting only track 3 of 11
  gives a file tagged 3 of 11.
- Names: the shared masks (§21) over those fields, sanitized, with `.flac`. When no mask fits a ticked track (say
  it has no title and every mask needs one), the page marks it, naming the missing field, and Split is disabled.
- **Nothing is ever overwritten.** A ticked track's name taken by an existing file, or given to two ticked tracks,
  is marked as a clash and Split is disabled. Unticked tracks are not checked.

## In the app
Redesigned with Hugo on 2026-10-16, replacing the 0.2.10 Tools card (see "Replaced" below). There is **one way
in**, and **one page with every control**; you can't split without going through it.

**The entry point**
- The side panel's preview of any `.cue` (§20) shows a **Split into tracks…** button. The side panel previews
  only a single selected file, so this is "one `.cue` selected". There is no menu entry, and nothing on the Tools
  page.
- It opens the **Split page** in the centre area, non-modal with a back button, as the Tools page does (§10), so
  browsing stays possible. One Split page at a time: opening another `.cue` while the page has unsaved edits asks
  first.

**The Split page**, top to bottom:
- **Source:** the FILE line, editable. It must name a FLAC, WAV or AIFF file in the `.cue`'s folder. Then its
  format and length, or why it can't be split ("APE isn't supported: the split reads FLAC, WAV and AIFF.", "File
  not found", "This sheet names 11 files: only single-file albums can be split").
- **Encoding:** the picker (above).
- **Album fields**, editable: performer, title, genre (`REM GENRE`), date (`REM DATE`), disc and total discs
  (`REM DISCNUMBER`, `REM TOTALDISCS`), catalog, songwriter, and **Other lines**: the album-level lines that have
  no field of their own (`REM DISCID`, `REM COMMENT`, unknown lines), editable as text.
- **The track table**, one row per track:
  - columns: tick, #, Start (`mm:ss:ff`), Gap (INDEX 00, optional), Length (computed), Title, Performer,
    Songwriter, ISRC, Flags, and New name (computed live from the masks, §21);
  - the selected track's **Other lines** (INDEX 02 and later, PREGAP, POSTGAP, REM, unknown lines) show in a box
    under the table, editable as text;
  - **Add track** inserts a track after the selected one, starting halfway through it; **Delete track** removes
    the selected tracks. A deleted track's audio joins the previous track (gaps go to the previous track), or the
    next one when it was track 1. Tracks are renumbered 1…N after each change;
  - a hidden track (above) shows as row 0, with its tick.
- **Live validation:** a bad value is marked on its cell, with the reason as a tooltip and in a line under the
  table: a malformed time, start times that don't increase, a time past the end of the audio, a gap after its
  track's start, fewer than two tracks, a ticked track no mask fits, a name clash. **Save and Split are disabled**
  while anything is invalid. If the File names masks themselves are invalid, the page says "Fix the file names
  first (Tools page)." and Split is disabled.
- **Ticks:** every track is ticked when the page opens. Unticked tracks still set the boundaries; they are just
  not written. The clash check and the free-space check cover the ticked tracks only.
- **Options**, at their defaults each time the page opens (nothing is remembered):
  - **Send the album file and its .cue to the Recycle Bin once the tracks are verified**: off. Enabled only when
    every track is ticked, since otherwise the unticked audio would be lost; the tooltip says so.
  - **Then run ReplayGain on the album**: off. Enabled only when the option above is ticked, since ReplayGain
    measures every audio file in the folder and would count the album file too. It uses the ReplayGain card's
    options.
- **Buttons:** **Save**, **Save as…**, **Split N tracks** (disabled by invalid values, no ticked track, or a
  failed flac self-check, §13), **Stop** while the job runs, then the report on the page.
- **Unsaved edits:**
  - Split with unsaved edits asks "Save the cue sheet first?" (Save, Save as…, Cancel), so the tracks always
    match a `.cue` on disk. The ticks and options aren't cue data and need no save.
  - Leaving the page (back, another `.cue`, closing the app) with unsaved edits asks to save or discard them.

**Saving the sheet** (a Ground rule 1 exception, added 2026-10-16)
- The sheet is written from the page's model. **Lines that weren't edited are written exactly as they were read**:
  their order, spelling, case and unknown lines. An unedited sheet saved is byte-identical to the original. Line
  endings follow the file's own (CRLF or LF).
- Edited and new lines: times as `mm:ss:ff` with two digits each (minutes may have more), track numbers with two
  digits, values quoted (as EAC writes them), indented as EAC does.
- Encoding: the one the sheet was read with, BOM included. If a character doesn't fit (a Japanese title in a
  Windows-1252 sheet), Save asks to write UTF-8 with a BOM instead, naming the characters.
- **Save** overwrites the selected `.cue`. It confirms "Replace Fixture Album.cue? Its current version goes to the
  Recycle Bin." Then it writes `<name>.cue.partial`, sends the old file to the Recycle Bin through
  `fsops.send_to_recycle_bin` and its check (§19), and renames the new one into place. Where Windows can't
  recycle, Save is refused with the reason and Save as… is offered. A folder locked by a job (`PathLocks`)
  refuses it, as tag saves are refused.
- **Save as…** opens a Save dialog in the `.cue`'s folder. Windows' dialog asks before replacing a file; a
  replaced file goes to the Recycle Bin first, as for Save. The page then works on the new file.
- Saving refreshes the folder in the browser and the side panel's preview.

## The job
- It runs in the Jobs panel (§10) as kind **Split**, for one album, holding a `PathLocks` folder lock owned by
  "the Split tool", so tag saves, renames and other jobs in that folder are refused meanwhile. Unsaved Metadata
  edits in the folder ask first, as the other tools do. The page's controls are read-only while it runs.
- One flac process at a time (`CREATE_NO_WINDOW`, below-normal priority, as rsgain in `core/rsgain.py`). flac
  comes from `paths.resource_path("flac/flac.exe")`.
- **Free space:** the drive must have free space at least equal to the ticked tracks' share of the source's size,
  or the split is refused.
- **For each ticked track:** `flac -8 -V --silent --skip=<first sample> [--until=<end sample>] -o
  "<new name>.partial" <source>`. `-V` makes flac decode what it wrote and compare it while encoding.
- **The proof**, before any file gets its real name, for each ticked track:
  1. its sample count equals its planned length (this catches a cut in the wrong place);
  2. the MD5 of its decoded audio (`flac -d -c --force-raw-format --endian=little --sign=signed`) equals the MD5
     of the same range of the source, decoded by flac (`FlacRunner.decode_range`).

  When every track is ticked, the MD5 of all the tracks end to end must also equal the source's: the FLAC
  STREAMINFO MD5, or for WAV/AIFF, or a FLAC without an MD5, the hash of the source's decoded audio.
- **Then:**
  - each `.partial` is renamed to its final name (checked free again);
  - the tags and the cover are written through `TaggedFile`;
  - if ticked (only possible with every track ticked), the album file and its `.cue` go to the Recycle Bin
    together, through `fsops.send_to_recycle_bin` and its safety check (§19). If they can't be recycled, they
    stay and the report says why.
- **Stop:** it kills flac and deletes this run's `.partial` files, and only those (their exact paths are
  recorded). Once the renames have started, Stop takes effect after them.
- **Failure:** a failed proof or a flac error leaves the folder untouched. The `.partial` files are deleted, and
  the report gives flac's message.
- **Split + ReplayGain** is one job across two steps, as Tidy is (§10): `process_album` on the folder once the
  originals reached the Recycle Bin.
- **Afterwards:** `MainWindow.refresh_after_tool` on the folder.
- **The report**, on the Split page:
  - "Done: 11 tracks split, all identical to the original." or "Done: 3 of 11 tracks split, each identical to its
    part of the original.";
  - the tracks written, the proof's result, and what happened to the originals;
  - the ReplayGain report when it ran.

  The jobs panel reads, for example, "Running (2 of 3): Kid A", "Done: 3 tracks split", or "Stopped: nothing left
  half-done".

**Ground rule 1** gains three exceptions: creating the track files of a split (never overwriting a file), deleting
the split's own `.partial` files from the same run, and saving a cue sheet from the Split page (Save and Save as,
the replaced version going to the Recycle Bin).

## Measured on 2026-10-16 (setup, before any app code)
- The official flac 1.5.0 Win64 build (SHA-256 checked against the release notes) cut a scratch copy of
  `tests/fixtures/generated/cleanup/single_file_album/Fixture Album.flac` (44.1 kHz, 132,300 samples, 3 tracks)
  with `-8 -V --skip/--until` in samples:
  - each track had exactly its planned 44,100 samples;
  - the MD5 of the three decoded tracks end to end equalled the source's STREAMINFO MD5 (`4c0c7e80…`);
  - the same from a WAV copy of it.
- flac accepted the `.partial` output names.
- **Lesson for the fixtures:** that album is a tone that repeats every second, so its three tracks have the same
  MD5, and a cut in the wrong place would still pass the end-to-end MD5. That's why the proof also checks each
  track's length, and why the split fixtures use audio that never repeats (a sweep or seeded noise).

## As built (M10 task 2): `core/cue.py`
- `read_cue_sheet(path, encoding=None)` / `parse_cue(text)` → `CueSheet`: album fields, `files`, audio `tracks`
  (`CueTrack`: number, file, title, performer, songwriter, ISRC, `index00`/`index01` in CD frames), `data_tracks`
  skipped, the encoding's name and `fell_back`. Keywords are matched ignoring case, values may be unquoted, and
  unknown lines are ignored. A track without INDEX 01 or a malformed time raises `CueError`.
- `ENCODINGS` is the picker's list (codec, label); None is Auto, which uses `preview.decode`.
- `read_source(path)` → `SourceInfo` (format, rate, samples, channels, bits, the FLAC MD5, the album-level tags,
  the front cover).
  - FLAC is read with mutagen, WAV with Python's `wave` (a float WAV is refused), and AIFF from its COMM chunk
    (Python 3.13 dropped `aifc`).
  - Any other extension is refused by name, as the spec says.
- `plan_album(cue, masks, replacement, encoding, is_silent, read_info)` → `AlbumPlan`, with the tracks
  (`PlannedTrack`: number, start/end sample, rename fields, ISRC, name, path) or a `refusal`, plus warnings.
  - `is_silent(source, start, end)` is the job's flac decode (task 3). Without it, a pregap of 2 s or more is
    kept as track 0: no sample is lost either way.
  - `name_tracks` gives the names and checks clashes against the folder, ignoring case.
- **Added: paths too long for Windows.** A track whose full path would exceed 259 characters (MAX_PATH less its
  terminator) refuses the album, naming the track and suggesting shorter masks.
  - The reason: Hugo's example sheet has a 128-character title, and a deep library folder could push a track
    past what Windows tools reliably open.
  - Inferred (MAX_PATH is a known Windows limit), not measured with flac or players.
- **Hugo's own EAC sheet** (pasted 2026-10-16) is a fixture (`split/sheets/eac_example.cue`). It plans 11
  tracks, its two INDEX 00 gaps stay at the end of tracks 4 and 10, and every sample lands in one track.
- The split fixtures are 16-bit mono chirps (`CHIRP`, a rising pitch), generated with `make_fixtures.py split`
  and byte-identical across runs.

## As built (M10 task 3): flac, the cut and the proof
- **Bundled:** `packaging/music_desk.spec` ships `resources/flac`, and `paths.flac_path()` finds it. The licences
  are `flac-GPL-2.0.txt` and `libFLAC-Xiph-BSD.txt` in `resources/licenses/` and two `COMPONENTS` rows.
- **Self-check:** `app.ToolCheck` (formerly `RsgainCheck`) runs `rsgain --version`, then `flac --version`, off
  the UI thread. `MainWindow.show_flac_version` keeps the result for the Split button and About ("Cue
  split: flac 1.5.0", or "flac unavailable"). There's no status-bar label (lean UI).
- **`core/flac.py` `FlacRunner`:**
  - `encode(source, start, end, output)` runs `flac -8 -V --silent --skip --until -o`.
  - `decode(path)` gives raw PCM: little-endian, signed, interleaved, the layout the STREAMINFO MD5 hashes.
  - `decode_range(source, start, end)` pipes `flac -c -0` into `flac -d`, so WAV and AIFF sources are read by
    flac itself, as the tracks were.
  - Stop kills the processes. stderr is drained on a thread, and flac's last line becomes the error.
- **`core/split.py`:**
  - `cut_album(plan, runner, cancel, free_space)` checks free space and refuses existing `.partial` names
    (never overwritten nor deleted). It writes each track to `<name>.partial`, then `prove()`s.
  - On a flac error, a failed proof or Stop, it deletes exactly this run's `.partial` paths, recorded before
    each flac run, since a killed flac may leave one behind.
  - `is_silent(runner)` is `plan_album`'s silence check: decode the range and look for any non-zero byte.
  - The renames, tags and recycling are the job's (task 4).
- **Measured on this machine (real flac, `slow` tests):**
  - Whole-file decodes through `decode` and `decode_range` both gave the hidden-track fixture's STREAMINFO MD5.
  - The FLAC, WAV and silent-pregap fixtures cut and proved in 0.16–0.31 s. The silent 3 s pregap was found
    silent and joined track 1.
  - Moving one boundary by one sample failed the proof on both length and MD5, and left the folder as it was.

## Planned (M10 tasks 5 and 6): the editable sheet and the Split page
- **Core (`core/cue.py`, no Qt):**
  - The sheet model keeps **every line** of the file in order, each with its raw text: the parsed fields read
    from it, and the raw text written back unchanged unless that line was edited. Unknown lines are kept as they
    are. The BOM, the encoding and the line-ending style are part of the model.
  - Edits: set an album or track field (which rewrites or adds that one line), set a track's INDEX 00/01, set the
    FILE name, set an "Other lines" block, add a track after another, delete tracks, renumber.
  - `validate(sheet, source_info, masks, replacement, ticked)` lists the problems per cell (above).
  - `format_cue(sheet) -> bytes`, and `save_cue(sheet, path, recycle)` / Save as through `.partial` and the
    Recycle Bin (above). Round trip: `format_cue(read(path))` equals the file's bytes for every fixture sheet.
  - `plan_album` plans from an in-memory sheet (not a path) and a set of ticked track numbers; the proof gains
    the per-track MD5 against `decode_range`, and the end-to-end check runs only with every track ticked.
- **UI:** `ui/split_page.py` (the page, in the centre stack); the controller of §16 owns it, its save and its
  job. `FilePreview.splitRequested` is shown on every `.cue`.

## Replaced on 2026-10-16 (shipped in 0.2.10, removed by M10 task 6)
These notes describe the 0.2.10 code that the Split page replaces. Removed: the Split card and its Tools page
wiring, `cue.find_albums` and the folder scan, the multi-album preview (`AlbumBlock`s and the summary), the
"Split into tracks…" menu entries (on `.cue` and on audio files), `SplitOptions`' JSON and
`Settings.split_options` (dropped when the settings load). Kept and adapted: `finish_album`, `write_tags`,
`SplitRunner` (one album, one job), the report. Delete this section once task 6 is built.

### As built (M10 task 4): the card, the preview, the job
- **Core:**
  - `split.finish_album(plan, cut, recycle_originals, recycle)` checks every final name is still free; if one
    was taken meanwhile, nothing is renamed and the `.partial` files go. Otherwise it renames them, writes the
    tags (`write_tags`: the fields through `TaggedFile`, the cover, ISRC as a Vorbis comment) and recycles the
    album file then its .cue. A refusal (`CannotRecycle` included) keeps both and says why.
  - `cue.find_albums(folder, recursive)` lists the sheets in Explorer's order. `split.SplitOptions` and its
    JSON are `Settings.split_options`.
- **`services/split_job.py` `SplitRunner`:**
  - `plan(folder, recursive, masks, replacement, cues, source)` plans on its worker, with flac's silence
    check, and `replan(cue, encoding, …)` serves the picker.
  - `run(folder, plans, options, rg_options)` takes the "the Split tool" lock. Then, per album: `cut_album`,
    `finish_album`, and ReplayGain when asked.
  - ReplayGain is `replaygain.process_album` inside the same job (as Tidy chains its steps), only when both
    originals reached the Recycle Bin; otherwise the report says why it didn't run.
  - Background indexing pauses while the job runs, as for ReplayGain. Tests inject `recycle` and `scanner`.
- **UI:**
  - `ui/tools/split_card.py` `SplitCard` is last on the Tools page. Its ReplayGain box is enabled only with
    recycling, and a saved ReplayGain option is always off without it.
  - Run is disabled by invalid masks, a failed flac self-check (`ToolsPage.set_flac_unavailable`, from
    `MainWindow.show_flac_version`), or a file selection (the card works on folders).
  - `ui/tools/split_preview.py` `SplitPreview` shows `AlbumBlock`s: the source, an encoding picker that
    re-reads that album, a track table, warnings or the refusal. Then the summary, Split N albums, Stop, and
    the report (`report_text`, and `short_result` for the jobs panel).
- **"Split into tracks…":**
  - In the file list's menu, on one `.cue`, or on an audio file that looks like an album file **from the
    index alone**: a .cue beside it with the same stem, or the folder's only audio file next to a .cue (no
    file is read on the UI thread). The preview then says if it isn't one.
  - On a `.cue` preview, a **Split into tracks…** button (`FilePreview.splitRequested`).
  - Both open the preview for that album with the card's saved options.

## Tests
- **Fixtures** (`make_fixtures.py`, ffmpeg, dev-only; non-repeating audio):
  - a FLAC image with an INDEX 00 gap and a 3 s audible hidden track;
  - one with a silent 3 s pregap (prepended to track 1);
  - a WAV image;
  - a cp1252 sheet with accents, a UTF-8 sheet with a BOM, and a Shift-JIS sheet;
  - a data track;
  - a multi-FILE sheet;
  - a sheet longer than its audio;
  - an APE-named source (refused; made in `tmp_path` by its test, since the tag parity test reads every audio
    fixture and a fake .ape breaks it);
  - a name clash.
- **Core, with a fake runner:** parsing, encodings, samples from times, the boundaries and hidden-track rules,
  names and clashes, tags, refusals, and the partial-file cleanup on cancel and on failure.
- **The editable sheet (task 5):** every fixture sheet saved unedited is byte-identical (UTF-8 with BOM, cp1252,
  Shift-JIS, LF and CRLF); each edit changes only its own line; add, delete and renumber; validation per cell;
  the UTF-8 question when a character doesn't fit; Save and Save as with an injected recycler (the old version
  recycled, refused where it can't recycle, a replaced file recycled).
- **Ticks (task 5):** planning only track 3 of a fixture keeps the boundaries and the tags' 3 of N; the clash and
  free-space checks ignore unticked tracks.
- **`slow`, with the real flac:** the proof passes on the FLAC and WAV images, and fails when a boundary is
  shifted by one sample; extracting only track 3 proves its length and MD5 against the source's range, and fails
  on a one-sample shift.
- **UI (task 6):** the button on every `.cue` preview; the page's fields, table, Add and Delete, validation marks
  and the disabled buttons; the options' enabling (recycling only with every track ticked); the encoding picker
  and the unsaved-edits questions (Split, back, another `.cue`, quit); the job with the lock, Stop, recycling
  through an injected recycler (never the real bin), and ReplayGain chaining; no split key in the saved
  settings.
