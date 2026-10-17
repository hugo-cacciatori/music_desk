# 22. Cue split
Specified with Hugo on 2026-10-16 (M10), redesigned with him the same day (see "In the app"). Some albums are
ripped as **one audio file plus a `.cue` sheet** that says where each track starts. From one selected `.cue`, the
app shows a Split page where the sheet can be edited, then cuts the file into one FLAC file per ticked track, named
with the shared masks (§21) and tagged from the sheet, and proves the cut lost nothing.

**The use case** (Hugo, 2026-10-17):
- The split is for **EAC rips**, which notoriously arrive as one set: the unified image (`.flac`), the `.cue`
  that EAC wrote, and EAC's extraction `.log`. Hugo doesn't store albums that way; the set is what a new rip
  looks like.
- **The set is kept.** The image and the original `.cue` stay as they are, so the album can be split again later.
- **One image, several splits.** The same image may be split in several ways, each from its own **version of the
  cue sheet** with different split points (start times, tracks added or deleted). Each version is a `.cue` beside
  the image that points to it; the original EAC sheet is one of them. Making a version is Save as… from the
  Split page (Keep both numbers it, `Split Album (2).cue`), then Split from it.
- **A cue sheet is useless without its audio file.** Which audio file a sheet points to (its FILE line) is a
  control of its own on the page (below, "Source"), not text to type, and a sheet is saved only beside it.
- **A split is just a split.** It writes the tracks, and nothing else: it never deletes or moves the image, the
  sheet or the log, and nothing runs after it (ReplayGain is its own tool). The 0.2.10–0.3.0 options to recycle
  the originals and then run ReplayGain are removed (Hugo, 2026-10-17: never asked for, and they would break the
  other versions).

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
- Names: the shared masks (§21) over those fields, sanitized, with `.flac`. **The split never refuses over a
  name** (Hugo, 2026-10-17); the cue itself has no constraint on titles:
  - a ticked track no mask fits (say it has no title and every mask needs one) is named from its number:
    `03 Track.flac`;
  - **nothing is ever overwritten:** a ticked track's name taken by an existing file, or by an earlier ticked
    track, gets a number in Explorer's style: `03 Duet (2).flac`, then `(3)`…;
  - the page shows the final names, with a note on each one that changed and why. Unticked tracks are not
    checked.
  - Still refused, naming the track: a path longer than Windows tools reliably open (below, "As built").

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
- **Source:** which audio file the sheet points to (its FILE line), chosen from a list of the FLAC, WAV and AIFF
  files in the `.cue`'s folder (Hugo, 2026-10-17: a control, not text to type). A FILE line naming a file that
  isn't there, or one the split can't read, shows in the list marked "(not found)" or "(not supported)" until
  another is chosen. Choosing one rewrites that one line (its type word kept). Then the file's format and length,
  or why it can't be split ("APE isn't supported: the split reads FLAC, WAV and AIFF.", "This sheet names 11
  files: only single-file albums can be split").
