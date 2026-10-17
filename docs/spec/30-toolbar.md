# 30. The tools toolbar and the Options window (M21, 0.8.3)
Specified with Hugo on 2026-10-17. It replaces the Tools page (§10 "Tools page") and the toolbar's Tools,
Playlist builder and Advanced search buttons (§5).

**The need** (Hugo): the tools are always one click away, without opening a page first; a tool's options are
apart from starting it; settings used in several places (the file name masks) live in one global place.

## The two toolbar rows
- **Top row** (Navigation): Back, Forward, Up, Rescan folder, the **address bar** and the **search field**. The
  address bar and the search field share the rest of the row (both stretch; the search field has no maximum
  width any more).
- **Second row** (Tools), always visible, full width below the first: buttons with an **icon and a short
  label** (drawn by the app, in the palette's text colour, so they follow light and dark themes):
  1. **Rename** ▾: a dropdown with **Start on <target>…** and **Options…**.
  2. **ReplayGain** ▾: a dropdown with **Start on <target>** and **Options…**.
  3. **Tidy**: no dropdown (it has no options of its own: it runs with Rename's and ReplayGain's). A click shows
     Tidy's plan for the target (§28), as "Run Tidy…" did.
  4. a separator, then **Playlist** (checkable: shows or hides the playlist builder, Ctrl+Shift+P, §15) and
     **Advanced search** (opens its window, §26).
- The Jobs button stays in the status bar (Ctrl+J).

## What Start acts on (the target)
- **Rename**: the audio files selected in the file list (only those and their sidecars, never junk, §10), else
  a single selected folder, else the open folder.
- **ReplayGain** and **Tidy** work on folders: a single selected folder, else the open folder (a file selection
  counts as its folder).
- The dropdown names the target when it opens: "Start on Kid A…", "Start on 3 selected files…". With no usable
  target it reads "Start (open a folder first)" and is disabled. With options that need fixing it reads
  "Start (fix its options first)", disabled, and Options… shows why; while rsgain can't run, ReplayGain's reads
  "Start (rsgain couldn't run)". Tidy's hover text says what it works on; a click with no target or bad options
  shows the reason in the status bar.
- ReplayGain still has no preview: its Start runs at once (Hugo, 2026-10-15), so the named target is what tells
  you where it runs. Rename shows its preview first, Tidy its plan.
- A tool runs one job at a time, as before: Start while its job runs shows that job.

## Options
- **One Options window** (Hugo, 0.8.3; it replaced M21's one dialog per card and the Options menu), opened from
  **File > Options…** (above Exit) and from the toolbar dropdowns' **Options…**. Not
  modal; one window, reused: opening it again raises it.
  - On the left, a **list** of its pages: **File names**, **Rename**, **ReplayGain**. A click shows that card
    on the right, in a scroll area (the card at its own height). Below, **Close**.
  - **File names** (§21): the masks and the replacement character, used by Rename, Tidy, Split and the
    Metadata tab's suggested name. Its live example uses the first track of the selection or of the open
    folder when the window opens.
  - **Rename** and **ReplayGain**: the tools' cards (§10), with their Reset. The cards' own run buttons aren't
    shown there.
  - A toolbar dropdown's Options… opens the window on its tool's page; File > Options… opens it where it was
    left (File names the first time).
  - Changes are saved as they are made, as the cards always did; Close (or Esc) only closes.
- **M21's Options menu is gone** (Hugo, 0.8.3): its three entries are the window's pages.
- **The File menu** (Hugo, 0.8.4) holds only Options… and Exit: Add folder… (the sidebar, a drop or Ctrl+O) and
  Rescan library (the startup rescan, F5 and Rescan folder) left it.

## Gone
- The Tools page and its sections; the right-click **Tools…** entries (file list, folder tree) and the folder
  summary's **Tools…** button (Hugo: only the toolbar starts the tools). "Remove ReplayGain tags…" stays in the
  right-click menus.
- Pages that went "Back to the Tools page" (the rename preview, the ReplayGain report, Tidy's plan) go back to
  the file list.

## As built
- **M21 (0.8.0):** `ui/tools/tool_cards.py`: `ToolCards` (the three cards, saved as they change; `problems(tool)`,
  `request(tool, scope)` turning a target into a `ToolRequest`, ReplayGain and Tidy with the files dropped). The
  cards' run buttons are hidden; the Tidy card is gone (its checks are `problems(TIDY)`).
  - `ToolsController.target(tool)`, `start_text(tool)`, `start(tool)`, `open_options(which)` (File names reads its
    live example from the target's first track).
  - `MainWindow._build_tools_bar`: `tools_bar` (after a toolbar break), `tool_buttons` (InstantPopup QToolButtons
    whose menu's `aboutToShow` calls `_update_start`), `start_actions`, `tidy_action`, then `playlist_action`
    ("Playlist") and `advanced_search_action`. `ui/toolbar_icons.py` draws the five icons in the palette's
    ButtonText colour. The search field lost its 320 px maximum.
  - The Back buttons of the rename preview and the ReplayGain report read "← Back" and go to the file list.
  - File > Options…'s status tip is in `hover.MENU_TIPS`.
- **0.8.3:** `OptionsWindow` in `tool_cards.py` (`list`, `stack`, `pages`, `current`, `show_page(which)`)
  replaced `CardDialog`; `ToolCards.open_options(which=None)` makes it on first use (`options_window`).
  `ToolsController.open_options(which=None)` reads the File names example whatever the page. The Rename card's
  line reads "File names: the masks set on the File names page."; the Split page's says "(File > Options…, File
  names)".

## Tests
- The top row holds the address bar and the search field, and no tool buttons; the second row has Rename,
  ReplayGain, Tidy, Playlist and Advanced search, each with an icon and a label.
- The dropdown's Start names the target (a folder, a file selection) and is disabled without one; Rename's Start
  previews the selection; ReplayGain's Start on a file selection runs on its folder.
- File > Options… opens the Options window, its list showing each card; a dropdown's Options… opens it on its
  tool's page, the same window each time; a change there is saved as it is made. No Options menu.
- No "Tools…" in the context menus or the folder summary.
