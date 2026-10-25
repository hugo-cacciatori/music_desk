"""Library search in the window (SPEC §6): Ctrl+K, the debounced query, the File / Lyrics toggle, the count,
filters, folder rows, highlights and snippets, going to a result, Esc, the side panel as the detail pane, and Play
results."""

import threading

import pytest
from browser_helpers import entry
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton, QCheckBox

from music_desk.core.index.lyrics_index import record_lyrics
from music_desk.core.index.search import FILES, LYRICS
from music_desk.settings import Settings
from music_desk.ui.main_window import MainWindow
from music_desk.ui.search_view import CARD_COLUMN, SEARCH_COLUMN_INDEX, SEARCH_COLUMNS, line_spans
from music_desk.ui.side_panel.lyrics_tab import FOUND_STATE

ALBUM = "Fixture Artist - Fixture Album"


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.lock = threading.Lock()

    def __call__(self, *args) -> None:
        with self.lock:
            self.calls.append(tuple(str(a) for a in args))


def put_lyrics(service, title: str, plain: str) -> None:
    key = f"fixture artist\x1f{title}".casefold()
    entry_ = {"status": "found", "source": "lrclib", "lrclib_id": 1, "plain": plain, "checked": "2026-10-15"}
    service.writer.submit(lambda conn: record_lyrics(conn, key, entry_)).result()


def search(qtbot, window, text: str | None = None) -> None:
    """Type a query (or re-run the current one) and wait for its results on screen."""
    if not window.showing_search:
        window.search.open()
    if text is not None:
        window.search_field.clear()
        QTest.keyClicks(window.search_field, text)
    # The results on screen are those of the latest request, and that request is the field's text: never the empty
    # query that opening the page runs while the typed one is still debounced.
    qtbot.waitUntil(lambda: window.search._token is not None and window.search.shown == window.search._token
                    and not window.search._timer.isActive()
                    and window.search._request == window.search_field.text(), timeout=10000)


def names(window) -> list[str]:
    return window.search_view.column_texts("name")


def shown(window) -> str:
    """What the page says besides the rows, for failure messages: an empty result may be an error note."""
    view = window.search_view
    return f"count {view.count_label.text()!r}, note {view.note.text()!r}, request {window.search._request!r}"


@pytest.fixture
def lyrics(service):
    put_lyrics(service, "First", "Hello darkness, my old friend\nI've come to talk with you again")
    put_lyrics(service, "Sixth", "Love will tear us apart")


