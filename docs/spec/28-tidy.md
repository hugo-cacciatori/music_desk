# 28. Tidy (remade, M17)
Specified with Hugo on 2026-10-17. It replaces §10's Tidy ("C. Tidy album": the rename, then ReplayGain, on one
folder or a discography).

**The need** (Hugo): one action that tidies a whole folder tree of albums. Each album ends up split into tracks,
named by the current masks and carrying ReplayGain tags for the current target, and the files a split made
redundant are offered for deletion at the end, one by one.

## What it covers
- Tidy runs on the Tools page's folder (the selection's folder or the open folder, as the other cards) **and every
  subfolder below it** that holds music. There is no scope choice any more (no "This album" / "Discography").
- **Albums** are found as ReplayGain finds them (`replaygain.find_album_folders`): every folder holding audio,
  with the ReplayGain card's keyword subfolders handled by their parent album. The keywords come from the
  ReplayGain card's Advanced options (Hugo: one list, no Tidy list):
  - **lite and bonus folders** (the "track-data" and "excluded" keywords) are never split or renamed. Only
    ReplayGain touches them, through their parent, as today.
  - **Disc folders** (the other album-data keywords: cd2, disc 2, cd 2) are split and renamed like any album
    (Hugo). ReplayGain still runs on the parent, which copies its album values into them, as today. So a disc
    folder that needs work makes its parent album run ReplayGain too.
- Folders whose audio is only a format the split and rsgain can't handle are listed as skipped, with the reason.

## Each album
Tidy sorts each album into one of these:
1. **Not split**: the folder holds a `.cue` whose FILE line names one FLAC, WAV or AIFF image in the folder, and
   no other audio file (as Advanced search's "not split", §26). Tidy **splits** it with the Split page's defaults
   (§22): every track ticked, into the same folder, named by the File names masks, in the sheet's Auto encoding.
   Then **ReplayGain** runs on the album. The `.cue` and the image go on the **offer list** (below).
   - The split is skipped, and the album listed in the report as "open its .cue to split it by hand", when the
     sheet needs a human (Hugo): any warning of the Split page (data tracks, a sheet whose bytes can't be
     Windows-1252, a hidden track kept before track 1, …), the plan's refusal (a name too long, an existing file, too
     little space, flac unavailable), or two sheets naming the same image. Nothing in that folder is touched.
   - A sheet Auto read as Windows-1252 is split (0.7.3, §22 "Text encoding"); the report says so for that album.