- **Encoding:** the picker (above).
- **Destination:** the folder the tracks are written to, the image's folder by default, with **Browse…** (Hugo,
  2026-10-17). It must be an existing folder (the app creates none); otherwise Split is disabled, saying so. Names
  are checked there: a name already taken, by an earlier split for instance, gets " (2)", " (3)"…. Like the
  ticks, it starts at its default each time the page opens.
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
  track's start, fewer than two tracks, a double quote in a value. **Save and Split are disabled** while
  anything is invalid. Names never disable Save (Hugo, 2026-10-17: they aren't cue data); a path too long
  disables Split only. If the File names masks themselves are invalid, the page says "Fix the file names first
  (Tools page)." and Split is disabled.
- **Ticks:** every track is ticked when the page opens. Unticked tracks still set the boundaries; they are just
  not written. The name numbering and the free-space check cover the ticked tracks only.
- **Buttons:** **Save**, **Save as…**, **Split N tracks** (disabled by invalid values, no ticked track, or a
  failed flac self-check, §13), **Stop** while the job runs, then the report on the page.
- **Unsaved edits:**
  - Split with unsaved edits asks "Save the cue sheet first?" (Save, Save as…, Cancel), so the tracks always
    match a `.cue` on disk. The ticks and the destination aren't cue data and need no save.
  - Leaving the page (back, another `.cue`, closing the app) with unsaved edits asks to save or discard them.

**Saving the sheet** (a Ground rule 1 exception, added 2026-10-16)
- The sheet is written from the page's model. **Lines that weren't edited are written exactly as they were read**:
  their order, spelling, case and unknown lines. An unedited sheet saved is byte-identical to the original. Line
  endings follow the file's own (CRLF or LF).
- Edited and new lines: times as `mm:ss:ff` with two digits each (minutes may have more), track numbers with two
  digits, values quoted (as EAC writes them), indented as EAC does.
- Encoding: the one the sheet was read with, BOM included. If a character doesn't fit (a Japanese title in a
  Windows-1252 sheet), Save asks to write UTF-8 with a BOM instead, naming the characters.
- **Save** overwrites the selected `.cue`, as any editor's Save does (Hugo, 2026-10-17: no `.partial` step). Its
  old version goes to the Recycle Bin first, through `fsops.send_to_recycle_bin` and its check (§19), so a
  mistaken save can be undone from the bin; then the sheet is written in its place. Where Windows can't recycle
  (a network drive), Save asks "Overwrite <name> for good? This drive has no Recycle Bin." first. A folder
  locked by a job (`PathLocks`) refuses it, as tag saves are refused.
- **Save as…** is how a version is made. It opens a Save dialog in the `.cue`'s folder. On a name that exists
  (the sheet's own name included), the app asks: **Replace** (the old file goes to the Recycle Bin first, as for
  Save) or **Keep both**, which saves as `image (2).cue`, then `(3)`… (Hugo, 2026-10-17). The page then works on
  the new file. A sheet is saved only in its audio file's folder: another folder is refused, saying so, since its
  FILE line would point to nothing.
- Saving refreshes the folder in the browser and the side panel's preview.

## M13 additions (specified with Hugo on 2026-10-17, not built)
The Split page is also where cue sheets are edited and created. Four changes, built with the trim (§23). When they
are built, merge them into "In the app" above and drop this heading.

**1. The source warning**
- Every `.cue` keeps its **Split into tracks…** button, always clickable: the page is where a wrong FILE line is
  fixed (Hugo).
- When the sheet's FILE line names a missing file, one the split can't read, or several files, the page draws the
  eye to the **Source** control. It is marked like a bad cell (red), with the reason in bold beside it, for
  example "This sheet points to Fixture Album.wav, which isn't in this folder. Choose its audio file." Split stays
  disabled until it is fixed, as today.

**2. New cue sheet…**, a second way in, from one FLAC, WAV or AIFF file (§23 "The entry point")
- It opens the Split page on a **new, unsaved sheet** for that file, with only:

      PERFORMER "<album artist, else artist>"
      TITLE "<album>"
      FILE "<file name>" WAVE
        TRACK 01 AUDIO
          INDEX 01 00:00:00

  A missing tag leaves its line out. Track 1 always starts at 00:00:00 (Hugo). The user adds everything else (the
  other tracks, their starts, the titles…) with the page's controls. An intro that ended up in track 1 is trimmed
  from the split track afterwards (§23), not handled here (Hugo).
- Written in UTF-8 with a BOM and CRLF line endings, laid out as EAC does. The encoding picker shows it, disabled
  until the sheet is saved (there is no file to read again).
- Otherwise it is the ordinary page: the source picker, the album fields, the table, Add / Delete, the ticks, the
  destination. One Split page at a time: New cue sheet… while the page has unsaved edits asks first, as opening
  another `.cue` does.
