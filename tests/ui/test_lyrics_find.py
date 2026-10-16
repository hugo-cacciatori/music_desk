"""The Lyrics menu's Find entries (SPEC §5): ready-made library searches for a lyrics action."""

import shutil

import pytest
from PySide6.QtWidgets import QMenu
from test_library_search import names, put_lyrics, search

from music_desk.core.index.lyrics_index import record_lyrics
from music_desk.ui.main_window import FIND_QUERIES


@pytest.fixture
def library_dir(library_dir, fixtures_dir):
    loose = library_dir / "Loose"
    loose.mkdir()
    shutil.copy2(fixtures_dir / "formats" / "tone.aac", loose / "untitled.aac")  # no artist or title at all
    return library_dir


def menu_action(window, text):
    menu = next(m for m in window.menuBar().findChildren(QMenu) if m.title() == "&Lyrics")
    return next(a for a in menu.actions() if a.text() == text)


@pytest.mark.parametrize("text, query", FIND_QUERIES)
def test_each_entry_opens_its_search(qtbot, window, service, text, query):
    put_lyrics(service, "First", "found words")
    key = "fixture artist\x1fsixth"
    service.writer.submit(lambda conn: record_lyrics(conn, key, {"status": "not_found",
                                                                 "checked": "2026-10-15"})).result()
    menu_action(window, text).trigger()
    search(qtbot, window)
    assert window.showing_search and window.search_field.text() == query
    shown = set(names(window))
    if query == "lrclib:notfound":
        assert shown == {"track6.FLAC"}
    elif query == "lrclib:notags":
        assert shown == {"untitled.aac"}
    else:  # without lyrics in their tags: every track here (the fixture files carry none)
        assert "01 First.flac" in shown and "untitled.aac" in shown
