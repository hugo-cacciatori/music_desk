# 12. Settings
**No Settings dialog** (Hugo, 2026-10-15). Each preference is set where it is used, and a dialog would only
duplicate those controls:
- library folders: the sidebar (Add folder…, Remove from library);
- hide folders without music: the folder tree's context menu (the View menu until M16);
- offline mode and auto-save of found lyrics: the Lyrics menu (both retired by M12, §26);
- the tools' options: their cards, which remember every change (§10), in the **Options window** (File >
  Options…, or the toolbar's Options…; Hugo, 0.8.3, §30): a list on the left (File names, Rename, ReplayGain),
  the card on the right. It gathers the settings used in several places; the other preferences stay where they
  are used;
- (M13) the trim's subfolder name: the Trim page (§23); "Save beside <file> without asking" for new cue
  sheets: the Split page (§22);
- light or dark, and text size: Qt follows Windows.

The planned "watch for changes" and "tag-reading workers" settings were dropped: they answer no user need (the
watcher always runs, with revalidation on open as its fallback, and the scanner uses 4 workers).
`settings.py` keeps the preferences above and the windows' UI state in settings.json.

**Help › About MusicDesk** (`ui/about_dialog.py`):
- the app version, the Python and Qt versions, and the rsgain version from the startup self-check (or "rsgain
  unavailable");
- a table of the third-party components with their versions and licences (`music_desk/licenses.py`
  `COMPONENTS`);
- "Open licences folder", which opens `resources/licenses/` in Explorer through the window's shell thread.