- **Save** is enabled once the sheet has no problem of its own (`validate`): a usable source, at least two
  tracks, starts that are valid, increase and stay inside the audio, no double quote. That is what Split needs
  from the sheet itself (Hugo: "as soon as the split would be possible"); the ticks, the destination and the masks
  aren't cue data.
  - Save opens a Save dialog in the audio file's folder, proposing `<audio file stem>.cue`. An existing name asks
    Replace or Keep both, and another folder is refused, as Save as does today.
  - A box beside it, **Save beside <file name> without asking**, skips the dialog: the sheet is saved at once as
    `<stem>.cue`, or `<stem> (2).cue`, `(3)`… when the name is taken (`numbered_path`), never replacing a file. The
    box shows only on a new sheet, and its state is remembered (Hugo, 2026-10-17: a preference kept where it is
    used, §12).
  - Save as… on a new sheet always opens the dialog.
  - After the save, the page works on the new file like any sheet, and Split is there.
- Split on a new sheet asks "Save the cue sheet first?", as with any unsaved edit.
- Leaving a new sheet (back, another `.cue`, quitting) asks Save / Discard / Cancel only once something was changed
  beyond what was filled in.

**3. Preview in player** (Hugo: hear the current edit before writing anything)
- A button that opens the page's current sheet, unsaved edits included, in the default player, through a temporary
  sheet written as for the trim (§23 "Preview in player": `preview-*.cue` in the app's playlists folder, the FILE
  line a full path, opened with `os.startfile`).
- Enabled when the sheet is viable: a usable source, no sheet problem, and a plan. The masks, the ticks, the
  destination and flac don't matter.
- The temporary sheet lists every track the split would write, ticked or not, from the plan. Each one starts where
  the split cuts it (gaps belong to the previous track) and keeps its title and performer.
- A hidden track (row 0) becomes the temporary sheet's track 1, titled "Hidden track", since a cue sheet can't
  number a track 0. The others follow, one number higher than on the page.

**4. Fill from track lengths…** (Hugo: the page reflects the cue grammar exactly, so Length stays computed; a
helper that works in other terms gets its own window)
- The need: a tracklist (a back cover, Discogs) gives each track's length, while the sheet needs each start time.
  Adding up the lengths by hand is slow and error-prone.
- A button beside Add / Delete track opens a small **modal dialog**: fixed size, on top of the app, keeping the
  focus until OK or Cancel (Hugo).
- It has a track count, and a row per track with its length as `m:ss` or `m:ss:ff`, filled in from the sheet's
  current tracks. The last track's length isn't asked: it runs to the end of the audio, shown as "The rest: 4:12".
- OK sets the starts: track 1 keeps its start, and each next track starts where the previous one ends.
  - Tracks are added at the end to reach the count (with no title). Extra tracks at the end are deleted, and the
    dialog names them first: "Tracks 9–11 will be deleted."
  - A moved track's INDEX 00 is removed, since its gap no longer fits. Titles and the other fields stay.
- OK is disabled, saying why, while a length is malformed or the lengths before the last track pass the end of the
  audio.
- The result is an ordinary unsaved edit. Lengths in whole seconds give starts on whole seconds: Preview in player
  checks them, and the Start cells adjust them.
- Tests never open it: it goes through an `ask_track_lengths` method that they replace.

**Tests (M13)**
- The source warning on a missing, an unsupported and a multi-FILE source, gone once a file is chosen.
- New cue sheet…: the filled-in lines (and the ones left out without tags); Save disabled until the sheet is
  viable; the dialog's proposed folder and name; the box's direct save, numbered when the name is taken and never
  replacing a file; the box remembered; the page on the new file afterwards; the unsaved-edits questions only after
  a change.
- Preview in player: the temporary sheet from a plan with gaps and with a hidden track, through an injected opener.
- Fill from track lengths: the starts, tracks added and deleted, a gap removed, the disabled OK.

