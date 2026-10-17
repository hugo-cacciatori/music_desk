# 23. Trim
Specified with Hugo on 2026-10-17 (M13, built in 0.3.5–0.3.9). From one selected audio file, the app writes a **trimmed
copy** of it, keeping only the part between a new start and a new end, into a subfolder of its folder. The
original is never touched. The same selection also offers **New cue sheet…**, which opens the Split page on a new
sheet for that file (§22, "M13 additions").

**The use case** (Hugo, 2026-10-17):
- A track with a long, unrelated silence at its start or its end: the user wants a version without it.
- An album's last track with a **hidden track** after minutes of silence: the user wants one part only (the song,
  or the hidden track). To keep both as files, trim twice, or write a cue sheet and split it.
- The trim is a split of one track into two or three parts where only one is kept, so it reuses the split's
  cutting, proof and job (§22). The removed parts are not written anywhere.
- A trim is just a trim: nothing runs after it (no ReplayGain, no recycling), as for the split.

## The entry point
- With **one audio file** selected, the Metadata tab's header (§7) shows two buttons under the format line, in
  the room the cover column already leaves (nothing on screen is resized): **Trim…** and **New cue sheet…**.
  They don't show for several files, a folder or a non-audio file.
- **Formats:** FLAC, WAV and AIFF, what the bundled flac.exe reads, as for the split (Hugo, 2026-10-17). On any
  other format both buttons show greyed, with the reason as their tooltip: "MP3 isn't supported: trimming reads
  FLAC, WAV and AIFF." 32-bit float WAV is refused the same way.
