# 10. Tools
This is the port of music_tools. The tools open from the folder context menu, the toolbar's **Tools** button (acts
on the current folder) or the folder summary. The **Tools page** is non-modal and takes the center area with a back
button, so browsing stays possible. Each card starts with a plain-language paragraph saying exactly what will
happen to which files.

From M10 the page shows, in order: the **File names** card (the shared masks, §21), then Rename, ReplayGain and
Tidy. The cue split isn't a card: it starts from a `.cue`'s preview and has its own page (§22, Hugo, 2026-10-16). The masks and the replacement below move to the File names card; the Rename and
Tidy cards keep their other options.

**A. Rename files from tags**
- "Renames every music file in this folder (and its subfolders) from its tags, using the first mask whose fields
  are all present. Characters not allowed in file names are replaced. A file is never renamed over another one;
  files that fit no mask keep their name."
- Options (defaults are the script's, except the file types and the junk list):
  - Masks: an ordered list you can add to, remove from and drag to reorder. A field picker inserts `{track:02}`
    `{track_total}` `{disc}` `{disc_total}` `{title}` `{artist}` `{albumartist}` `{album}` `{year}` `{genre}`
    `{composer}`, and a live example uses the first file's tags. Default: `{track:02} {title}`, `{title}`.
  - Replacement for invalid characters: `-`.
  - Include subfolders: on.
  - **Send junk files to the Recycle Bin**: off. Default extensions: .cue .nfo .sfv .txt .lrc. That's the script's
    list without .m3u, which is a playlist (§14, §15). The list is editable, within the protections below.
  - Advanced: music file types, defaulting to every audio extension in §1.
- **Cleanup protections**, so the cleanup never works against the rest of the app:
  - Validation refuses these extensions in the junk list:
    - every audio extension in §1 (the script already refused overlaps);
    - playlists: .m3u .m3u8 .pls, which players use (and the playlist builder exports, §15);
    - cover images: .jpg .jpeg .png, since `folder.jpg`, `cover.jpg` and `front.jpg` are cover sources (§7).
  - The ReplayGain log (its configured file name, `replaygain.log` by default) is never junk, whatever its
    extension.
  - Some files are kept case by case. The preview lists them as "kept", with the reason:
    - A .cue sheet for a single-file album is needed to play that album. That's a sheet whose FILE lines point to
      one audio file in the folder holding several tracks. It is kept, and its audio file is left out of the
      rename, since a new name would break the reference. Status: "kept: used by <name>.cue".
    - Other .cue sheets (one FILE per track, or files that don't exist) are junk, as in the script. When cleanup is
      off and the files they list get renamed, the preview warns that the sheet won't match them anymore.
    - Retired by M12 (Hugo, 2026-10-17; §26 "Retired with M12"): the protection below: a `.lrc` is junk like the other default extensions.
    - A .lrc file whose audio file (same name stem) has no lyrics tag yet is kept, because recycling it would lose
      those lyrics. Status: "kept: lyrics not in the tags yet". The Metadata tab offers "Import from <name>.lrc"
      for that track (§7).
  - A .lrc sidecar that stays is renamed along with its audio file, here and with F2 (§11), so it stays matched.
    If the sidecar's new name is taken, the audio file is still renamed and the sidecar is left, with a warning.
- Validation is the script's `validate_config` plus the protections above, shown inline. Run stays disabled until
  it passes.
- **Preview** (always shown first; this is the dry run): an "Old name → New name" table with a status per row:
  rename, unchanged, fallback mask (missing: …), missing tags, name taken, unreadable, kept (with the reason). Junk
  files and renamed sidecars are listed separately, the table can be filtered by status, and the counts match the
  script's summary.
- **Apply** executes exactly the previewed plan and re-checks each item; a file changed since the preview is
  skipped with a warning. Junk files go to the Recycle Bin (the preview lists them; no question since 0.7.4, Hugo 2026-10-17). From M8,
  a junk file that can't be recycled (a network share, a removable drive: §19) is kept and reported, never
  deleted permanently. The result
  summary has the same sections as the script.
- Also works on a file selection, renaming only the selected files and their sidecars. A selection run never
  sends junk to the Recycle Bin.
- Details pinned at M1 (`core/rename.py`):
  - The preview simulates the renames in the script's walk order, so "name taken" matches what the script's
    *real* run did. The script's own `--dry-run` checked names against the untouched folder and could differ.
  - A `.lrc` follows an audio file only when exactly one audio file in the folder has its stem (ignoring case).
  - A single-file album is a sheet whose FILE lines all name one audio file present in the folder, with at least
    two TRACKs. The sheet shows "kept: needed to play <audio>", and the audio file shows
    "kept: used by <name>.cue".

**B. ReplayGain**
- "Measures loudness with rsgain at the target level, lowers any track gain that would clip, sets the album gain
  to the lowest track gain and the album peak to the highest track peak, and writes 'LUFS Target : X' in the
  comment. It then copies the album values into the lite/bonus/CD2 subfolders, plus the track values into 'lite'
  subfolders for files with the same name. Its report replaces replaygain.log in the album folder."
- **The log holds the last run only** (Hugo, 2026-10-16; a deliberate change from the script, which appended):
  each run replaces the album's `replaygain.log`. Within one discography run, the leveling line follows that
  run's report in the same file. The discography summary replaces the artist folder's log, unless that folder
  was one of the run's albums, whose report it then follows.
- **Removing ReplayGain** (Hugo, 2026-10-16): "Remove ReplayGain tags…" in the menus of tracks, folders (with
  their subfolders), the list background and search results.
  - It removes every `REPLAYGAIN_*` tag (`TaggedFile.remove_replaygain`, all families; any case, extra values
    such as a reference loudness included), and the comment lines the ReplayGain step writes: the card's
    comment template with any number for `{target}` (`replaygain.lufs_comment_pattern`). The user's other comment
    lines stay.
  - It asks first (`MainWindow.ask_remove_replaygain`, with the track count and the folder), then runs on the
    TagWriter (`remove_replaygain` → `replaygainRemoved`): one file at a time under a short lock, refused for files
    under a job's lock, the index updated after each file, and an unedited Metadata tab reloaded.
  - A file with nothing to remove isn't saved again. `replaygain.log` files are left alone (the app never deletes
    files).
  - The status bar reports "Removed the ReplayGain tags of N files. M had none."; refusals and errors come in a
    toast.
- Handles every format marked ✓ in the ReplayGain column of §1. Other audio files are listed as skipped.
- Scope:
  - **This album**: the current folder.
  - **Discography**: every folder below that holds music. Each album's lite/bonus/CD2 subfolders are handled by
    that album. Then every album is leveled to the lowest album gain, so the whole discography plays at the same
    level, and a summary goes to the artist folder's replaygain.log.
- Options:
  - Target loudness: −15 LUFS.
  - Advanced (collapsed):
    - safety threshold 0.99999 and safety margin 0.01 dB;
    - album-data subfolder keywords: lite, bonus, cd2, disc 2, cd 2;
    - track-data keywords: lite; excluded keywords: bonus;
    - log file name: replaygain.log; comment text: "LUFS Target : {target}".
- No preview (Hugo, 2026-10-15): the card's **Run ReplayGain** starts the job at once, as the script did. It only
  writes tags and the logs, and the report lists what was skipped (no audio, unsupported formats, a command line
  too long for one rsgain call). Tidy previews its rename step only.
- Run:
  - A background job that processes **one album at a time**, with one rsgain process at below-normal priority, so
    neither the UI nor Windows bogs down.
  - Shows per-album progress and live log lines.
  - Cancel ends the current rsgain run and stops before the next album. Leveling is then skipped, and the report
    says so.
  - As built at M1 (`core/replaygain.py`):
    - A cancelled album gets "Scan: CANCELLED" and a warning, and its result is STOPPED. Albums after it show
      CANCELLED in the discography summary, whose gain line reads "none, the run was cancelled before leveling".
    - A crash is written to the album's log as CRASHED. The job goes on; the script re-raised.
    - Audio files rsgain can't scan get a warning in the album's log ("skipped, format not supported by rsgain").
    - An album whose command line is too long is reported as "Scan: FAILED (command line too long)", and rsgain
      isn't started.
- Report:
  - Per album: the RunLog track table (Track, Loudness, Peak, rsgain gain, Final gain, Note), the album values,
    the subfolder lines, warnings and errors, and the result.
  - Discography summary table: Album, Result, Album gain, Leveled to, Files changed, plus notes.
  - Buttons: "Open replaygain.log" and "Open in File Explorer".

**As built (M5 task 1): the page and the cards** (`ui/tools/`):
- **Opening.** The page opens from:
  - the toolbar's **Tools** action (the selection, or the open folder);
  - "Tools…" in the folder menus (list and tree) and the file menu (greyed out without a music file);
  - the folder summary's **Tools…** button.
- **Scope** (`ToolScope`):
  - one folder;
  - else the music files among the selected items (a selection: renames only, with no subfolders or junk;
    ReplayGain and Tidy say they need a folder);
  - with nothing selected, the open folder.
- **Layout.** The page replaces the file list in the centre (a QStackedWidget), with "← Back". Opening another
  folder returns to the list.
- **Rename card:**
  - an ordered, editable, drag-reorderable mask list, with Add, Remove and "Insert field";
  - a live example from the first track (natural order, read off the UI thread by `TagIO.read_rename_fields`);
  - the replacement, subfolders, Recycle Bin and junk types; music file types under "Advanced".
- **ReplayGain card:**
  - scope (This album / Discography);
  - the target, whose spin box only allows rsgain's −30 to −5 LUFS;
  - "Advanced" with the threshold, margin, keywords (comma-separated, since they may hold spaces), log file name
    and comment text.
- **Tidy card:** no options of its own: the other cards' (File names, Rename, ReplayGain). "Run Tidy…" opens Tidy's
  plan (§28, since 0.7.0; before, a scope and the rename-then-ReplayGain flow below).
- **Validation**, inline, under each card:
  - Rename: `rename.validate`.
  - ReplayGain: the new `replaygain.validate`. The script had no ReplayGain checks. It checks rsgain's target
    range, a threshold in (0, 1], a margin of 0–6 dB, non-empty lowercase keywords (folder names are compared in
    lowercase), a valid log file name, and a comment that only uses `{target}`.
  - Tidy: needs both cards valid.
  - A card's button stays disabled until its card is valid: "Run rename…", "Run ReplayGain", "Run Tidy…" (named on
    2026-10-16 with Hugo: the tools are *run* from their card; the "…" means a preview to check comes first, and
    ReplayGain has none); the ReplayGain and Tidy buttons also while rsgain is unavailable (the startup self-check).
