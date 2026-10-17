# 25. The mouse wheel never changes a value
Asked by Hugo on 2026-10-17, for later (not scheduled, not built app-wide). It is Ground rule 7.

## The need
- Scrolling a page with the mouse wheel while the pointer passes over a number field or a drop-down changes its
  value by accident: Qt's spin boxes, combo boxes and sliders take the wheel when hovered, focused or not. The
  user notices late, or not at all, and runs a tool or saves with a value they never chose.
- The ReplayGain card's number fields were fixed this way on 2026-10-15 (`NoWheelSpinBox` in
  `ui/tools/replaygain_card.py`, HISTORY.md). This rule extends that fix to the whole app.

## The rule
- **No input changes its value from the mouse wheel**: number fields (spin boxes, time and date fields), option
  selectors (combo boxes, drop-downs), sliders, and any other control that edits a value, the waveform handles of
  the Trim page (§23) included.
- The wheel **scrolls the page** instead, as if the pointer were over empty space (the event is ignored, so it
  goes to the scroll area around the control).
- The value still changes by typing, by the control's arrows, by the keyboard (arrow keys, Page Up/Down) and,
  for a selector, by opening its list with a click.
- Not affected: lists and views that scroll their own content (the folder tree, the file list, the playlist,
  the Split page's table, a combo box's open list), where the wheel scrolls and changes nothing.

## Building it
- One shared way to do it, not a subclass per card: for example an application-wide event filter that ignores
  wheel events on `QAbstractSpinBox`, `QComboBox` and `QAbstractSlider` (the waveform handles do it themselves),
  replacing `NoWheelSpinBox`.
- A UI test that sends a wheel event to each kind of input on a page and checks its value didn't change, and
  that the page scrolled.
