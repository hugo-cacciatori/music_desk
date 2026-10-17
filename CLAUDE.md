# MusicDesk: CLAUDE.md

MusicDesk is a Windows desktop app (Python 3.13 + PySide6) for browsing a music library from a SQLite index,
editing tags, tidying albums (renaming from tags, ReplayGain with the bundled rsgain), looking up lyrics on LRCLIB
in the background and building playlists. It replaced `music_tools.py` and `seek_lyrics.py`; their frozen copies
are in `tests/reference/`.

This file is the working summary: state, rules, the work plan and open questions. It is the only doc loaded
automatically, so keep it short (under about 15 KB): how things were built belongs in the spec, not here.
- `docs/SPEC.md`: the spec's index. "SPEC §n" means `docs/spec/NN-*.md`; read the files the current milestone
  lists, not the whole spec.
- `docs/DECISIONS.md`: every dated decision. New decisions get a row there, and their spec section is updated at
  the same time.
- `docs/HISTORY.md`: how M0–M10 were built (session notes, finished work plan blocks, measurements). Read it only to find out
  why something is the way it is.
- `README.md`: the user-facing description.

## Resume here
- **State (2026-10-17, session 16):** 0.4.0 is on `main`, not released: M13 (trim and new cue sheets) is done.
  Full suite passed, slow included.
- **Optional, for later (Hugo):** in the installed app, on a copy of an EAC rip, save a second version of the
  cue and split from it; Preview in player opens in foobar2000 (Trim page and Split page). A release when he asks.
- **Next step:** M12 task 1 (SPEC §26 "Retired with M12"). Give Hugo full manual steps for checks (memory:
  manual-check-instructions).

## Work plan
Specified with Hugo on 2026-10-16. Each task ends with a patch version bump (0.2.2, 0.2.3, …). Tick tasks as
they are done. A task is done when its code, tests and docs are; releases and installed-app checks are never part
of it (see Session protocol 5).

**M12 Advanced search** (Hugo, 2026-10-17; read SPEC §26, then §5, §6, §7, §8, §9, §10 where they retire).
- [ ] 1. Retire the lyrics-tag features, the Lyrics menu (Find entries, auto-save, Offline mode as a setting).
- [ ] 2. Core: the two album searches over the index (no ReplayGain on some track; cue + image, no tracks).
- [ ] 3. UI: the Advanced search window (not modal), Start, results, double-click to the folder, Refresh search.
- [ ] 4. README and the minor version (0.5.0).

Done when: the window lists both searches' albums on a fixture library while the main window stays usable; a
double-click shows the folder; Refresh follows a fix; nothing in the app reads or writes the lyrics tag.

**Later: hover texts** (Hugo, 2026-10-17; SPEC §24). A tooltip on every control. Not scheduled, no tasks yet.

## Session protocol
1. Read this file, then the spec files of the current milestone. Pick up the first unchecked task. A block that
   waits on Hugo ("when Hugo says so", "to be specified with Hugo") isn't a stop sign (Hugo, 2026-10-17): ask
   him the questions its decisions need (explained from the need, memory: explain-from-the-need), record the
   answers in the spec and DECISIONS.md, then start it.
2. Hugo's global rule: before running anything destructive (deleting files, etc.), tell him the exact command
   first.
   - Hugo works in auto mode, whose safety check blocks some needed commands (running an installer, for
     example). When a step needs more privileges than auto mode allows, ask him to switch to manual mode (and
     say why) instead of working around the block; tell him when he can switch back.
3. Commit each finished task on `main`. Push, publish a release or install global tools only when Hugo asks.
   Project dependencies go in the project's virtual environment.
4. **Versions** (Hugo, M7; reminded 2026-10-16): don't forget them. Every finished task of the work plan bumps
   the patch version (0.2.2 → 0.2.3) in its own commit, following "Version bump" below. The last task of a
   milestone may bump the minor version instead (M10: 0.3.0). Docs-only commits and measurements don't bump.
5. **Releases and installed-app checks are never required** (Hugo, 2026-10-17). No task, milestone or "Done
   when" waits on a GitHub release or on an end-to-end check in the installed app: a release happens only when
   Hugo asks, and installed-app checks are optional, listed as "for later" and in Resume here, never blocking a
   tick. Plan them that way from the start.