2. **Split** (tracks are there; a cue or not): Tidy **renames** the tracks the masks name differently (the Rename
   card's own options apply, junk cleanup included only when ticked there), then checks ReplayGain.
   - **Half-split** (the image, its `.cue` and the tracks split from it, Hugo): handled as split, and the image and
     `.cue` also go on the offer list.
3. **ReplayGain check**, for every album after its split or rename: ReplayGain runs (scope "This album", per
   album) unless every track rsgain can scan has the four ReplayGain values and the ReplayGain comment for the
   card's current target ("LUFS Target : -15"; the comment template with `{target}`, `lufs_comment_pattern`).
   A track with another target, or none, makes the album run. **No leveling** across albums (Hugo: an album
   already correct is left untouched).
4. **Already tidy**: nothing to do; listed as such.

## The plan page (preview)
- "Run Tidy…" on the Tidy card reads the tree on a worker (Ground rule 2), then shows **the whole plan** (Hugo):
  every album with what will happen to it, and the albums skipped with why:
  - "Split into N tracks, then ReplayGain" (with the cue's name);
  - "Rename N files, then ReplayGain" or "Rename N files" (each rename as old → new, expandable);
  - "ReplayGain (missing tags)" / "ReplayGain (target −18 LUFS, now −15)";
  - "Already tidy";
  - "Skipped: …" with the reason.
- A summary line on top ("12 albums: 3 to split, 2 to rename, 5 to ReplayGain, 2 already tidy, 1 skipped").
- **Run** starts the job; **Back** leaves without changes. Run is disabled when nothing is to be done.
- The plan is the run's: an album changed on disk meanwhile is re-checked when its turn comes; a rename whose
  file changed is skipped, as the Rename tool does.

## The run
- One job in the Jobs panel (§10), kind **Tidy**, over the whole tree, **one album at a time**: the split (one flac
  process), the renames, then ReplayGain (one rsgain process), each with its own lock on the album folder (and
  the parent's for ReplayGain on a disc folder). Background indexing pauses meanwhile, as for the split and
  ReplayGain. The page shows the album in progress and live log lines.
- **Back** stays on the page while Tidy runs (Hugo, 2026-10-17): it goes back to the Tools page and the run goes
  on; the Jobs panel (or the Tidy card's "Run Tidy…") shows the running page again.
- An album whose split fails or is stopped gets no ReplayGain, and nothing of it goes on the offer list. A failed
  rename or ReplayGain doesn't stop the run: the next album goes on.
- **Stop** stops the current step as the tools do (a split leaves nothing half-written) and no further album
  starts. The offer list still opens for the albums split before the stop.
- The **report** lists every album with what was done, what failed and why, and the skipped ones. Each album's
  ReplayGain writes its own `replaygain.log`, as the ReplayGain tool does.
- The index re-reads the tree afterwards (`Indexer.scan_path(folder, recursive=True)`).

## The offer list ("ask to be deleted")
- When the run ends (finished or stopped) and the list isn't empty, a **separate window** opens, not modal:
  "Files Tidy made redundant". Each row is a file: the image or the `.cue`, with its folder.
- Each row has **Keep / Delete** in a drop-down (Hugo, 2026-10-17), **Keep** by default. Rows can be selected
  freely (click, Ctrl, Shift, Ctrl+A) and set together with "Keep selected" / "Delete selected". A row also opens in File Explorer (context menu).
- **Apply** sends the rows marked Delete to the **Recycle Bin** (no question since 0.7.4, Hugo); the
  window then closes. **Closing the window keeps every file** (Hugo). Nothing is ever deleted permanently: on a
  drive without a Recycle Bin (`fsops.can_recycle`), its rows can't be marked Delete and say why.
- Only the image and its `.cue` are offered (Hugo): the rip's `.log`, `.accurip` and other files are never
  listed.
- **Ground rule 1** gains an entry: sending to the Recycle Bin the files the user marks Delete in this window.

## Kept from the old Tidy
- The Tidy card needs the File names, Rename and ReplayGain cards valid, flac and rsgain available; its button is
  "Run Tidy…". One job per tool: while a Rename, ReplayGain, Split or Tidy job runs, Tidy shows that job instead.

## As built
- **M17 task 1 (0.6.1):** `core/tidy.py`. `plan_tidy(root, rename_options, replaygain_options, masks,
  replacement, is_silent=…, read_info=…, cancel=…, progress=…)` gives a `TidyPlan`: `albums` (`AlbumTidy`: its
  `folders`, the album's own `FolderPlan` first, then its disc folders; `replaygain`, why ReplayGain runs or None;
  `replaygain_exclude`, the images never measured), `summary()` and `has_work`.
  - `find_albums` walks as `replaygain.find_album_folders` but counts any audio file (a folder of formats rsgain
    can't read is still renamed, with a note); a disc folder is a keyword subfolder matching none of the
    track-data and excluded keywords.
  - `plan_folder` sorts a folder: one audio file named by a single-file sheet is **not split**
    (`cue.plan_sheet` with the File names masks, every track, the sheet's folder); otherwise the folder is
    renamed (`rename.plan_renames`, the Rename card's options, not recursive) and is **half-split** when a
    sheet still names an image beside the tracks. Missing tags, names taken and the rename warnings become notes.
  - A sheet needs a human when the plan has a refusal, a fallback encoding, a warning (data tracks), a hidden
    track (number 0), a path problem or a renamed track (`plan.notes`). Without `is_silent` any pregap of 2 s or
    more counts as a hidden track; the job passes flac's silence check.
  - `replaygain_needed` reads every track rsgain scans (images aside): the four values, and a comment line
    matching the template (`lufs_comment_pattern`) whose number is the target. Disc folders' tracks need the two
    album values. An unsplit image is never measured, even when its album is skipped.
- **M17 tasks 2-4 (0.7.0)**, built together after Hugo ran the old Tidy on an EAC album (it asked to rename
  files that didn't exist yet, and showed a "Run ReplayGain" button):
  - `services/tidy_job.py` `TidyRunner`: `plan()` on its worker (flac's silence check, `planProgress`), `run()`
    under one lock on the tree (owner "the Tidy tool"), album by album: `split.cut_album` + `finish_album` for
    each not-split folder, `rename.apply_plan` for the renames, then `replaygain.process_album(…, exclude=images)`.
    `process_album` and its copy step gained `exclude`: the album's images (split ones and ones waiting) are never
    measured nor tagged. A failed or stopped split ends that album (no ReplayGain, nothing offered).
    `report_text` / `short_result` for the page and the jobs panel.
  - `ui/tools/tidy_page.py` `TidyPage`: states planning, plan, running, result, error. The plan is a tree: each
    album with "Split image.cue into 2 tracks, then ReplayGain (after the split)", "Rename 1 file, then ReplayGain
    (missing tags)", "Already tidy" or "Skipped: …"; its children are the renames (old → new), notes, disc folders
    and the files to be offered. The only action button is **Run Tidy** (disabled with nothing to do).
  - `ui/controllers/tidy.py` `TidyController` (`window.tidy`): the Tidy card's request opens it; one Tidy at a
    time (a second request shows the running one); the job in the jobs panel (kind Tidy, report "tidy"); indexing
    paused while it runs; the tree re-read after it.
  - `ui/tidy_offer.py` `OfferWindow` ("Files Tidy made redundant"): File, Folder, Choice; Keep / Delete selected,
    a double-click toggles (0.7.2: a drop-down per row instead), the context menu has Keep, Delete and Open in
    File Explorer. Apply asks (`confirm_apply`; 0.7.4: no longer asks) and sends the Delete rows to the Recycle Bin; a drive without one
    shows "Keep (no Recycle Bin: …)" and can't be marked. Closing keeps all.
  - The Tidy card lost its scope; its description says what Tidy does. The old Tidy branches of the Tools
    controller and the rename preview ("step 1 of 2", "Run ReplayGain") are gone.

- **0.7.2** (Hugo, 2026-10-17): the page's Back is visible while Tidy runs (tooltip "Tidy goes on, and the Jobs
  panel shows it again"); `TidyController.back` leaves the job running. The offer window's Choice column is a
  `QComboBox` per row (Keep, Delete; `choice_box(item)`), none on a drive without a Recycle Bin; the double-click
  toggle is gone. The wheel never changes it (Ground rule 7).

## Tests
- Album discovery: lite and bonus never split or renamed; a CD2 image split and its parent's ReplayGain run.
- Classification: not split, split, half-split, already tidy, the skip reasons (fallback encoding, a warning, a
  refusal, two sheets for one image).
- The ReplayGain check: all tagged at the target (tidy), one track missing a value, another target in the comment.
- The run: split then ReplayGain; rename then ReplayGain; a failed split skips ReplayGain and the offer; Stop.
- Back while Tidy runs leaves it running; the jobs panel shows its page again.
- The offer window: Keep by default, a row's drop-down, multi-select with the buttons, Apply recycles only Delete rows,
  closing keeps all, no Recycle Bin on the drive.
