# 24. Hover texts
Asked by Hugo on 2026-10-17; scheduled as M15 the same day. Every button and control in the app gets a
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

## Settled with Hugo (2026-10-17, when M15 was scheduled)
- **Menu entries** (the menu bar, context menus) get no tooltip. The less obvious ones get a **status-bar tip**
  (`QAction.setStatusTip`): hovering the entry shows its one-sentence description in the window's status bar.
  Self-explanatory entries (Copy, Rename, Delete, Open) get none. Nothing pops up over the menu.
- **A guard test**: a UI test opens every page, panel and dialog and fails on any button or control without a
  tooltip, outside a short allow-list of the exclusions above, so a control added later can't forget its text.

## As built
- `ui/hover.py`: `exempt(widget, why)` marks a widget (and everything inside it) as needing no hover text: the
  folder tree, the file list, the address bar, the Metadata tab's tag fields, the toast (its message says what
  its button does). Where a control has a reason tooltip, the descriptive text is a constant the reason falls
  back to (`TRIM_TIP`, `NEW_CUE_TIP`, `TIMESTAMPS_TIP`), so it returns as soon as the reason no longer applies.
- The status bar's lyrics button takes its text's tip (Pause, Resume, Retry now) each time it changes.
- The guard (`tests/ui/test_hover_texts.py`) walks every button, combo box, spin box, slider (not scroll bars)
  and text field under a root, shown or not, and fails on one with no tooltip or one that only repeats its label.
  It leaves out what Qt builds itself (tab bar arrows, dock and toolbar buttons named `qt_*`, the text field of a
  spin box or combo box, a text field's clear and action icons, a dialog's standard buttons) and a hidden button
  with neither text nor icon (a placeholder filled when shown).
- M15 task 1 (0.5.2): the main window's toolbar and status bar, the sidebar, the file list's filter, the search
  field, and the side panel (folder summary, Metadata, Lyrics, file preview).