- **Saved options.** Each change is saved to `Settings.rename_options` / `replaygain_options` (JSON through
  `options_to_json` / `options_from_json`). A mistyped saved value falls back to its default; a well-typed invalid
  value is kept for validation to show. "Reset to defaults" is per card.
- **The run:** a Rename request carries the ReplayGain card's log file name as `protected_names`, so the
  configured log is never junk.

**As built (M5 task 2): Rename preview and apply** (`services/rename_job.py`, `ui/tools/rename_preview.py`):
- **Running.** `RenameRunner` runs `plan_renames` (now cancellable: `PlanCancelled`) and `apply_plan` on one
  worker thread. A newer preview cancels an older one still reading tags. A selection plans without subfolders or
  junk.
- **The preview page** replaces the Tools page in the centre, titled "Rename: check the changes". "← Back to
  options" (at the top, and next to Apply) returns to the card with its options as they were, changing nothing,
  and cancels a preview still reading tags. The primary button is "Apply renames". There is no Close in the
  preview, only Apply or Back; Done closes the result:
  - the summary line in the script's dry-run wording;
  - a table (Folder, Name, New name, Status, Details), sorted by folder then name, with a status filter that
    shows the counts. The statuses are rename, fallback mask (missing: …), unchanged, missing tags (missing: …),
    name taken, unreadable, and kept (with the reason); a rename's details say when its .lrc follows;
  - below the table: the junk files ("would be sent to the Recycle Bin as junk"), the
    sidecars that follow, and the warnings;
  - Apply is enabled only when something would change.
