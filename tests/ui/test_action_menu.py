"""The Action menu (SPEC §5): actions over the whole library, as ready-made searches."""

from pathlib import Path

import pytest
from PySide6.QtWidgets import QMenu
from test_library_search import search

from music_desk.core.formats import AUDIO_EXTENSIONS
from music_desk.core.metadata import RG_KEYS
from music_desk.core.tags import TaggedFile
from music_desk.ui.main_window import ACTION_QUERIES


def menu_titles(window) -> list[str]:
    return [action.text() for action in window.menuBar().actions()]


def action_menu(window) -> QMenu:
    return next(m for m in window.menuBar().findChildren(QMenu) if m.title() == "&Action")


def without_replaygain(library_dir: Path) -> set[Path]:
    """Read from the files themselves, not the index: the readable audio files with no ReplayGain tag."""
    found = set()
    for path in library_dir.rglob("*"):
        if path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        try:
            tagged = TaggedFile(path)
            if all(tagged.get_replaygain(key) is None for key in RG_KEYS):
                found.add(path)
        except Exception:  # noqa: BLE001 - an unreadable fixture: its tags can't be read
            continue
    return found


def test_the_menu_sits_before_help(window):
    assert menu_titles(window) == ["&File", "&View", "&Action", "&Help"]  # no Lyrics menu since M12 (SPEC §26)
    assert [a.text() for a in action_menu(window).actions()] == [text for text, _query in ACTION_QUERIES]
    assert action_menu(window).isEnabled()


@pytest.mark.parametrize("text, query", ACTION_QUERIES)
def test_show_tracks_without_replaygain_tags(qtbot, window, library_dir, text, query):
    assert query == "rg:none"
    next(a for a in action_menu(window).actions() if a.text() == text).trigger()
    search(qtbot, window)
    assert window.showing_search and window.search_field.text() == "rg:none"
    view = window.search_view
    shown = {Path(row.folder, row.name) for row in view.model.rows}
    expected = without_replaygain(library_dir)
    assert expected and shown == expected
    assert all(not row.is_dir for row in view.model.rows)


def test_disabled_without_a_library(qtbot):
    from music_desk.ui.main_window import MainWindow

    window = MainWindow(None)
    qtbot.addWidget(window)
    assert not action_menu(window).isEnabled()
