"""Helpers for the right-click entries' windows (SPEC §31, §35)."""

from PySide6.QtWidgets import QWidget


def entry_window(qtbot, starter, entry: str, paths) -> QWidget:
    """Start a right-click entry on these paths and return the window it opened."""
    before = list(starter.entry_windows)
    starter.run_entry(entry, [str(p) for p in paths])
    [opened] = [w for w in starter.entry_windows if w not in before]
    if isinstance(opened, QWidget):
        qtbot.addWidget(opened)
    return opened