- **Apply:**
  - Unsaved Metadata edits on a file in the folder ask first (`can_leave`).
  - With junk in the plan, `MainWindow.ask_recycle(names)` lists them, with three choices: "Rename and recycle",
    "Rename only" (the junk items are dropped from the plan) or Cancel.
  - The run holds a `PathLocks` lock on the folder tree (owner "the Rename tool"), taken on the UI thread, so a
    conflicting job is refused at once with a toast. Stop cancels between two files.
- **The result** uses the script's summary sections and wording (`rename.summary_sections`; a parity test checks
  the titles against the script's `print_summary`), with "sent to the Recycle Bin" instead of "deleted", plus the
  app's sections: kept, .lrc files that followed, files skipped because they changed since the preview, and
  warnings.
- **The index:** `RenameReport.steps` (every rename, sidecars included, in order) goes to `Indexer.apply_renames`
  (ids, tags and the selection are kept), one folder at a time, so a recursive rename's subfolders follow too.
  Then the whole scope is refreshed (`MainWindow.refresh_after_tool`), recycled junk included.
- `rename.preview_report(plan)` is what apply would report if nothing changes: a test checks it against
  `apply_plan`'s report on the cleanup fixtures.

**As built (M5 task 3): ReplayGain and Tidy** (`services/replaygain_job.py`, `ui/tools/rg_report.py`):
- **Preview:** removed in the tweaks before M7 (2026-10-15), with `replaygain.preview`, `AlbumPreview` and the
  page's preview table. The card's Run starts the job; a folder with nothing to scan ends as "Nothing was done".
