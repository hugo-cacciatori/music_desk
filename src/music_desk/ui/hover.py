"""Hover texts (SPEC §24): every button and control says what it does when hovered.

The few controls that explain themselves (the explorer views, the address bar, tag fields, OK and Cancel) are
marked with `exempt`, so the guard test (tests/ui/test_hover_texts.py) knows they go without on purpose.
"""

from PySide6.QtWidgets import QMenu, QWidget

EXEMPT = "hoverExempt"


def exempt(widget: QWidget, why: str) -> None:
    """`widget` and everything inside it need no hover text; `why` is for the reader (and the guard's message)."""
    widget.setProperty(EXEMPT, why)


def is_exempt(widget: QWidget | None) -> bool:
    while widget is not None:
        if widget.property(EXEMPT):
            return True
        widget = widget.parentWidget()
    return False


# Menu entries get no tooltip; the less obvious ones show this in the window's status bar while hovered (Hugo,
# 2026-10-17). Keyed by the entry's text without "&", "…" or its shortcut; self-explanatory entries have none.
MENU_TIPS = {
    "Add folder": "Add a music folder to the library and index it; adding only reads the folder",
    "Play selection": "Play the selected audio files in your default player, as a temporary playlist",
    "Play folder": "Play this folder's tracks in your default player, as a temporary playlist",
    "Play folder and subfolders": "Play the tracks of this folder and of all its subfolders in your default player",
    "Rescan": "Read this folder from the disk again, to show changes made outside MusicDesk",
    "Options": "Every setting of the app, in one window: file names, Rename, ReplayGain, Clean up, Split, Trim, "
               "Browser",
    "Clean up": "List this folder's junk files (and what the Clean up options add) to send to the Recycle Bin; "
                "you choose in the list",
    "Remove ReplayGain tags": "Remove the ReplayGain values and their comment line from these tracks (a folder's "
                              "subfolders included), after asking",
    "Delete": "Send to the Recycle Bin, at once (restore it from there)",
    "Remove from library": "Stop showing and indexing this folder; its files aren't touched",
    "Go to folder": "Show this result in its folder, selected; a folder result opens",
    "Reset columns": "Put back the default columns, their order and widths",
}


def menu_key(text: str) -> str:
    return text.split("\t")[0].replace("&", "").replace("…", "").strip()


def with_status_tips(menu: QMenu) -> QMenu:
    """Gives `menu`'s entries their status-bar tips (SPEC §24) and returns it."""
    for action in menu.actions():
        tip = MENU_TIPS.get(menu_key(action.text()))
        if tip:
            action.setStatusTip(tip)
    return menu