## The job
- It runs in the Jobs panel (§10) as kind **Split**, for one album, holding a `PathLocks` lock on the image's
  folder and the destination, owned by "the Split tool", so tag saves, renames and other jobs there are refused
  meanwhile. Unsaved Metadata
  edits in the folder ask first, as the other tools do. The page's controls are read-only while it runs.
- One flac process at a time (`CREATE_NO_WINDOW`, below-normal priority, as rsgain in `core/rsgain.py`). flac
  comes from `paths.resource_path("flac/flac.exe")`.
- **Free space:** the destination's drive must have free space at least equal to the ticked tracks' share of the source's size,
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
  - the tags and the cover are written through `TaggedFile`. Nothing else changes.
- **Stop:** it kills flac and deletes this run's `.partial` files, and only those (their exact paths are
  recorded). Once the renames have started, Stop takes effect after them.
- **Failure:** a failed proof or a flac error leaves the folder untouched. The `.partial` files are deleted, and
  the report gives flac's message.
- **Afterwards:** `MainWindow.refresh_after_tool` on the destination.
- **The report**, on the Split page:
  - "Done: 11 tracks split, all identical to the original." or "Done: 3 of 11 tracks split, each identical to its
    part of the original.";
  - the folder and the tracks written, the proof's result, and that the image and its sheet are as they were.

  The jobs panel reads, for example, "Running (2 of 3): Kid A", "Done: 3 tracks split", or "Stopped: nothing left
  half-done".

**Ground rule 1** gains three exceptions: creating the track files of a split in the destination the user chose
(never overwriting a file), deleting the split's own `.partial` files from the same run, and saving a cue sheet from the Split page (Save and Save as,
the replaced version going to the Recycle Bin; overwritten for good only where Windows has no Recycle Bin and the
user agreed).

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

## As built (M10 task 5, 0.2.13): the editable sheet, ticks and the per-track proof
- **`core/cue_edit.py`** (a module of its own beside `cue.py`, which keeps parsing and planning):
  - `CueDocument` is the file's lines in order, each with its own text and line ending (CRLF, LF, CR, or none on
    a last line). The lines are the only state: album fields, tracks and their values are parsed from them each
    time they are asked for. New lines take the file's most common ending.
  - `read_document(path, encoding=None)` / `parse_document(bytes)`: Auto is a BOM (UTF-8, UTF-16), then UTF-8,
    then Windows-1252, as the preview. Decoding uses `surrogateescape`, so bytes the encoding can't decode are
    written back as they were; the page sees them as U+FFFD.
  - `format_cue(doc)` is the BOM plus every line re-encoded: every fixture sheet, read with any picker encoding,
    comes back byte-identical, and so do LF, CR, no final line ending, UTF-16 and undecodable cp1252 bytes.
    A character the encoding can't hold raises `CueEncodingError` naming it; `use_utf8_bom()` then switches the
    document to UTF-8 with a BOM.
  - Edits: `set_file`, `set_album(field, value)`, `set_track(row, field, value)`, `set_album_other`,
    `set_track_other`, `add_track(after, audio_frames)`, `delete_tracks(rows)`. A value equal to the current one
    leaves its line untouched; an empty one removes the line. A changed line keeps its indentation; a new one
    goes where EAC puts it (REM lines after the last REM, other album lines before FILE, a track's fields after
    the last field EAC writes before it). Times typed as `9:1:6` are written `09:01:06`; a malformed time is
    kept as typed for `validate` to mark. Quoted values are read up to the line's last quote, so a quote inside
    a value shows and is marked.
  - Add and delete renumber the TRACK lines 1…N, rewriting only the ones whose number changes. Deleting track 1
    gives its start to the next track kept, and drops that track's INDEX 00.
  - `validate(doc, info)` → `Problem(row, column, message, blocks_save)` per bad cell: a missing or malformed
    time, a gap after its start, starts that don't increase, a start past the end of the audio, fewer than two
    tracks, a double quote in a quoted value, a disc number that isn't a number.
  - `save_cue(doc, path, recycle, check, overwrite_for_good)` is Save and Save as: the file it replaces goes
    to the Recycle Bin, then the sheet is written in its place. Where Windows can't recycle it raises
    `CannotRecycle`, unless the user agreed to overwrite for good. `numbered_path(path)` is Save as's "Keep
    both" (`image (2).cue`). (Revised on 2026-10-17: the first build wrote a `.partial` first.)
