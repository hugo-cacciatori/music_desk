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


def missing_hover_texts(root: QWidget, skip: tuple[QWidget, ...] = ()) -> list[str]:
    """The controls under `root` (shown or not) with no hover text, or one that only repeats their label."""
    missing = []
    for widget in root.findChildren(QWidget):
        if not isinstance(widget, CONTROLS) or isinstance(widget, QScrollBar) or qt_internal(widget) or \
                is_exempt(widget) or any(widget is s or s.isAncestorOf(widget) for s in skip):
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


def assert_all_have_hover_texts(root: QWidget, skip: tuple[QWidget, ...] = ()) -> None:
    missing = missing_hover_texts(root, skip)
    assert not missing, "No hover text:\n" + "\n".join(missing)


def window_parts_not_done_yet(window) -> tuple[QWidget, ...]:
    """M15 task 1 covers the main window, the sidebar and the side panel; task 2 covers these."""
    pages = [window.center.widget(i) for i in range(window.center.count())]
    return (*(page for page in pages if page is not window.file_view), window.playlist_panel, window.jobs_panel)


def test_the_main_window_sidebar_and_side_panel(window, service, qtbot):
    skip = window_parts_not_done_yet(window)
    window.open_folder(entry(service, "Artist", "Album"))
    assert_all_have_hover_texts(window, skip)  # a folder's summary
    window.file_view.select_ids([entry(service, "Artist", "Album", "track 1.flac")])
    qtbot.waitUntil(lambda: window.side_panel.metadata.snapshot is not None, timeout=10000)
    window.side_panel.tabs.setCurrentIndex(1)
    assert_all_have_hover_texts(window, skip)  # one file: the Metadata and Lyrics tabs
    window.file_view.select_ids([entry(service, "Artist", "Album", "cover.jpg")])
    assert_all_have_hover_texts(window, skip)  # a file's preview
