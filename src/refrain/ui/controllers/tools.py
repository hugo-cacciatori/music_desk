"""The toolbar's tools (SPEC §30, §35) and the Options window.

Each button starts the right-click entry for what a right-click would act on: the file list's selection, or the
open folder when nothing is selected (Explorer's empty space). Tidy, Rename and Clean up start their entry, whose
window opens beside the main window (app.Starter); ReplayGain lists its entries for that selection, as the
right-click menu shows them. A selection the entry doesn't take (Tidy on files) is refused in the status bar, with
what to select instead. The entries' rules, labels and help texts are `context_menu/entries.py`'s.

Also here: the right-click ReplayGain and Rename windows' headings and checks, shared with their windows.
"""

from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMenu

from refrain.context_menu import request as menu
from refrain.context_menu.entries import MenuEntry, Selection, menu_entries
from refrain.core.text import plural
from refrain.services.replaygain_job import ALBUM, ALBUMS, DISCOGRAPHY, LOOSE, TRACKS
from refrain.ui.controllers import STATUS_MS
from refrain.ui.tools.tool_cards import CLEANUP, RENAME, REPLAYGAIN, TIDY, ToolCards

TOOL_NAMES = {RENAME: "Rename", REPLAYGAIN: "ReplayGain", TIDY: "Tidy", CLEANUP: "Clean up"}
TOOL_ENTRIES = {RENAME: menu.RENAME, TIDY: menu.TIDY, CLEANUP: menu.CLEANUP}  # ReplayGain: a list of them
REFUSALS = {  # a selection the tool's entry doesn't take: what to select instead
    RENAME: "select folders or music files, or nothing for the open folder",
    REPLAYGAIN: "select folders, or tracks it measures, or nothing for the open folder",
    TIDY: "it works on folders: select folders, or nothing for the open folder",
    CLEANUP: "it works on folders: select folders, or nothing for the open folder",
}

# The right-click menu's ReplayGain entries (SPEC §31) and the job each runs.
ENTRY_KINDS = {menu.REPLAYGAIN_ALBUM: ALBUM, menu.REPLAYGAIN_DISCOGRAPHY: DISCOGRAPHY,
               menu.REPLAYGAIN_ALBUMS: ALBUMS, menu.REPLAYGAIN_TRACKS: TRACKS, menu.REPLAYGAIN_LOOSE: LOOSE}


def entry_title(kind: str, targets: list[Path]) -> str:
    """The job page's heading for a right-click run, as its menu entry reads."""
    count = len(targets)
    if kind == ALBUMS and count > 1:
        return f"ReplayGain · {count} folders, each as an album"
    if kind == TRACKS:
        return f"ReplayGain · {plural(count, 'track')} as an album"
    if kind == LOOSE:
        return "ReplayGain · this track" if count == 1 else f"ReplayGain · each of {count} tracks on its own"
    return f"ReplayGain · {'Discography' if kind == DISCOGRAPHY else 'This album'}"


def rename_target_text(targets: list[Path]) -> str:
    """The rename preview's heading for a right-click Rename: what was clicked."""
    if len(targets) == 1:
        return str(targets[0])
    folders = sum(1 for path in targets if path.is_dir())
    parts = [plural(count, noun) for count, noun in ((folders, "folder"), (len(targets) - folders, "file")) if count]
    return f"{' and '.join(parts)} in {targets[0].parent}"


def entry_problem(kind: str, targets: list[Path]) -> str | None:
    """Why a right-click run can't start whatever the options: nothing clicked, or (as an album) tracks of several
    folders, which the menu doesn't offer but a run from the command line could ask for."""
    if not targets:
        return "nothing was clicked"
    if kind == TRACKS and len({path.parent for path in targets}) > 1:
        return "the tracks are in several folders, and an album is one folder's tracks"
    return None