- **Job:** `ReplayGainRunner.run` takes a `PathLocks` lock on the folder tree ("the ReplayGain tool") on the UI
  thread, so a conflict is refused at once. It runs `process_album` or `process_discography` on a worker thread.
  - Log lines (rsgain's output and the Reporter's info, warnings and errors) reach the page in batches, at most
    10 a second.
  - Per-album progress comes from the Reporter.
  - Stop cancels: rsgain is killed, the album is reported CANCELLED, and nothing is leveled (the M1 rules).
- **Report:**
  - the result line ("Done: N albums OK. Every album was leveled to X dB.", "Done, but N albums need checking",
    or "Stopped…");
  - for a discography, the summary table (Album, Result, Album gain, Leveled to, Files changed);
  - each album's RunLog text, exactly as written to its replaygain.log, in a monospace view.
    `process_discography(on_album=)` hands over each album's RunLog.
  - "Open replaygain.log" (the folder's log; the artist folder's for a discography) and "Open in File Explorer",
    launched on the shell thread.
- **Index:** after the job, `Indexer.scan_path(folder, recursive=True)` re-reads the files whose size or mtime
  changed (the new ReplayGain tags and the comment).
- **Unsaved Metadata edits** on a file in the folder ask Save, Discard or Cancel before a run (the rename tool
  does the same).
- **Tidy:** see §28 (since 0.7.0). The old flow (a rename preview, "Tidy, step 1 of 2", then ReplayGain as step 2)
  is retired: Hugo saw it ask to rename an unsplit EAC album and offer a "Run ReplayGain" button inside Tidy.

