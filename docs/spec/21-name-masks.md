# 21. File name masks (shared)
Specified with Hugo on 2026-10-16 (M10 task 1). The masks that name music files from their tags become one
app-wide setting, used everywhere the app names a music file. Today they are a Rename card option, and the rename
hint after a tag save (§7, §11) uses the *default* masks, not the user's.

**What a mask is** (unchanged from the script, §10 A, §17)
- A mask is a Python format string over these fields:
  - `{track:02}` `{track_total}` `{disc}` `{disc_total}`;
  - `{title}` `{artist}` `{albumartist}` `{album}` `{year}` `{genre}` `{composer}`.
- Masks form an ordered list, and the first one whose fields are all present names the file. Characters not allowed
  in file names are replaced by the **replacement** (default `-`), and the lowercased extension is added.
- Defaults: `{track:02} {title}`, then `{title}`.

**One setting**
- `Settings.name_masks` (list of str) and `Settings.name_replacement` (str).
- Migration on load: when they are absent, they are taken from the old `rename_options` masks and replacement, so
  an existing settings.json keeps the user's masks. A mistyped value falls back to the defaults, as for every
  setting.
- `rename_options` stops saving masks and the replacement. `RenameOptions` keeps its `masks` and `replacement`
  fields, filled from the shared setting when a run starts, so `core/rename.py` and its parity tests don't
  change.

**`core/masks.py`** (no Qt): the mask code moves out of `core/rename.py`, and `rename.py` imports it.
- `FIELDS` (from `tags.NUMBER_FIELDS` and `RENAME_TEXT_FIELDS`) and `FORBIDDEN_CHARACTERS`.
- `mask_fields`, `sanitize`, `mask_values`, `choose_name`, `missing_from`.
- `validate_masks(masks, replacement) -> list[str]`, the mask half of `rename.validate`, which now calls it.
- `name_for(fields, masks, replacement, extension) -> str | None`, the full file name.

**Where it is edited: a "File names" card** (Hugo: at the top of the Tools page)
- First on the Tools page (§10), above the tool cards. It holds the existing mask editor, moved from the Rename
  card unchanged:
  - the ordered list (drag to reorder, Add, Remove, Insert field);
  - the replacement;
  - the live example from the first track of the scope.
- Its text: "How MusicDesk names music files from their tags: the first mask whose fields are all present is
  used. Rename, Split albums and the rename suggestion after you save tags all use these masks."
- Inline validation. While it fails, Run on the Rename, Tidy and Split cards is disabled, and those cards say "Fix
  the file names first."
- It has its own "Reset to defaults", and saves on every change, as the cards do.
- The Rename and Tidy cards lose their mask editor and replacement, and show one line instead: "File names: the
  masks in the File names card above."

**Who uses the masks**
- The Rename tool and Tidy (§10 A), unchanged apart from where the masks come from.
- The **rename hint** after a single-file tag save (§7, §11): `metadata.suggested_name` takes the masks and
  replacement as arguments, and TagIO passes the current setting with each read.
- The **cue split** (§22): each track's fields come from the cue sheet, then go through the same masks.

**Tests**
- An old settings.json with custom Rename masks migrates them.
- The hint follows a custom mask.
- Rename and Split name the same fields the same way.
- An invalid mask disables the three Run buttons.
- The rename parity tests pass unchanged.