class ToolsController(QObject):
    def __init__(self, window, parent: QObject | None = None, cards: ToolCards | None = None) -> None:
        super().__init__(parent)
        self.window = window
        # The options, in dialogs (SPEC §30); `cards`: the process's, which app.Starter shares with the entries.
        self.cards = cards or ToolCards(window.settings, window)
        if cards is not None:
            cards.adopt(window)
        window.tag_io.renameFields.connect(self._tool_example)
        self._example_token: int | None = None

    # -- the target: what a right-click would act on --

    def target(self) -> tuple[Selection, list[str]] | None:
        """The file list's selection, else the open folder as empty space; None without an open folder."""
        window = self.window
        if not window._library_open():
            return None
        ids = window.file_view.selected_ids()
        background = not ids
        if background:
            if window.folder_id is None:
                return None
            ids = [window.folder_id]
        paths, folders = window.menus.entry_paths(ids)
        if not paths:
            return None
        return Selection.of(paths, background, is_dir=folders.__contains__), paths

    def entries(self, tool: str) -> tuple[list[MenuEntry], list[str]]:
        """The tool's entries for the target (ReplayGain: all of its own; the others: theirs, if the selection
        takes it), and the target's paths."""
        found = self.target()
        if found is None:
            return [], []
        selection, paths = found
        items = menu_entries(selection, self.cards.replaygain_card.options().target)
        wanted = menu.REPLAYGAIN_ENTRIES if tool == REPLAYGAIN else (TOOL_ENTRIES[tool],)
        return [item for item in items if item.entry in wanted], paths

    def why_not(self, tool: str, paths: list[str]) -> str:
        return f"{TOOL_NAMES[tool]}: {REFUSALS[tool] if paths else 'open a folder first'}."

    def hover_text(self, tool: str) -> str:
        """The button's hover text, read as the mouse comes over it: the entry it starts and on what, or why it
        can't start."""
        items, paths = self.entries(tool)
        if not items:
            return self.why_not(tool, paths)
        where = (Path(paths[0]).name or paths[0]) if len(paths) == 1 else f"{len(paths)} selected items"
        if tool == REPLAYGAIN:
            return f"ReplayGain on {where}: choose how"
        return f"{items[0].label} ({where}). {items[0].help}."

    def start(self, tool: str) -> None:
        """Tidy, Rename or Clean up: its entry on the target, in its own window, as from the right-click menu."""
        items, paths = self.entries(tool)
        if not items:
            self.window.statusBar().showMessage(self.why_not(tool, paths), STATUS_MS)
            return
        self.window.entryRequested.emit(items[0].entry, paths)

    def fill_replaygain_menu(self, replaygain: QMenu) -> None:
        """The ReplayGain button's list, as it opens: the right-click menu's ReplayGain entries for the target
        (Hugo, 2026-10-25), or why there are none."""
        replaygain.clear()
        items, paths = self.entries(REPLAYGAIN)
        for item in items:
            action = replaygain.addAction(item.label,
                                          lambda entry=item.entry: self.window.entryRequested.emit(entry, paths))
            action.setStatusTip(item.help)
        if not items:
            replaygain.addAction(self.why_not(REPLAYGAIN, paths)).setEnabled(False)

    # -- the Options window --

    def open_options(self, which: str | None = None) -> None:
        """The Options window, on `which`'s page. File names gets its live example from the first track of the
        target, whichever page it opens on (its page is a click away)."""
        found = self.target()
        self.open_options_on(found[1] if found else [], which)

    def open_options_on(self, paths: list[str], which: str | None = None) -> None:
        """The Options window, its File names example from these items' first track (the right-click Settings
        entry's too, SPEC §31); the main window stays where it is."""
        self.cards.set_example(None, "")
        self._example_token = self.window.tag_io.read_first_track_fields(paths) if paths else None
        self.cards.open_options(which)

    def _tool_example(self, token: int, fields: dict | None, extension: str) -> None:
        if token == self._example_token:
            self.cards.set_example(fields, extension)