def test_ctrl_k_opens_the_search_with_a_prompt(qtbot, window):
    window.activateWindow()
    QTest.keyClick(window, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    assert window.showing_search and not window.showing_tools
    qtbot.waitUntil(lambda: window.search_field.hasFocus())
    note = window.search_view.note
    assert note.isVisibleTo(window.search_view) and "Type to search" in note.text()
    assert window.search_view.model.rowCount() == 0
    assert window.side_panel.tabs.currentWidget() is window.side_panel.lyrics  # the detail pane: lyrics


def test_typing_runs_after_a_pause_and_counts(qtbot, window):
    window.search.open()
    QTest.keyClicks(window.search_field, "fir")
    assert window.search._timer.isActive() and window.search._request == ""  # debounced: nothing runs on each key
    search(qtbot, window)
    assert names(window) == ["01 First.flac", "clash.flac", "01 First.flac"], shown(window)
    assert window.search_view.count_label.text() == "3 of 3 results"
    assert window.search_view.column_texts("folder")[0].endswith(ALBUM)
    assert window.search_view.play_button.isEnabled()


def test_substring_and_lyrics_snippet(qtbot, window, service, lyrics):
    search(qtbot, window, "arknes")
    assert names(window) == []  # File search: not the lyrics
    window.search.set_mode(LYRICS)
    search(qtbot, window)
    first = entry(service, "Other", ALBUM, "01 First.flac")
    assert set(names(window)) == {"01 First.flac", "clash.flac"}
    assert window.search_view.column_texts("snippet")[0] == "Hello darkness, my old friend"
    assert window.search_view.highlights(first, "snippet") == ((7, 13),)
    window.search.set_mode(FILES)
    search(qtbot, window, "TONE")
    assert window.search_view.column_texts("snippet") == [""] * 4  # File search: no snippet
    tone = entry(service, "Artist", "Album", "Track 2.flac")
    assert window.search_view.highlights(tone, "title") == ((0, 4), (5, 9))


def test_the_toggle_runs_the_query_again_and_is_kept(qtbot, window, lyrics):
    header = window.search_view.view.header()
    assert window.search.mode == FILES and window.search.mode_buttons[FILES].isChecked()
    assert header.isSectionHidden(SEARCH_COLUMN_INDEX["snippet"])  # the Lyrics column: Lyrics search only
    search(qtbot, window, "first")
    assert len(names(window)) == 3
    before = window.search.shown
    window.search.mode_buttons[LYRICS].click()
    assert window.settings.search_mode == LYRICS and window.search_field.hasFocus()
    qtbot.waitUntil(lambda: window.search.shown not in (None, before), timeout=10000)
    assert names(window) == [], shown(window)  # no lyrics say "first"
    assert not header.isSectionHidden(SEARCH_COLUMN_INDEX["snippet"]) and window.search_view.showing_cards
    assert "Search lyrics" in window.search_field.placeholderText()
    search(qtbot, window, "tear us")
    assert names(window) == ["track6.FLAC"], shown(window)


def test_the_last_mode_and_the_focus_at_startup(qtbot, service):
    window = MainWindow(service, Settings(search_mode=LYRICS))
    qtbot.addWidget(window)
    window.show()
    assert window.search.mode == LYRICS and window.search.mode_buttons[LYRICS].isChecked()
    window.activateWindow()
    qtbot.waitUntil(lambda: window.search_field.hasFocus())  # the app opens on its search
    other = MainWindow(service, Settings(search_mode="something else"))
    qtbot.addWidget(other)
    assert other.search.mode == FILES


def test_cap_and_total(qtbot, window):
    window.search.limit = 2
    search(qtbot, window, "e")
    total = window.search_view.count_label.text()
    assert window.search_view.model.rowCount() == 2 and total.startswith("2 of ")


def test_no_chips_and_no_scope_box(qtbot, window):
    search(qtbot, window, "fixture")
    buttons = [b.text() for b in window.search_view.findChildren(QAbstractButton)]
    assert buttons == ["Play results"]  # no All / With lyrics / Missing
    assert not window.search_view.findChildren(QCheckBox)  # no "Only in <folder> and below"


def test_filters_and_errors(qtbot, window):
    search(qtbot, window, "ext:mp3")
    assert sorted(names(window)) == ["Track 3.mp3", "lowercase_rg.mp3", "no_track.mp3"]
    search(qtbot, window, "lrclib:sometimes first")
    assert "lrclib:sometimes" in window.search_view.note.text()
    assert len(names(window)) == 3  # the bad filter is ignored, the rest runs


def test_a_folder_result(qtbot, window, service, tmp_path):
    album = entry(service, "Other", ALBUM)
    search(qtbot, window, "fixture album")
    assert names(window)[0] == ALBUM
    view = window.search_view
    row = view.row(album)
    assert row.is_dir and view.column_texts("folder")[0].endswith("Other")
    assert [view.column_texts(key)[0] for key in ("title", "artist", "album", "track", "rg")] == [""] * 5
    view.select_ids([album])
    panel = window.side_panel
    assert panel.stack.currentWidget() is panel.summary and panel.summary.summary.id == album  # its summary

    recorder = Recorder()
    window.launch = recorder
    window.playlist_dir = tmp_path / "playlists"
    view.play_button.click()  # the audio files among the results, not the folder
    qtbot.waitUntil(lambda: len(recorder.calls) == 1, timeout=10000)
    text = next((tmp_path / "playlists").glob("play-*.m3u8")).read_text(encoding="utf-8")
    paths = [line for line in text.splitlines() if line and not line.startswith("#")]
    assert len(paths) == len(names(window)) - 1 and all(p.endswith((".flac", ".mp3", ".FLAC")) for p in paths)

    view.select_ids([album])
    view.focus_results()
    QTest.keyClick(view.view, Qt.Key.Key_Return)  # opens the folder itself
    assert not window.showing_search and window.folder_id == album


def test_artist_filter_from_a_result(qtbot, window, service):
    search(qtbot, window, "first")
    window.search.add_artist_filter("Fixture Artist")
    search(qtbot, window)
    assert window.search_field.text() == 'first artist:"Fixture Artist"'
    assert len(names(window)) == 3
    window.search.add_artist_filter("Fixture Artist")  # once only
    assert window.search_field.text().count("artist:") == 1


def test_enter_goes_to_the_file_and_esc_returns(qtbot, window, service):
    album = entry(service, "Artist", "Album")
    window.open_folder(album)
    search(qtbot, window, "lowercase")
    target = entry(service, "Other", ALBUM, "lowercase_rg.mp3")
    window.search_view.focus_results()
    QTest.keyClick(window.search_view.view, Qt.Key.Key_Return)
    assert not window.showing_search
    assert window.folder_id == entry(service, "Other", ALBUM)
    assert window.file_view.selected_ids() == [target]

    window.open_folder(album)
    search(qtbot, window, "first")
    window.activateWindow()  # Esc is a shortcut of the field: it needs the focus, in the active window
    window.search_field.setFocus()
    qtbot.waitUntil(lambda: window.search_field.hasFocus())
    QTest.keyClick(window.search_field, Qt.Key.Key_Escape)
    assert not window.showing_search and window.folder_id == album
    window.search.open()
    assert window.search_field.text() == "first"  # the query is kept for the next Ctrl+K


def test_enter_runs_the_search_again_from_anywhere(qtbot, window, service):
    window.search.set_mode(LYRICS)
    search(qtbot, window, "blue monday")
    assert names(window) == []
    window.search.close()
    window.open_folder(entry(service, "Artist", "Album"))
    put_lyrics(service, "Second", "How does it feel... blue monday")  # indexed while the page wasn't shown
    before = window.search.shown
    window.activateWindow()
    window.search_field.setFocus()
    qtbot.waitUntil(lambda: window.search_field.hasFocus())
    QTest.keyClick(window.search_field, Qt.Key.Key_Return)
    assert window.showing_search
    search(qtbot, window)
    assert window.search.shown != before
    assert sorted(names(window)) == ["02 Second.flac", "track2.flac"], shown(window)
    qtbot.waitUntil(lambda: window.search_view.view.hasFocus() and window.search_view.view.currentIndex().row() == 0)

    put_lyrics(service, "First", "blue monday again")  # Enter on the page shown: the query runs again too
    QTest.keyClick(window.search_field, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: len(names(window)) > 2, timeout=10000)
    assert "01 First.flac" in names(window), shown(window)


def test_selecting_a_result_shows_its_lyrics(qtbot, window, service, lyrics):
    window.search.set_mode(LYRICS)
    search(qtbot, window, "tear us")
    sixth = entry(service, "Other", ALBUM, "track6.FLAC")
    window.search_view.select_ids([sixth])
    tab = window.side_panel.lyrics
    qtbot.waitUntil(lambda: tab.state == FOUND_STATE, timeout=10000)
    assert "Love will tear us apart" in tab.text.toPlainText()
    metadata = window.side_panel.metadata
    qtbot.waitUntil(lambda: metadata.name_label.text() == "track6.FLAC", timeout=10000)


def test_unsaved_edits_keep_the_result(qtbot, window, service, monkeypatch):
    search(qtbot, window, "second")
    first, second = (entry(service, "Other", ALBUM, "track2.flac"),
                     entry(service, "Other", ALBUM, "lite", "02 Second.flac"))
    window.search_view.select_ids([first])
    tab = window.side_panel.metadata
    qtbot.waitUntil(lambda: tab.name_label.text() == "track2.flac" and tab.snapshot is not None, timeout=10000)
    monkeypatch.setattr(tab, "ask_leave", lambda: "cancel")
    tab.edits["title"].setText("Edited")
    tab.edits["title"].textEdited.emit("Edited")
    window.search_view.select_ids([second])
    assert window.search_view.selected_ids() == [first] and tab.dirty
    window.search.close()
    assert window.showing_search  # Cancel: the search stays
    window.search.go_to_result(first)
    assert window.showing_search


def test_play_results_in_view_order(qtbot, window, service, tmp_path):
    recorder = Recorder()
    window.launch = recorder
    window.playlist_dir = tmp_path / "playlists"
    search(qtbot, window, "tone")
    window.search_view.play_button.click()
    qtbot.waitUntil(lambda: len(recorder.calls) == 1, timeout=10000)
    text = (tmp_path / "playlists").glob("play-*.m3u8").__next__().read_text(encoding="utf-8")
    paths = [line for line in text.splitlines() if line and not line.startswith("#")]
    assert [p.rsplit("\\", 1)[1] for p in paths] == ["track 1.flac", "Track 2.flac", "Track 3.mp3", "Track 10.flac"]


def test_results_follow_new_lyrics(qtbot, window, service):
    window.search.set_mode(LYRICS)
    search(qtbot, window, "blue monday")
    assert names(window) == []
    put_lyrics(service, "Second", "How does it feel... blue monday")
    window.lyrics_updated([entry(service, "Other", ALBUM)])
    qtbot.waitUntil(lambda: sorted(names(window)) == ["02 Second.flac", "track2.flac"], timeout=10000)


def test_lyrics_search_shows_cards(qtbot, window, service, lyrics):
    """Direction task 6 (SPEC §6, §33): the Lyrics column alone, without the header, painted as cards; File search's
    table comes back with its columns and widths."""
    view = window.search_view
    header = view.view.header()
    header.resizeSection(SEARCH_COLUMN_INDEX["title"], 333)
    window.search.set_mode(LYRICS)
    search(qtbot, window, "darkness")
    assert view.showing_cards and header.isHidden()
    assert [i for i in range(len(SEARCH_COLUMNS)) if not header.isSectionHidden(i)] == [CARD_COLUMN]
    first = entry(service, "Other", ALBUM, "01 First.flac")
    row = view.row(first)
    assert row is not None and row.extract == "Hello darkness, my old friend\nI've come to talk with you again"
    assert line_spans(row.extract, row.extract_spans) == [("Hello darkness, my old friend", ((6, 14),)),
                                                          ("I've come to talk with you again", ())]
    height = view.view.visualRect(view.model.index(view.model.row_for(first), CARD_COLUMN)).height()
    assert height > 3 * view.view.fontMetrics().height()  # a card: title, artist and album, the extract
    window.search.set_mode(FILES)
    assert not view.showing_cards and header.isVisibleTo(view)
    assert header.isSectionHidden(CARD_COLUMN) and header.sectionSize(SEARCH_COLUMN_INDEX["title"]) == 333


def test_a_click_on_a_card_shows_its_lyrics(qtbot, window, service, lyrics):
    window.search.set_mode(LYRICS)
    search(qtbot, window, "darkness")
    panel = window.side_panel
    panel.tabs.setCurrentWidget(panel.metadata)
    first = entry(service, "Other", ALBUM, "01 First.flac")
    view = window.search_view.view
    rect = view.visualRect(window.search_view.model.index(window.search_view.model.row_for(first), CARD_COLUMN))
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert window.search_view.selected_ids() == [first] and panel.tabs.currentWidget() is panel.lyrics
    qtbot.waitUntil(lambda: "Hello darkness" in panel.lyrics.shown_text(), timeout=5000)
    texts = [a.text() for a in window.search.result_menu([first]).actions() if a.text()]
    assert texts[:2] == ["Go to folder", "Open in File Explorer"]  # a card's ways to its folder come first
    assert texts[2] == "ReplayGain this track (−15 LUFS)"  # then Explorer's entries (SPEC §35)


def test_result_menu(qtbot, window, service):
    search(qtbot, window, "sixth")
    sixth = entry(service, "Other", ALBUM, "track6.FLAC")
    texts = [a.text() for a in window.search.result_menu([sixth]).actions() if a.text()]
    # Explorer's entries first (SPEC §35), then the results' own.
    assert texts == ["ReplayGain this track (−15 LUFS)", "Rename from tags…", "Remove ReplayGain tags…",
                     "Trim this track…", "Go to folder", "Open", "Play selection", "Open in File Explorer",
                     "Copy path", "Delete\tDel", "Filter by Fixture Artist"]


def test_sorting_and_live_updates_keep_the_selection(qtbot, window, service):

    search(qtbot, window, "fixture artist")
    view = window.search_view
    view.view.sortByColumn(SEARCH_COLUMN_INDEX["title"], Qt.SortOrder.DescendingOrder)
    titles = view.column_texts("title")
    assert titles == sorted(titles, key=lambda t: (t == "", t.casefold()), reverse=True)  # as the file list
    sixth = entry(service, "Other", ALBUM, "track6.FLAC")
    view.select_ids([sixth])
    put_lyrics(service, "Sixth", "a new text")
    window.lyrics_updated([entry(service, "Other", ALBUM)])
    qtbot.waitUntil(lambda: view.row(sixth) is not None and view.row(sixth).lrclib == "plain", timeout=10000)
    assert view.selected_ids() == [sixth]
    assert view.column_texts("title") == titles  # still sorted by title
    view.view.sortByColumn(SEARCH_COLUMN_INDEX["folder"], Qt.SortOrder.AscendingOrder)
    assert view.selected_ids() == [sixth]
