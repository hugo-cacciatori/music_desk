"""The theme (SPEC §36 task 2): the one place the app's look comes from, following Windows' light, dark and accent
colours. Also a desktop guard: no window or page sets a style sheet of its own."""

import re
from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit

from music_desk.ui import theme

SRC = Path(__file__).resolve().parents[2] / "src" / "music_desk"


def test_no_style_sheet_outside_the_theme():
    found = [str(path.relative_to(SRC)) for path in SRC.rglob("*.py")
             if path.name != "theme.py" and re.search(r"\.setStyleSheet\(", path.read_text(encoding="utf-8"))]
    assert found == []


def test_the_app_has_the_style_sheet():
    assert QApplication.instance().styleSheet() == theme.style_sheet()


def test_danger_and_invalid_follow_light_and_dark(monkeypatch):
    for dark in (False, True):
        monkeypatch.setattr(theme, "is_dark", lambda dark=dark: dark)
        sheet = theme.style_sheet()
        assert f"color: {theme.DANGER[dark].name()}" in sheet
        assert f"background: {theme.INVALID_BACKGROUND[dark].name()}" in sheet


def test_states_are_properties_the_style_sheet_matches(qtbot):
    label, edit = QLabel(), QLineEdit()
    qtbot.addWidget(label)
    qtbot.addWidget(edit)
    theme.mark_danger(label)
    assert label.property("tone") == "danger"
    theme.mark_danger(label, False)
    assert label.property("tone") is None
    theme.set_invalid(edit, True)
    assert theme.is_invalid(edit)
    theme.set_invalid(edit, False)
    assert not theme.is_invalid(edit)


def test_a_new_accent_colour_rebuilds_the_style_sheet(qtbot, qapp):
    """Windows' accent colour reaches Qt as a palette change: the sheet follows, and `changed` tells the toolbar."""
    current = theme.current()
    assert current is not None
    before = qapp.palette()
    palette = QPalette(before)
    palette.setColor(QPalette.ColorRole.Accent, QColor("#118833"))
    try:
        with qtbot.waitSignal(current.changed, timeout=2000):
            qapp.setPalette(palette)
        assert "background: #118833;" in qapp.styleSheet()
    finally:
        qapp.setPalette(before)
        current.refresh()


def test_the_main_window_is_layered(window):
    """SPEC §36 task 3: the panels are cards on the window's background, the side panel's tabs plain text."""
    for panel in (window.sidebar, window.center, window.playlist_panel, window.side_panel):
        assert panel.property("card") is True
    assert window.objectName() == "mainWindow" and "QMainWindow#mainWindow" in theme.style_sheet()
    assert window.side_panel.tabs.objectName() == "sidePanelTabs" and window.side_panel.tabs.documentMode()
    assert window.search_field.objectName() == "searchField"
    assert [b.objectName() for b in window.search.mode_buttons.values()] == ["searchMode", "searchMode"]