- Both are also greyed, saying why, when flac's self-check failed (§13) or the file is under a job's lock (the
  tooltip names the job, as the Metadata tab's read-only note does).
- **Trim…** opens the **Trim page** in the centre area, non-modal with a back button, like the Split page. One Trim
  page at a time: Trim on another file replaces the page without asking (its two times are the only state, as the
  split's ticks aren't saved either). While a trim job runs, its page is shown instead.

## The Trim page
Top to bottom:
- **Header:** the file name and its format line ("FLAC 16-bit/44.1 kHz · 9:47.213").
- **The waveform** of the whole track, with two **crop handles**, like a crop in a photo app:
  - the start handle and the end handle are dragged along the waveform; the parts outside them are dimmed;
  - a handle can't pass the other one;
  - no zoom (the time fields give the precision) and no silence detection: silence shows as a flat line, and the
    user drags to it (Hugo, 2026-10-17);
  - its height is fixed at 70 px (Hugo, 2026-10-17, M16: it filled the page, much too big); the fields and the
    rest of the page sit under it, and the spare height goes below them (built in M16 task 4, 0.5.8:
    `trim_page.WAVEFORM_HEIGHT`);
  - while the audio is being read, "Reading the audio…" shows in its place, and the time fields already work. If
    it can't be read, the reason shows there; the trim itself only needs the file's length.
- **Start** and **End**, as `m:ss.mmm` (minutes may pass 59), typed directly for small, precise changes:
  - moving a handle updates its field, and a field typed updates its handle, live;
  - Up / Down nudges by 10 ms, Page Up / Page Down by 1 s;
  - under them: "Keeps 3:41.200. Removes 0:02.350 at the start and 5:12.000 at the end."
- **Subfolder:** "Save in subfolder" with a name field, **lite** by default. The last name used comes back next
  time (Hugo, 2026-10-17: a preference kept where it is used, §12). Below it, the copy's path, for example
  `lite\03 Song.flac`, with a note when its name had to be numbered.
- **Buttons:** **Preview in player** (below), **Trim**, **Stop** while the job runs, then the report on the page.
- **Live validation**, as on the Split page: a bad value is marked, with its reason beside Trim, and Trim is
  disabled:
  - a malformed time, a start not before the end, an end past the track's length;
  - nothing to cut (the start at 0 and the end at the length): "Nothing to trim: move a handle.";
  - an empty subfolder name, characters Windows refuses (`\ / : * ? " < > |`), a name ending in a dot or a space,
    a reserved name (`CON`, `NUL`, `COM1`…), or a file (not a folder) of that name in the track's folder;
  - a path longer than 259 characters (the split's rule, §22).

## Preview in player
- Hugo, 2026-10-17: a button that opens the current edit in the default player, as a cue sheet, so the user hears
  exactly what is cut before writing anything. The Split page gets the same button (§22, "M13 additions"); the
  temporary sheet is written the same way for both.
- For a trim, the sheet points to the selected file and has up to three tracks: **Cut from the start** (0 to the
  new start), **<title> (kept)** (the new start to the new end) and **Cut from the end** (the new end to the end
  of the file). A part of zero length is left out.
- **The temporary sheet:**
  - written to `%LOCALAPPDATA%\MusicDesk\playlists\` as `preview-<YYYYMMDD-HHMMSS>[-n].cue`, beside the
    temporary playlists of §14, in UTF-8 with a BOM and CRLF line endings;
  - its FILE line holds the audio file's **full path**, since the sheet isn't beside it;
  - the last 20 are kept, and pruning only ever touches `preview-*.cue` in that folder (the app's own files);
  - opened with `os.startfile` on the shell thread, as Play does (§14). If no app is associated with `.cue`, a
    message says how to set one, as for `.m3u8`.
- **Precision:** a cue sheet counts in CD frames (1/75 s), so the preview's cut points are rounded to the nearest
  frame, up to 6.7 ms from the trim's own points, which are exact to the sample.
- It works with unsaved values: it needs only valid Start and End (the subfolder doesn't matter).
- **Measured** (2026-10-17, Hugo's machine): `.cue` files open in foobar2000 (the registry's UserChoice is
  `foobar2000.CUE`). **Inferred, not measured:** that foobar2000 plays a sheet whose FILE line is a full path in
  another folder. An optional check for later, in the installed app.

## Cutting
- Times become samples: sample = round(seconds · rate). The kept range is [start, end).
- The copy is always **FLAC** (a WAV or AIFF gives a `.flac`), cut with `FlacRunner.encode` (`flac -8 -V
  --skip --until`), as the split does.
- **Name:** the original's file name with `.flac`, in the subfolder. A name already taken there (an earlier trim)
  gets " (2)", " (3)"…, in Explorer's style (`cue.free_name`). Nothing is ever overwritten.
- **The subfolder** is created when the job starts if it doesn't exist yet. A run that fails or is stopped removes
  it again only if that run created it and it is still empty.
- **Tags** (Hugo, 2026-10-17):
  - the copy gets the original's tags: for a FLAC, every Vorbis comment and every picture as they are; for WAV
    and AIFF, the fields of §1 mapped to Vorbis comments, and the front cover;
  - **ReplayGain values are kept**: the album values are still the album's, and cutting silence barely changes the
    track's (inferred from how ReplayGain 2 measures loudness, which ignores silence; they are stale when a hidden
    track was cut off);
  - **no lyrics tag**, synced or not: the app's lyrics come from LRCLIB (CLAUDE.md "Standing decisions"), and
    synced lyrics would no longer match once the start moves. A same-stem `.lrc` beside the original isn't copied.
- **The proof**, before the copy gets its name: its sample count equals the kept range's length, and the MD5 of its
  decoded audio equals the MD5 of the same range of the original (`FlacRunner.decode_range`), as the split's
  per-track proof (§22).

## The job
- It runs in the Jobs panel (§10) as kind **Trim**, holding a `PathLocks` lock on the track's folder (which holds
  the subfolder), owned by "the Trim tool". Unsaved Metadata edits on that track ask first, as the other tools do.
  Background indexing pauses meanwhile, as for a split. The page's controls are read-only while it runs.
  - **← Back stays while it runs**, as on the Split page (M16 task 6, 0.6.0): the trim goes on and the Jobs panel
    reopens its page.
- **Free space:** the drive must have at least the kept range's share of the original's size, or the trim is
  refused.
- `.partial` first, the proof, then the rename to the final name (checked free again), then the tags. **Stop**
  kills flac and deletes this run's `.partial` file, and only that one. **Failure** leaves the folder as it was
  (the `.partial` deleted, the subfolder removed if this run created it), and the report gives flac's message.
- **Afterwards:** `MainWindow.refresh_after_tool` on the track's folder, so the subfolder shows in the tree.
- **The report**, on the page: "Done: lite\03 Song.flac, 3:41.200, identical to its part of the original. The
  original is unchanged." Done closes the page. The jobs panel reads "Running: 03 Song", "Done: 03 Song trimmed"
  or "Stopped: nothing left half-done".

## What follows from it
- The copy is a new file in the library: it is indexed, shows in the tree and in search beside the original, and
  can be played and tagged like any track.
- It is looked up on LRCLIB like any track, with its own length. LRCLIB matches the length within 3 s (§9), so a
  copy without its hidden track may find another entry than the original, or none.

## Not now
- Replacing the original by its trimmed version (Hugo: "for now, the original track is unaffected").
- Lossy formats (MP3, AAC, Opus…): they would need ffmpeg, rejected for the split for its size (§22).
- Keeping several parts of one track: that is a split (New cue sheet…).
- Zoom and silence detection on the waveform.

## Building it (notes for M13)
- **Core, no Qt:**
  - `core/trim.py`: the plan (times to samples, the subfolder name's checks, the copy's name and path, the
    refusals), and the cut with its proof, reusing `core/flac.py` and the split's proof (`split.prove`, or a
    shared part of it).
  - `core/waveform.py`: peaks per column (a minimum and a maximum, all channels together) reduced from
    `FlacRunner.decode_range` as its chunks arrive, never holding the whole audio: a 70-minute CD image is 740 MB
    of PCM. Pure Python with `array` or `memoryview` slices, since numpy isn't in the stack. One idea to measure:
    a waveform needs only each sample's most significant byte (`data[1::2]` for 16-bit, `data[2::3]` for 24-bit),
    which slicing gives at C speed.
  - The temporary preview sheets (writing and pruning) beside `core/playlists.py`.
  - **Built (M13 task 1, 0.3.5):** `core/trim.py`. `plan_trim(source, info, start, end, subfolder)` takes the
    fields' text and gives a `TrimPlan`: the samples, the copy's name and `shown_path`, the numbering `note`, the
    `summary` line, and `problems` as (field, why), field being "start", "end", "subfolder" or None ("Nothing to
    trim"), so the page marks each field. `unsupported_reason(path)` is the buttons' tooltip; `read_source` the
    split's, with the trim's wording. `cut_trim` (subfolder, free space, `.partial`, `prove`) and `finish_trim`
    (the name checked again, `copy_tags`) are what the job will call. The split shares `hash_chunks`,
    `delete_partials` and `megabytes` with it.
  - Times: `m:ss` and one to three decimals are read too; a time to samples rounds halves up, in integers. An End
    left at the shown length (rounded to the millisecond) means the last sample. A start at or past the length is
    refused like an end past it.
  - **Measured** (2026-10-17, flac 1.5.0): cutting a FLAC carries its Vorbis comments and pictures into the cut.
    `copy_tags` therefore clears the copy's tags and pictures and writes the original's (without lyrics), so the
    result doesn't depend on flac. The split does the same since 0.3.6 (§22).
  - For `core/waveform.py` (notes from task 1, not measured): `decode_range` runs `flac -c -0 | flac -d` so any
    source is read by flac; for a whole FLAC, `FlacRunner.decode` (one `flac -d`) skips that re-encode, so time
    both on the 70-minute image. Neither a 70-minute nor a 24-bit/96 kHz fixture exists: generate them with the
    dev-only ffmpeg into a scratch folder for the measurement (too big to commit), and keep the tests' fixtures
    small. The temporary playlists' `prune` touches only `play-*.m3u8`: the preview sheets need their own, for
    `preview-*.cue`.
  - **Built (M13 task 2, 0.3.7):** `core/waveform.py`. `read_peaks(runner, path, info)` gives `Peaks` (`lows`,
    `highs`, -1.0 to 1.0) at `COLUMNS` = 4096 columns (one a frame for a shorter track): a FLAC through
    `FlacRunner.decode`, WAV and AIFF through `decode_range`. `PeakReducer` takes chunks of any size, keeps the top
    byte of each sample (`bytes.translate` to offset binary, then `min` and `max` per column, so 256 levels; a
    20-bit sample is scaled by what its top byte reaches), and holds only the current column. `fit(peaks, width)`
    reduces them to the widget's pixels (lowest low, highest high), so painting never decodes again.
  - **Measured** (2026-10-17, this machine, flac 1.5.0; scratch files made with ffmpeg, pink noise and a sine):
    | Source | flac alone | flac and the peaks | worst `feed` call (GIL held) | Python memory |
    |---|---|---|---|---|
    | 70 min, 16-bit/44.1 kHz FLAC, `decode` | 3.5 s | 9.7 s | 13 ms | 2.4 MB |
    | the same FLAC, `decode_range` | 6.1 s | 13.0 s | 17 ms | 2.4 MB |
    | the same as WAV, `decode_range` | 4.2 s | 11.1 s | 16 ms | 2.4 MB |
    | 10 min, 24-bit/96 kHz FLAC, `decode` | 1.2 s | 3.2 s | 10 ms | 2.5 MB |

    The Python reduction is the slower half (about 8 ms per MB of PCM). Looking for the extreme bytes with `in`
    first was slower on noise and on silence, so `min`/`max` stay. Each `feed` holds the GIL under 20 ms, inside
    the 50 ms target; the UI-thread cost of painting and dragging is measured with the page (task 3).
  - **Built (M13 task 2):** the temporary preview sheets are in `core/playlists.py`: a `PreviewSheet` (the audio, its
    rate and length, `PreviewTrack`s at sample starts, the album title and performer), `render_preview` and
    `write_preview` (`preview-<stamp>[-n].cue`, pruned to 20 by `prune` with its own pattern, which never touches
    `play-*.m3u8`, nor the reverse). Starts round to the nearest frame, halves up (`frames_from_samples`); a track
    that rounds to no frame (it starts on the next one's frame, or at the end) is left out, so no two tracks share
    a frame. A double quote in a title becomes `'` and line breaks a space, since a sheet can't hold them. The FILE
    type is always WAVE, as EAC writes it. `trim.preview_sheet(plan, title, artist, album)` gives the three parts
    (the kept one "<title> (kept)", else the file name); `TrimPlan.times_ok` is Preview in player's condition.
    `split.preview_sheet(plan)` gives the Split page's (§22): every planned track, a hidden track first.
- **UI:** `ui/trim_page.py` (the widgets: the waveform widget, the fields, the subfolder, the buttons, the report)
  and `ui/controllers/trim.py` `TrimController` (`window.trim`), following `SplitController`. The waveform is
  computed on a worker and painted from the cached peaks, so dragging a handle and resizing never decode again.
  - **Built (M13 task 3, 0.3.8):** `services/trim_job.py` `TrimRunner` reads the track (`trim.read_source`, and
    title, artist and album for the preview), plans each edit (latest request only, 80 ms after the last change),
    and runs the job under the lock; the waveform has its own worker and a newer track stops it. `ui/trim_page.py`
    holds `WaveformView` (a press takes the nearer handle and drags it; the handles stay at least 1 ms apart),
    `TimeEdit` (the nudge keys) and `TrimPage`; `ui/controllers/trim.py` `TrimController` (`window.trim`).
  - The Metadata tab shows **Trim…** under the format line for one audio file (`trimRequested`), and **New cue
    sheet…** beside it since 0.3.9 (`newCueRequested`, §22), greyed by the same rules. The button decides from the extension: a 32-bit float WAV opens the page, which says
    why it can't be trimmed (reading the format needs the file, which the button doesn't open).
  - The subfolder is a name field labelled "Save in subfolder:" (the copy always goes in one). The name is saved
    in `Settings.trim_subfolder` when Trim runs. The note beside Trim lists every problem, not only the first.
  - Preview in player writes the sheet and calls `window.launch` on the shell thread (`window._in_shell`, as
    Play does); without a player for `.cue`, the toast says how to set one and offers File Explorer.
- **Measure** (Ground rule 2, §18 `StallWatch`): the waveform's read time and memory on a 70-minute 16-bit image
  and on a 24-bit/96 kHz track, and the UI-thread cost of painting and dragging (target: no stall over 50 ms).
  The read is above ("Built (M13 task 2)"). **Measured** (2026-10-17, this machine, a 2,538-pixel-wide waveform of
  4096 columns): a repaint 4 ms, a drag step (the handle, its field, the repaint) 4 ms, a resize 23 ms (the peaks
  fitted again and drawn once into a cached pixmap). Before the pixmap cache, every drag step redrew the waveform
  (18 ms).

## Tests
- **Core, with a fake runner:** times to samples at 44.1 and 48 kHz; the validation (each case above); the copy's
  name, numbered when taken; the subfolder created when missing, reused when present, refused when a file has its
  name, and removed after a failure or a Stop only when this run created it and it is empty; tags copied without
  the lyrics, ReplayGain kept; the temporary preview sheet's tracks (three, or two when a part is empty), its full
  FILE path, its rounding to frames, and the pruning that keeps 20 and touches only `preview-*.cue`.
- **Waveform:** peaks of a known fixture (a silent stretch gives a flat line), 16- and 24-bit, mono and stereo, and
  a source read in chunks.
- **`slow`, with the real flac:** trimming the hidden-track fixture to its hidden part and to its song, each proved
  by length and MD5 against its range; a one-sample shift fails the proof; the original byte-identical afterwards.
- **UI:** the two buttons on one FLAC, WAV or AIFF file, greyed on an MP3 and while flac is unavailable or the file
  is locked, absent on several files and folders; handles and fields kept in step both ways; the disabled Trim and
  its reasons; the remembered subfolder name; Preview in player through an injected opener (never a real player);
  the job with its lock, Stop and the report; the original left byte-identical.