- **Ticks:** `cue.plan_sheet(sheet, cue_path, masks, replacement, ticked=…)` plans an in-memory sheet (the
  page's, with unsaved edits); `plan_album` reads the file and calls it. `AlbumPlan.tracks` keeps every track
  (they set the boundaries), `written` is the ticked ones, `all_ticked` says whether every one is. Names
  (revised 2026-10-17): `name_tracks` numbers a taken name (`cue.free_name`, Explorer's "name (2).ext") and
  names a track no mask fits from its number (`03 Track.flac`), with a note per changed name in `plan.notes`;
  only a path too long is a problem (`plan.problems`) and refuses the album. Each track
  has its `row` in the sheet (None for a hidden track). Clashes, unfit masks and too-long paths are checked
  for ticked tracks only and listed per track in `plan.problems`; an unticked track that no mask fits gets no
  name.
- **The proof** (`split.prove`): each written track's length, then its MD5 against `decode_range` of its own
  range of the source. With every track ticked, the tracks end to end must also match the STREAMINFO MD5, or,
  without one (WAV, AIFF), the source ranges' hashes end to end, which cover the whole audio, so the source is
  decoded once. Free space is the ticked tracks' share of the source's size. `finish_album` renames and tags
  the written tracks only (its recycling was removed in 0.3.1).
- **Tests:** `tests/core/test_cue_edit.py`; ticks in `test_cue.py` and `test_split.py`, including the real flac
  extracting only track 3 (its length and MD5 against its range) and track 2 one sample off (moving only its
  start fails on the length; moving its start and end keeps the length and fails on the MD5).

## As built (M10 task 6, 0.2.14): the Split page and its job
- **The way in:** the side panel's `.cue` preview button (`FilePreview.splitRequested`) calls
  `SplitController.open(path)`. The same sheet opened again comes back as it was left (browsing elsewhere keeps
  the page and its unsaved edits); another sheet asks first about unsaved edits; while a split runs, its page is
  shown instead. There is no menu entry and nothing on the Tools page.
- **`ui/controllers/split.py` `SplitController`** (the first of §16's controllers; `window.split`): it owns the
  page, the runner, the open `CueDocument`, the ticks and the job.
  - Each edit goes to the document, then `validate` runs at once on the UI thread (pure Python), and the plan
    follows 150 ms after typing pauses, on the runner's worker (`cue.plan_sheet` with the ticks). While a value
    can't be parsed there is no plan: lengths and new names stay empty and Split is disabled.
  - Ticks are kept per sheet row (and the hidden track's), so they follow Add and Delete. Whether the table has
    a hidden track's row 0 comes from the last plan that had tracks, so rows don't move while a value is fixed.
  - Buttons: Save needs unsaved edits and no sheet problem; Save as needs no sheet problem; Split needs a plan
    of the latest edit with no refusal, a ticked track, valid masks ("Fix the file names first (Tools page).")
    and flac (`set_unavailable`, from `MainWindow.show_flac_version`). The reasons show beside Split. (Revised
    2026-10-17: the recycle and ReplayGain options are gone; the source picker and the destination were added,
    see below.)
  - Saving: `save_cue` with the controller's recycler. A character the encoding can't hold asks `confirm_utf8`; a
    drive without a Recycle Bin asks `confirm_overwrite_for_good`; a folder locked by a job refuses it. Save as
    asks `choose_cue_file`, then `ask_replace_or_keep` on an existing name (Keep both: `numbered_path`), and
    the page then works on the new file. Saving refreshes the folder (`refresh_after_tool`).
  - Unsaved edits: `ask_leave` (Save, Discard, Cancel) on Back, another sheet and quitting
    (`MainWindow.closeEvent`); `ask_save_first` (Save, Save as…, Cancel) on Split, which then splits once the
    saved sheet is planned; `confirm_discard_for_encoding` on another encoding.
  - The job: `_unsaved_edits_under` first (Metadata edits in the folder), then `SplitRunner.run` under the
    folder lock, a Split job in the jobs panel ("Running (2 of 4): <album>", then "4 tracks split", "nothing
    left half-done" or the error), indexing paused meanwhile, `refresh_after_tool` afterwards. The report
    replaces the editor (`report_text`); Done closes the page, and the jobs panel shows the report again.
- **`ui/split_page.py` `SplitPage`:** the widgets only (source and encoding, album fields and other lines, the
  table with ticks and marks, the selected track's other lines, Add / Delete, the options, the buttons, the
  report). Bad cells get a red background and their reason as a tooltip; the reasons are listed under the
  table, with the plan's naming notes ("02 Two.flac is taken (it already exists), so it's named 02 Two
  (2).flac.").
- **`services/split_job.py` `SplitRunner`:** `load(path, encoding)` reads the document on its worker,
  `plan(sheet, cue, masks, replacement, ticked)` plans it (the source's info and the silence checks are cached
  per file version, so planning again after an edit doesn't decode again), and `run(plan)` cuts, proves
  and finishes one album (0.3.1: its options and ReplayGain removed). `split.cut_album` gained `on_track` for the
  progress.
- **Kept from 0.2.10:** `split.finish_album` (final names checked free again, tags through `write_tags` with the
  cover and the ISRC as a Vorbis comment). Its recycling of the originals, and ReplayGain chained in the job,
  were kept in 0.3.0 and removed in 0.3.1.
- **Removed:** the Split card and its Tools page wiring, `cue.find_albums` and the folder scan, the
  multi-album preview, the "Split into tracks…" menu entries, `SplitOptions.recursive` and its JSON, and
  `Settings.split_options` (an old settings file's key is ignored, and gone at the next save).
- **Tests:** `tests/ui/test_split_tool.py`, with the real flac on copied fixtures and a recorder for the Recycle
  Bin.

## As built (0.3.1): the source picker, the destination, a split that only splits
- **Source picker:** `SplitPage.source`, a list of the FLAC, WAV and AIFF files beside the sheet
  (`split_job.audio_files`, listed on the worker when the sheet is read and with each plan, so it follows the
  folder). The FILE line's name is listed first when it isn't one of them, marked "(not found)" or "(not
  supported)". Choosing one calls `CueDocument.set_file`, which rewrites that one line.
- **Destination:** `SplitPage.destination` and Browse… (`choose_destination_folder`, an existing folder only).
  `cue.plan_sheet(destination=…)` refuses a folder that doesn't exist; `AlbumPlan.folder` is where the tracks
  go, and `name_tracks` numbers names against it. `cut_album` checks that drive's free space; the job locks the
  image's folder and the destination.
- **Save as:** asks Replace / Keep both on any existing name, the sheet's own included (Keep both makes a
  version), and refuses a folder other than the audio file's.
- **Removed:** `SplitOptions`, recycling in `finish_album` (`Finished.recycled`, `kept_reason`), ReplayGain in
  the split job, and the page's two option boxes. The controller keeps its own recycler for saved-over sheets
  (`SplitController.recycle`).
- **Tests:** two splits of one image from two versions of its sheet (the second numbered " (2)"), the source
  picker, the destination, and a split that leaves the image and its sheet byte-identical.

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
  and the disabled buttons; the source picker and the destination; the encoding picker and the unsaved-edits
  questions (Split, back, another `.cue`, quit); Save and Save as through an injected recycler (never the real
  bin); the job with the lock, Stop, and the image and its sheet left byte-identical; one image split twice from
  two versions of its sheet; no split key in the saved settings.