**Layout** (Hugo, 2026-10-17): the cards come in three sections, each under a small upper-case heading as the
sidebar's LIBRARY: **Shared settings** (File names), **Tools** (Rename, ReplayGain) and **All in one** (Tidy). A
card's title is larger than its text, its description in a softer colour, and its Run button bold, set apart from
Reset to defaults; cards are 12 px apart, sections 20 px more. Built in 0.6.2 (`tools_page.ToolsPage.sections`,
`rename_card.Card.primary`).

**C. Tidy album**: replaced by §28's Tidy in 0.7.0 (M17). The script's run without flags was A, then B on the
final names; the app's Tidy does that and the split, over the whole tree, from one plan.

| Script                | App |
|-----------------------|-----|
| (no flag)             | Tidy (§28)  |
| `--rename-only`       | Rename files from tags |
| `--gain-only`         | ReplayGain, scope "This album" |
| `--discography`       | ReplayGain scope "Discography" (also available in Tidy) |
| `--dry-run`           | Preview, always shown before Apply |
| config block          | card options; defaults saved in Settings, "Reset to defaults" per card |
| `RSGAIN_PATH`         | bundled, fixed |
| `PAUSE_AT_END`        | n/a |
| seek_lyrics `--offline` / `--embed` | Settings (§9) and "Save to file tags" (§8) |
| seek_lyrics songs.html | dropped as a file; its search box is preserved in library search (§6, "Preserved from seek_lyrics") |

**Jobs panel** (bottom drawer): running and finished jobs with progress, logs and reports.
- Two jobs can't overlap on the same folder tree.
- Files under a running job are locked against tag edits and renames.
- After a job, the affected folders are reconciled in the index and the selection is kept.
- As built (M5 task 4: `services/jobs.py` `JobList`, `ui/jobs_panel.py` `JobsPanel`):
  - **The list:** this session's jobs, newest first (not saved). The columns are Job (Rename, ReplayGain, Tidy),
    Folder (its tooltip says the folder is locked while the job runs), Status and Started.
    - Running: "Running (3 of 9): Kid A".
    - Finished: "Done: 7/12 files were renamed", "Done: 2 albums OK, leveled to -10.40 dB", "Stopped: nothing was
      leveled; see the report", "Failed: …".
  - **Where:** a bottom dock ("Jobs"), shown when a job starts. It is toggled by the status bar's **Jobs** button
    and Ctrl+J. Since M16 (Hugo, 2026-10-17; the View menu is retired) the button is always there, reading
    "Jobs" or "Jobs: N running".
  - **Buttons:**
    - Open (or a double-click) shows the job's page: live while it runs, its report once finished. A job keeps
      its reports, so older runs reopen.
    - Stop cancels: the rename stops between two files; rsgain is killed and nothing is leveled.
    - Clear finished.
  - **Browsing:** a running job's page can be left (Back, opening a folder). The job goes on, and its page is
    reopened from the panel. Tidy's second step doesn't pull you back to its page if you went browsing.
  - **One job per tool:** while a Rename runs, Rename and Tidy previews show that job instead (a new preview
    would cancel it otherwise); while ReplayGain runs, ReplayGain and Tidy do the same. A Rename and a ReplayGain
    job can run together on different folders; the locks refuse an overlap.
  - **Tidy** is one job across both steps, keeping both reports.
  - **Quitting** with a job running asks first (`confirm_quit_with_jobs`), then stops it.
  - **The selection is kept after ReplayGain:** the job's recursive rescan updates entries in place, and the
    selected row stays selected, showing its new ReplayGain state (tested).
  - **M5's "Done when" through the window** (`test_jobs_panel.py`, slow): a ReplayGain job started from the
    window, with the real rsgain, writes the same tags and the same `replaygain.log` as the frozen script on a
    copy of the same album (timestamps masked; `compare_trees` from the parity tests).
