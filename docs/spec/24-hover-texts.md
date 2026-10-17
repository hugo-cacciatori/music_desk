# 24. Hover texts
Asked by Hugo on 2026-10-17, for later (not scheduled, not built). Every button and control in the app gets a
**short descriptive hover text** (a Qt tooltip), so a user can find out what a control does without trying it.

## The need
- Many of the app's controls are specific to it (the Tidy and Rename options, the split's and the trim's fields,
  the playlist builder's buttons): their label alone doesn't always say what happens to which files.
- A hover text is the lightest answer (Lean UI): nothing on screen moves or grows.

## What is covered
- **Every button and control** on every page, panel, tab, card and dialog of the app: push and tool buttons,
  checkboxes, radio buttons, combo boxes, spin boxes, sliders, the waveform handles (M13), text fields whose
  purpose the label doesn't make plain, and column headers that need it.
- **Not covered** (Hugo): the literal explorer (the folder tree and the file list) and the address bar, and the
  common, familiar elements that explain themselves: OK, Cancel, Yes, No, Close, the window's title-bar buttons,
  scroll bars, a field whose label already says it all (a tag field like Title or Year).
- When in doubt, a control gets one.

## The text
- One short sentence, in the scripts' style: plain and specific, saying what happens to which files ("Writes the
  kept part as a FLAC copy in the subfolder; the original isn't changed."), not repeating the label.
- A keyboard shortcut, where the control has one, ends the text in brackets ("(Ctrl+K)").
- The existing tooltips that give a **reason** (a greyed control's reason, a locked file's job, a bad cell's
  error, an offline root, a dimmed playlist track) keep priority: while that reason applies, it is the tooltip;
  the descriptive text returns when it no longer does.

## To settle with Hugo when it is started
- Menu entries (the menu bar, context menus): Qt shows no hover text in menus by default. Leave them out, or give
  the less obvious ones a status-bar tip?
- Whether a UI test should walk every page's controls and fail on one without a tooltip (outside an allow-list
  of the exclusions above), so new controls don't forget theirs.
