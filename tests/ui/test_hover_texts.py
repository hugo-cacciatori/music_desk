"""SPEC §24: every button and control has a hover text, except those marked as explaining themselves."""

import re

from browser_helpers import entry
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSlider,
    QAbstractSpinBox,
    QComboBox,
    QDialogButtonBox,
    QLineEdit,
    QScrollBar,
    QTabBar,
    QWidget,
)

from music_desk.ui.hover import is_exempt

CONTROLS = (QAbstractButton, QComboBox, QAbstractSpinBox, QAbstractSlider, QLineEdit)


def plain(text: str) -> str:
    return re.sub(r"&(?!&)|…|\.\.\.|\t.*", "", text).strip().lower()


def qt_internal(widget: QWidget) -> bool:
    """Parts Qt builds itself: a tab bar's scroll arrows, a dock's title buttons, a toolbar's overflow arrow, the
    text field inside a spin box or an editable combo box, a text field's clear and action icons (an action's
    icon shows the action's tooltip), and a dialog's standard buttons (OK, Cancel…)."""
    parent = widget.parentWidget()
    if widget.objectName().startswith("qt_") or \
            isinstance(parent, (QTabBar, QAbstractSpinBox, QComboBox, QLineEdit)):
        return True
    return isinstance(parent, QDialogButtonBox) and isinstance(widget, QAbstractButton) and \
        parent.standardButton(widget) != QDialogButtonBox.StandardButton.NoButton


def missing_hover_texts(root: QWidget) -> list[str]:
    """The controls under `root` (shown or not) with no hover text, or one that only repeats their label."""
    missing = []
    for widget in root.findChildren(QWidget):
        if not isinstance(widget, CONTROLS) or isinstance(widget, QScrollBar) or qt_internal(widget) or \
                is_exempt(widget):
            continue
        if isinstance(widget, QAbstractButton) and widget.isHidden() and not widget.text() and \
                widget.icon().isNull():
            continue  # an empty placeholder (the lyrics status button) gets its text and tip when it is shown
        tip = widget.toolTip().strip()
        label = plain(widget.text()) if isinstance(widget, (QAbstractButton, QLineEdit)) else ""
        if not tip or (label and plain(tip) == label):
            name = widget.objectName() or label or widget.property("placeholderText") or ""
            missing.append(f"{type(widget).__name__} {name!r} in {type(widget.parentWidget()).__name__}")
    return missing


def assert_all_have_hover_texts(root: QWidget) -> None:
    missing = missing_hover_texts(root)
    assert not missing, "No hover text:\n" + "\n".join(missing)


def test_the_side_panel_in_each_state(window, service, qtbot):
    window.open_folder(entry(service, "Artist", "Album"))
    assert_all_have_hover_texts(window)  # a folder's summary
    window.file_view.select_ids([entry(service, "Artist", "Album", "track 1.flac")])
    qtbot.waitUntil(lambda: window.side_panel.metadata.snapshot is not None, timeout=10000)
    window.side_panel.tabs.setCurrentIndex(1)
    assert_all_have_hover_texts(window)  # one file: the Metadata and Lyrics tabs
    window.file_view.select_ids([entry(service, "Artist", "Album", "cover.jpg")])
    assert_all_have_hover_texts(window)  # a file's preview


def test_every_page_and_panel(window):
    """The centre pages (tools, previews, reports, split, trim, search), the playlist builder and the jobs panel
    are built with the window, shown or not."""
    assert_all_have_hover_texts(window)


def test_the_other_windows_and_dialogs(window, qtbot):
    from music_desk.ui.about_dialog import AboutDialog
    from music_desk.ui.track_lengths import TrackLengthsDialog

    window.open_advanced_search()
    assert window.advanced_search is not None
    about = AboutDialog("rsgain 3.6", window, flac_version="flac 1.5.0")
    lengths = TrackLengthsDialog([7350, 7350], 0, 22050, window)
    for widget in (window.advanced_search, about, lengths):
        qtbot.addWidget(widget)
        assert_all_have_hover_texts(widget)


def test_menu_entries_show_a_status_bar_tip(window, service):
    from music_desk.ui.hover import MENU_TIPS, menu_key

    album = entry(service, "Artist", "Album")
    window.open_folder(album)
    menus = [window.list_menu([entry(service, "Artist", "Album", "track 1.flac")]), window.list_menu([]),
             window.sidebar.context_menu_for(window.sidebar.model.index(0, 0)), window.file_view.header_menu(),
             window.playlist_panel.header_menu()]
    menus += [action.menu() for action in window.menuBar().actions()]
    tipped = set()
    for menu in menus:
        for action in menu.actions():
            key = menu_key(action.text())
            assert action.statusTip() == MENU_TIPS.get(key, ""), key
            if action.statusTip():
                tipped.add(key)
    assert {"Play selection", "Play folder", "Options", "Remove from library", "Reset columns"} <= tipped
    play = next(a for a in menus[0].actions() if a.text() == "Play selection")
    play.showStatusText(window)  # what hovering it does
    assert window.statusBar().currentMessage() == MENU_TIPS["Play selection"]
