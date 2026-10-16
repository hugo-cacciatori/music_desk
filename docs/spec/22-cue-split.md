# 22. Cue split
Specified with Hugo on 2026-10-16 (M10). Some albums are ripped as **one audio file plus a `.cue` sheet** that
says where each track starts. The app cuts such a file into one FLAC file per track, named with the shared masks
(§21) and tagged from the sheet, and proves the cut lost nothing.

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

**Which albums qualify:** the single-file album rule the Rename tool already uses (§10 A, `rename.folder_facts`).
That is a sheet whose FILE lines all name one audio file present in the folder, with at least two tracks. Other
sheets (one FILE per track, missing files) are listed as "not a single-file album" and left alone. Cue sheets
embedded inside FLAC files are not read (not chosen, 2026-10-16).

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
- Every sample of the source ends up in exactly one track, in order, which the proof below checks.
- Refused, naming the reason: times that don't increase, a time past the end of the audio, and fewer than two
  audio tracks.

## Text encoding
- Cue sheets have no declared encoding. **Auto** tries a BOM (UTF-8, UTF-16), then UTF-8, then Windows-1252, the
  same order as the file preview (§20).
- Old Cyrillic, Japanese or Chinese sheets come out garbled that way. The split preview therefore has a picker per
  album, with Auto (naming what it chose), UTF-8, Windows-1252 (Western), Windows-1251 (Cyrillic), Shift-JIS
  (Japanese), GBK (Chinese, simplified), Big5 (Chinese, traditional) and EUC-KR (Korean). Changing it re-reads that
  album's titles and names before anything is written.
- When Auto had to fall back to Windows-1252, the album shows "If the titles look wrong, pick the cue sheet's
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
- Names: the shared masks (§21) over those fields, sanitized, with `.flac`. When no mask fits a track (say a
  track has no title and every mask needs one), the album is refused, naming the track and the missing field.
- **Nothing is ever overwritten.** A name taken by an existing file, or given to two tracks of the album, refuses
  the album and lists the clashes.

## In the app
- **The "Split albums (cue)" card**, the last card on the Tools page (§10), after File names, Rename,
  ReplayGain and Tidy:
  - Its text: "Cuts each single-file album (one audio file and its .cue) in this folder into one FLAC file per
    track, named with the File names masks and tagged from the cue sheet. The new files are checked to be
    identical to the original, sample for sample. The original files stay unless you choose otherwise."
  - Scope: the folder, with **Include subfolders** (on).
  - Options:
    - **Send the album file and its .cue to the Recycle Bin once the tracks are verified**: off (Hugo's
      choice);
    - **Then run ReplayGain on each album**: off. It is enabled only when the option above is ticked, since
      ReplayGain measures every audio file in the folder and would count the album file too. The tooltip says
      so. ReplayGain uses the ReplayGain card's options.
  - The options are remembered (`Settings.split_options`).
  - **Run split…** is disabled while the masks are invalid, or when flac's self-check failed (§13).
- **"Split into tracks…"** in the file menu of a single-file album's `.cue` or audio file, and in the `.cue`
  preview (§20). It opens the preview for that album with the card's saved options.
- **The preview page** ("Split: check the tracks"), like the rename preview (§10):
  - one block per album: the source (format, length), the cue and its encoding picker, then a table (#, Start,
    Length, New name, Title, Artist), the warnings, and the refusal reason when there is one;
  - a summary line: "5 albums → 58 tracks. 1 album refused." and what happens afterwards (the originals, and
    ReplayGain);
  - buttons: **Split N albums** and **← Back to options**.

## The job
- It runs in the Jobs panel (§10) as kind **Split**, holding a `PathLocks` folder lock owned by "the Split tool",
  so tag saves, renames and other jobs on that tree are refused meanwhile. Unsaved Metadata edits in the folder ask
  first, as the other tools do.
- It handles one album at a time, with one flac process (`CREATE_NO_WINDOW`, below-normal priority, as rsgain
  in `core/rsgain.py`). flac comes from `paths.resource_path("flac/flac.exe")`.
- **Free space:** before each album, the drive must have free space at least equal to the source's size, or the
  album is refused.
- **For each track:** `flac -8 -V --silent --skip=<first sample> [--until=<end sample>] -o "<new name>.partial"
  <source>`. `-V` makes flac decode what it wrote and compare it while encoding.
- **The proof**, before any file gets its real name:
  1. each track's sample count equals its planned length (this catches a cut in the wrong place);
  2. the MD5 of all the tracks' decoded audio, end to end (`flac -d -c --force-raw-format --endian=little
     --sign=signed`, read in order), equals the source's. That is the FLAC STREAMINFO MD5, or for WAV/AIFF, or a
     FLAC without an MD5, the hash of the source's decoded audio.
- **Then:**
  - each `.partial` is renamed to its final name (checked free again);
  - the tags and the cover are written through `TaggedFile`;
  - if ticked, the album file and its `.cue` go to the Recycle Bin together, through `fsops.send_to_recycle_bin`
    and its safety check (§19). If they can't be recycled, they stay and the report says why.
- **Stop:** it kills flac and deletes this run's `.partial` files, and only those (their exact paths are
  recorded). Once an album's renames have started, Stop takes effect after that album.
- **Failure:** a failed proof or a flac error leaves the album untouched. Its `.partial` files are deleted, and
  the report gives flac's message.
- **Split + ReplayGain** is one job across two steps, as Tidy is (§10): `process_album` on each verified album
  whose originals were recycled.
- **Afterwards:** `MainWindow.refresh_after_tool` on the scope.
- **The report:**
  - "Done: 5 albums split into 58 tracks, all identical to their originals.";
  - per album: the tracks written, the proof's result, and what happened to the originals;
  - refused albums with their reasons;
  - the ReplayGain report when it ran.

  The jobs panel reads, for example, "Running (2 of 5): Kid A", "Done: 5 albums split", "Done, but 1 album was
  refused", or "Stopped: 2 albums split, nothing left half-done".

**Ground rule 1** gains two exceptions: creating the track files of a split (never overwriting a file), and
deleting the split's own `.partial` files from the same run.

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
  the UI thread. `MainWindow.show_flac_version` keeps the result for the Split card (task 4) and About ("Cue
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

## As built (M10 task 4): the card, the preview, the job
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
- **`slow`, with the real flac:** the proof passes on the FLAC and WAV images, and fails when a boundary is
  shifted by one sample.
- **UI:** the card's options and their enabling, the preview and its encoding picker, the job with the lock, Stop,
  recycling through an injected recycler (never the real bin), and ReplayGain chaining.