6. At the end of a session:
   - tick the finished tasks in the work plan;
   - rewrite Resume here (state and next step, a few lines, no history);
   - add the new decisions to `docs/DECISIONS.md` and update the spec sections they touch;
   - move answered questions out of Open questions.
   - move each finished work plan block (all its tasks ticked) to HISTORY.md "Progress checklists" in the same
     commit; the work plan holds only unfinished blocks;
   - keep Resume here to about 5 lines (state, what's left to Hugo, next step), with no history;
   - check `wc -c CLAUDE.md`: over 15 KB, move finished or look-up material out (never rules, conventions or
     open tasks); if it's still over, tell Hugo the size and what takes the room.

## Ground rules
Code and tests cite these by number; keep the numbering.
1. The app never copies, moves, deletes or creates files or folders in the library. The only exceptions:
   - renaming files and folders (inline F2, or the Rename tool), including an audio file's same-stem `.lrc`
     sidecar, which follows its rename;
   - writing tags into music files (metadata editor, "Save to file tags" in the Lyrics tab until M12 retires it,
     ReplayGain tool);
   - writing `replaygain.log` in album folders (ReplayGain tool; each run replaces it, while the script
     appended);
   - writing a playlist the user exports from the playlist builder, where the user saves it (the Save dialog
     asks before replacing a file);
   - sending junk files to the **Recycle Bin**, only when the user ticks that option in the Rename tool and
     confirms. Audio files, playlists, cover images, the ReplayGain log, cue sheets of single-file albums and
     `.lrc` files not yet in the tags are never junk (SPEC §10);
   - (M8) sending the files and folders the user selects to the **Recycle Bin** with Delete, after a
     confirmation (SPEC §19);
   - (M10) creating the track files of a cue split's ticked tracks in the destination folder the user chose
     (an existing one), never over an existing file, and deleting that run's own `.partial` files. A split
     touches nothing else (SPEC §22);
   - (M10) saving a cue sheet from the Split page: Save overwrites the selected `.cue` after its old version
     goes to the Recycle Bin, Save as creates a `.cue` (a replaced one goes to the Recycle Bin first). Where
     Windows has no Recycle Bin, a `.cue` is overwritten only after the user agrees to lose it (SPEC §22).
   - (M13) a trim: creating its subfolder if missing (removed again only if that run made it and leaves it
     empty) and the copy there, never over a file; deleting that run's `.partial` (SPEC §23). Saving a new cue
     sheet beside its audio file, never over a file without asking (SPEC §22).

   This list grows when a feature needs to create or overwrite files: it is specified with Hugo, gets a row in
   DECISIONS.md and an entry here (Hugo, 2026-10-16).

   Nothing is ever deleted permanently: everything removed goes to the Recycle Bin, and only where Windows can
   recycle (`fsops.can_recycle`, SPEC §19). The only exceptions are the split's and the trim's own `.partial`
   files (and an empty trim subfolder its own run created). For anything else, the app offers "Open in File
   Explorer". Drags inside the app carry entry ids only (never file URLs) and only copy, because a drop of files
   in Explorer would move them. The app's own files (index, settings, logs, temporary playlists and preview cue
   sheets) live only in `%LOCALAPPDATA%\MusicDesk\`.
2. The UI thread never blocks. All scanning, tag reading and writing, rsgain runs and LRCLIB calls happen in
   background workers. Target: no event-loop stall above 50 ms.
3. Behavior parity: the ported logic behaves exactly like the scripts, except for the deliberate changes in
   SPEC §17. Frozen copies of the scripts live in `tests/reference/`, and parity tests compare the ported code
   against them.
4. `core/` never imports Qt. It is plain Python and unit-tested.
5. rsgain is bundled in `resources/rsgain/` and resolved through `paths.resource_path()`. Its path is never
   configurable. The same goes for flac in `resources/flac/` (M10).
6. Development and tests never touch a real library. Use the fixtures or copies.
7. The mouse wheel never changes an input's value (number fields, option selectors, sliders, any other
   input); over one it scrolls the page. Not yet app-wide: for later, SPEC §25 (Hugo, 2026-10-17).

## Standing decisions
The full log is `docs/DECISIONS.md`. These apply to all work:
- **Lean UI** (Hugo): a feature must answer a real need. Propose its lightest form first (a menu entry, an
  existing page), and never resize what is already on screen. Smart views and a Settings dialog were dropped for
  this reason; preferences live where they are used (SPEC §12).
- **Stack:** Python 3.13, PySide6-Essentials 6.12.0 (no Addons: no WebEngine, no PDF), mutagen for every tag
  read and write, SQLite (WAL, FTS5 trigram), watchdog, natsort (Explorer order), send2trash, urllib. uv with
  hatchling and a `src/` layout; pytest, pytest-qt, ruff, basedpyright; PyInstaller one-folder plus Inno
  Setup. tinytag 2.0.0 is test-only (parity). The stacks considered and rejected are in HISTORY.md ("Stack").
- **Cue split** (Hugo, 2026-10-16): the `.cue` is the only source of truth. The app stores no split state (no
  remembered options or edits); edits are kept only by saving them into a `.cue`. SPEC §22. (M13's remembered
  "Save beside … without asking" box is a preference, not split state: Hugo, 2026-10-17.)
- **Lyrics** (Hugo, 2026-10-17): the app's lyrics are LRCLIB's results, and no function relies on the lyrics tag
  (M12 retires them, SPEC §26). Features that touch lyrics start from LRCLIB (SPEC §9).
- **Licence:** GPL-3.0-or-later. Every bundled third-party component is listed in `music_desk/licenses.py`, with
  its text in `resources/licenses/` (SPEC §13).
- **Repo:** public on GitHub, `hugo-cacciatori/music_desk`, branch `main`.

## Conventions
- Python 3.13, type hints, ruff (line length 120), dataclasses for values, `pathlib` in new code.
- Match the scripts' style: short docstrings that explain why, and plain, specific user messages that say what
  happens to which files.
- `logging` writes to `%LOCALAPPDATA%\MusicDesk\logs\app.log`.
- Long work takes a `CancelToken`; workers talk to the UI only through queued signals (SPEC §16).

**Testing gotchas** (learned the hard way, details in HISTORY.md):
- Tests never open a real dialog: an autouse fixture fails them. Dialogs go through `confirm_*`, `ask_*` and
  `choose_*` methods that tests replace.
- Tests never use the real Recycle Bin (inject a recorder), the real network (an autouse guard blocks urllib's
  opener; LRCLIB is scripted with `tests/core/lrclib_fake.py`) or a real library.
- For signals emitted from worker threads, use `collect(signal)` and `qtbot.waitUntil`, not `qtbot.waitSignal`
  (a worker can emit while the blocker is being set up).
- pytest-qt closes the test's widgets before fixture finalizers run: a test with a running job must end it, or
  pre-answer `confirm_quit_with_jobs`.
- Test module names must be unique across folders (pytest refuses two `test_search.py`).
- A run that takes 30 s or more before starting is pytest deleting an old 100k-file temp tree, not a hang.
- The UI tests open real windows; some rely on window focus, so they don't run offscreen.

## Environment (checked 2026-10-15)
- Windows 11 Pro. Shells: PowerShell 5.1 (primary) and Git Bash.
- Python 3.13.1 at `C:\Users\Hugo\AppData\Local\Programs\Python\Python313`. The project venv is `.venv/`
  (pinned in `.python-version`; its mutagen 1.48.1 also runs the parity tests' reference scripts).
- uv 0.13.1 (winget). A shell started before its install may lack it on PATH: use
  `%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe`.
- Inno Setup 6.7.3 in `C:\Program Files\Inno Setup 6\`, for the installer build only.
- ffmpeg (dev-only, for regenerating fixtures, SPEC §18) at
  `C:\Program Files\ffmpeg\ffmpeg-master-latest-win64-gpl-shared\bin\ffmpeg.exe`. Its build has no Speex
  encoder.
- `gh` is logged in as hugo-cacciatori.
- My shells may see a sandboxed view of `%LOCALAPPDATA%\MusicDesk`: ask Hugo for his app.log, and run the
  installed app with a scratch `LOCALAPPDATA`.
- The original scripts' rsgain download is gone; `resources/rsgain/` is the only copy. To run the frozen
  `music_tools.py` for a comparison, point its `RSGAIN_PATH` at `resources/rsgain/rsgain.exe`, on a copy. Never
  edit `tests/reference/` (ruff excludes it; pytest collects neither `reference/` nor `fixtures/`).

## Commands
From the project root:

    uv sync
    uv run python -m music_desk
    uv run pytest                 # add -m "not slow" to skip rsgain integration tests
    uv run ruff check .
    uv run basedpyright           # the type check (also run by the suite, tests/test_types.py)
    uv run pyinstaller --noconfirm packaging/music_desk.spec    # -> dist/MusicDesk/MusicDesk.exe
    uv run python packaging/build_installer.py                  # both builds -> dist/installer/MusicDesk-Setup-<v>.exe
    uv run python packaging/make_icon.py                        # redraw resources/icons/ after changing the icon

**Version bump:** `music_desk.__version__`, `packaging/version_info.txt` (`tests/test_packaging.py` checks they
agree) and pyproject's `version`, then `uv lock`.
**Release** (only when Hugo asks, never a task's condition): bump the version if needed, run `uv run python packaging/build_installer.py`, then
`gh release create v<version> dist/installer/MusicDesk-Setup-<version>.exe`.

## Project layout
`src/music_desk/`: `core/` (no Qt; `index/` is the SQLite index), `services/` (Qt workers), `ui/` (`side_panel/`,
`tools/`), plus `app.py`, `paths.py`, `settings.py`, `licenses.py`. `resources/` (rsgain, flac, icons, licences),
`tests/` (`reference/`, `fixtures/`, `core/`, `ui/`), `packaging/`, `docs/`. File by file: SPEC §16 "Project layout".

## Open questions
None.
