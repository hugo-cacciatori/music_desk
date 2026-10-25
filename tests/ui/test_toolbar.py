"""The tools toolbar, the option cards and the Options menu (SPEC §30, §10, §21): the cards' options, inline
validation and saved defaults; Start's target; the dialogs; what the toolbar replaced."""

import shutil
from pathlib import Path

import pytest
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QToolBar, QToolButton

from music_desk.core import cleanup, rename, replaygain
from music_desk.core.index.store import path_of
from music_desk.settings import Settings
from music_desk.ui.tools.tool_cards import (
    CLEANUP,
    NAMES,
    RENAME,
    REPLAYGAIN,
    TIDY,
    OptionsWindow,
    ToolCards,
    ToolScope,
)

ALBUM = "Fixture Artist - Fixture Album"
SCOPE = ToolScope(Path("C:/lib/Album"), 1)


@pytest.fixture
def cards(qtbot):
    cards = ToolCards(Settings())
    for card in (cards.names_card, cards.rename_card, cards.replaygain_card, cards.cleanup_card):
        qtbot.addWidget(card)
    return cards


# -- the cards (SPEC §10, §21) --

def test_file_names_card_defaults(cards):
    names = cards.names_card
    assert [names.masks.item(i).text() for i in range(names.masks.count())] == ["{track:02} {title}", "{title}"]
    assert names.replacement.text() == "-" and not names.problems.isVisible()
    assert "Split albums and the rename suggestion" in names.description.text()


def test_the_cards_show_no_run_button(cards):
    """The toolbar starts the tools: the cards in the dialogs only hold options."""
    assert cards.rename_card.run_button.isHidden() and cards.replaygain_card.run_button.isHidden()
    for card in (cards.names_card, cards.rename_card, cards.replaygain_card):
        assert card.title.font().bold() and card.title.font().pointSizeF() > card.description.font().pointSizeF()


def test_rename_card_defaults(cards):
    card = cards.rename_card
    assert card.options() == rename.DEFAULT_OPTIONS and cards.problems(RENAME) == []
    assert card.masks_line.text() == "File names: the masks set on the File names page."
    assert card.recursive.isEnabled()  # a dialog shows every option
    assert not hasattr(card, "junk") and not hasattr(card, "cleanup")  # Clean up's now (SPEC §32)


def test_cleanup_card_defaults_and_validation(cards):
    card = cards.cleanup_card
    assert card.options() == cleanup.DEFAULT_OPTIONS and cards.problems(CLEANUP) == []
    assert card.junk.isChecked() and card.junk_extensions.text() == ".cue .lrc .nfo .sfv .txt"
    assert not (card.pictures.isChecked() or card.videos.isChecked() or card.clutter_folders.isChecked())
    card.junk_extensions.setText(".cue .m3u .jpg")
    card.junk_extensions.textEdited.emit(card.junk_extensions.text())
    assert card.problems.text() == ("• .m3u can't be junk: these are playlists\n"
                                    "• .jpg can't be junk: these are pictures (they have their own box)")
    assert cards.problems(TIDY) == []  # Tidy reads only the picture and video lists
    card.video_extensions.setText(".mkv .mp4")
    card.video_extensions.textEdited.emit(card.video_extensions.text())
    assert "• .mp4 can't be a video: these are music files" in card.problems.text()
    assert cards.problems(TIDY) == ["The Clean up options' picture or video types need fixing."]
    card.reset()
    assert cards.problems(CLEANUP) == [] == cards.problems(TIDY)


def test_validation_is_inline(cards):
    names, card = cards.names_card, cards.rename_card
    names.masks.item(0).setText("{track:02} {bogus}")
    assert names.problems.text() == "• mask '{track:02} {bogus}': unknown field(s) bogus"
    assert card.problems.text() == "• Fix the file names first."
    assert cards.problems(RENAME) == ["Fix the file names first."] == cards.problems(TIDY)
    names.masks.item(0).setText("{track:02} {title}")
    assert cards.problems(RENAME) == [] and cards.problems(TIDY) == []
    card.audio.setText(".flac MP3")
    card.audio.textEdited.emit(card.audio.text())
    assert card.problems.text() == "• Music file types: 'MP3' must be lowercase and start with a dot"
    assert cards.problems(TIDY) == ["The Rename options need fixing."]
    names.replacement.setText("/")
    names.replacement.textEdited.emit("/")
    assert "• The replacement contains a character not allowed in file names" in names.problems.text()
    names.reset()
    card.reset()
    assert cards.problems(RENAME) == [] and card.options() == rename.DEFAULT_OPTIONS


def test_masks_can_be_added_filled_and_removed(cards):
    names = cards.names_card
    names.masks.setCurrentRow(1)
    names.remove_mask.click()
    assert cards.rename_card.options().masks == ("{track:02} {title}",)
    names._add_mask()
    names.insert("{artist}")
    assert cards.rename_card.options().masks == ("{track:02} {title}", "{title} {artist}")
    names.masks.clear()
    names._edited()
    assert names.problems.text() == "• No rename mask" and cards.problems(RENAME)


def test_live_example(cards):
    names = cards.names_card
    assert names.example.text() == ""
    cards.set_example({"track": 3, "title": "Who: Me?"}, ".FLAC")
    assert names.example.text() == "Example: 03 Who- Me-.flac"  # the replacement character, the lowercased ext
    names.masks.item(0).setText("{disc}-{track:02} {title}")  # no disc: the next mask
    assert names.example.text() == "Example: Who- Me-.flac"
    cards.set_example({"genre": "Rock"}, ".mp3")
    assert names.example.text() == "Example: the first file fits no mask and keeps its name."


def test_masks_are_the_shared_setting(qtbot):
    settings = Settings(name_masks=["{album} - {title}"], name_replacement="_")
    cards = ToolCards(settings)
    qtbot.addWidget(cards.names_card)
    assert cards.rename_card.options().masks == ("{album} - {title}",)
    assert cards.rename_card.options().replacement == "_"
    cards.names_card.masks.item(0).setText("{title}")
    assert settings.name_masks == ["{title}"]
    assert "masks" not in settings.rename_options  # saved once, as the shared setting


def test_replaygain_card_defaults_and_validation(cards):
    card = cards.replaygain_card
    assert card.options() == replaygain.DEFAULT_OPTIONS and cards.problems(REPLAYGAIN) == []
    card.subfolders.setText("lite, Bonus")
    card.subfolders.textEdited.emit(card.subfolders.text())
    assert "'Bonus' must be lowercase" in card.problems.text() and cards.problems(REPLAYGAIN)
    assert cards.problems(TIDY) == ["The ReplayGain options need fixing."]
    card.reset()
    assert cards.problems(REPLAYGAIN) == [] == cards.problems(TIDY)


def test_replaygain_card_target_stays_in_rsgains_range(cards):
    card = cards.replaygain_card
    card.target.setValue(-40)
    assert card.options().target == -30.0


def test_replaygain_scope_and_unavailable_rsgain(cards):
    card = cards.replaygain_card
    card.scope_group.buttons()[1].setChecked(True)
    assert card.scope == "discography" and card.scope_note.text().startswith("Discography:")
    cards.set_rsgain_unavailable("rsgain couldn't run, so ReplayGain is unavailable.")
    assert "rsgain couldn't run" in card.scope_note.text()
    assert "rsgain couldn't run" in cards.problems(REPLAYGAIN)[-1] and cards.problems(TIDY)
    cards.set_rsgain_unavailable(None)
    assert cards.problems(REPLAYGAIN) == []


def test_requests_carry_the_options(cards):
    cards.replaygain_card.log_file.setText("rg.log")
    cards.replaygain_card.log_file.textEdited.emit("rg.log")
    selection = ToolScope(SCOPE.folder, 1, (SCOPE.folder / "a.flac",))
    rename_request = cards.request(RENAME, selection)
    rg_request = cards.request(REPLAYGAIN, selection)
    tidy_request = cards.request(TIDY, selection)
    cleanup_request = cards.request(CLEANUP, selection)
    assert rename_request is not None and rg_request is not None and tidy_request is not None
    assert cleanup_request is not None and cleanup_request.cleanup_options is not None
    assert cleanup_request.cleanup_options.protected_names == ("rg.log",)  # the configured log is never junk
    assert cleanup_request.scope.files == ()  # Clean up works on the folder
    assert tidy_request.cleanup_options is not None  # Tidy reads Clean up's picture and video lists
    assert rename_request.scope.files == selection.files  # Rename renames the selected files
    assert rg_request.replaygain_options.log_file == "rg.log" and not rg_request.discography
    assert rg_request.scope.files == () and tidy_request.scope.files == ()  # ReplayGain and Tidy: the folder
    assert tidy_request.rename_options.masks == rename.DEFAULT_OPTIONS.masks


def test_invalid_options_make_no_request(cards):
    cards.names_card.masks.item(0).setText("{nope}")
    assert cards.request(RENAME, SCOPE) is None and cards.request(TIDY, SCOPE) is None
    assert cards.request(REPLAYGAIN, SCOPE) is not None  # ReplayGain doesn't use the masks


def test_options_are_saved_and_restored(qtbot, tmp_path):
    settings = Settings(path=tmp_path / "settings.json")
    cards = ToolCards(settings)
    qtbot.addWidget(cards.names_card)
    cards.rename_card.recursive.setChecked(False)
    cards.cleanup_card.pictures.setChecked(True)
    cards.replaygain_card.target.setValue(-18)
    settings.save()
    again = ToolCards(Settings.load(tmp_path / "settings.json"))
    qtbot.addWidget(again.names_card)
    assert not again.rename_card.options().recursive and again.cleanup_card.options().pictures
    assert again.replaygain_card.options().target == -18.0


# -- in the window --

@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "album" / ALBUM, lib / "Artist" / ALBUM)
    (lib / "Artist" / ALBUM / "notes.txt").write_text("x")
    return lib


def folder_id(service, path) -> int:
    conn = service.conn
    return next(i for (i,) in conn.execute("SELECT id FROM entries WHERE is_dir = 1") if path_of(conn, i) == path)


def bar_texts(bar: QToolBar) -> list[str]:
    texts = []
    for action in bar.actions():
        widget = bar.widgetForAction(action)
        if isinstance(widget, QToolButton) or action.isSeparator() or widget is None:
            texts.append("|" if action.isSeparator() else (widget.text() if widget else action.text()))
    return texts


def test_tidy_comes_first_and_in_evidence(window):
    """SPEC §33 (Hugo, 2026-10-24): Tidy first, larger than the others, filled with Windows' accent colour."""
    tools = window.tools_bar
    tidy = tools.widgetForAction(window.tool_actions[TIDY])
    rename = tools.widgetForAction(window.tool_actions[RENAME])
    assert tools.widgetForAction(tools.actions()[0]) is tidy and tidy.objectName() == "tidyButton"
    assert tidy.iconSize().width() > rename.iconSize().width() and tidy.font().bold()
    accent = window.palette().color(QPalette.ColorRole.Accent).name()
    assert f"background: {accent}" in tidy.styleSheet() and not rename.styleSheet()  # the others as they are


def test_three_rows_search_address_then_the_tools(window):
    search, top, tools = window.search_bar, window.navigation_bar, window.tools_bar
    # The search first, on its own row: its toggle and its field, centred, the field taking most of it (SPEC §33).
    assert [search.widgetForAction(a) for a in search.actions()] == [window.search_row]
    layout = window.search_row.layout()
    assert [layout.itemAt(i).widget() for i in range(layout.count())] == [
        None, window.search.modes, window.search_field, None]
    assert window.toolBarArea(search) == window.toolBarArea(top) and window.toolBarBreak(top)
    assert search.geometry().top() < top.geometry().top() < tools.geometry().top()
    assert window.search_field.height() > window.address_bar.height() // 2
    assert window.search_field.font().pointSizeF() > window.font().pointSizeF()
    # Folders, then the navigation, the address bar and Open in File Explorer (SPEC §33).
    assert top.actions()[0] is window.folders_action and top.actions()[-1] is window.explorer_action
    assert top.widgetForAction(top.actions()[-2]) is window.address_bar
    assert not any(isinstance(top.widgetForAction(a), QToolButton) and top.widgetForAction(a).text() in
                   ("Tools", "Playlist builder", "Advanced search") for a in top.actions())
    assert window.toolBarBreak(tools)
    assert bar_texts(tools) == ["Tidy", "Rename", "ReplayGain", "Clean up", "|", "Playlist", "Advanced search"]
    assert tools.toolButtonStyle().name == "ToolButtonTextBesideIcon"
    for tool in (RENAME, REPLAYGAIN, TIDY, CLEANUP):  # plain buttons: no dropdown (Hugo, 2026-10-22)
        action = window.tool_actions[tool]
        assert not action.icon().isNull() and action.menu() is None
        button = tools.widgetForAction(action)
        assert isinstance(button, QToolButton)
        assert button.popupMode() is not QToolButton.ToolButtonPopupMode.InstantPopup
    assert not window.playlist_action.icon().isNull() and not window.advanced_search_action.icon().isNull()


def test_hover_text_names_the_target(window, service, library_dir):
    album = library_dir / "Artist" / ALBUM
    assert window.tools.hover_text(RENAME) == ("Rename: open a folder first", False)
    window.open_folder(folder_id(service, album))
    window.file_view.select_ids([])
    window.tool_actions[RENAME].hovered.emit()  # the mouse comes over the button
    assert window.tool_actions[RENAME].toolTip() == f"Rename the music files in {ALBUM} and its subfolders"
    assert window.tools.hover_text(REPLAYGAIN) == (f"ReplayGain on {ALBUM}", True)
    assert window.tools.hover_text(TIDY) == (f"Tidy {ALBUM} and its subfolders", True)
    ids = {r.name: r.id for r in window.file_view.model.rows}
    window.file_view.select_ids([ids["track2.flac"], ids["notes.txt"]])
    assert window.tools.hover_text(RENAME) == ("Rename 1 selected file", True)  # the music files only
    assert window.tools.hover_text(REPLAYGAIN) == (f"ReplayGain on {ALBUM}", True)  # a selection's folder
    assert window.tools.target(TIDY).folder == album and not window.tools.target(TIDY).is_selection
    window.tools.cards.names_card.masks.item(0).setText("{nope}")
    why = "fix the File names options first: File > Options…"
    assert window.tools.hover_text(RENAME) == (f"Rename: {why}", False)
    assert window.tools.hover_text(TIDY) == (f"Tidy: {why}", False)
    assert window.tools.hover_text(REPLAYGAIN)[1]  # ReplayGain doesn't use the masks
    window.tool_actions[RENAME].trigger()  # a click that can't start says why and starts nothing
    assert window.statusBar().currentMessage() == f"Rename: {why}."
    assert not window.showing_tools
    window.tools.cards.names_card.reset()


def test_a_folder_selected_in_the_list_is_the_target(window, service, library_dir):
    window.open_folder(folder_id(service, library_dir / "Artist"))
    window.file_view.select_ids([folder_id(service, library_dir / "Artist" / ALBUM)])
    scope = window.tools.target(RENAME)
    assert scope is not None and scope.folder == library_dir / "Artist" / ALBUM and not scope.is_selection
    assert scope.first_track == library_dir / "Artist" / ALBUM / "01 First.flac"  # natural order


def test_start_without_a_folder_says_so(window):
    window.tool_actions[TIDY].trigger()
    assert window.statusBar().currentMessage() == "Tidy: open a folder first."
    assert not window.showing_tools


def test_options_open_the_window_on_the_tools_page(qtbot, window, service, library_dir):
    album = library_dir / "Artist" / ALBUM
    window.open_folder(folder_id(service, album))
    window.file_view.select_ids([])
    window.tools.open_options(REPLAYGAIN)
    dialog = window.tools.cards.options_window
    assert isinstance(dialog, OptionsWindow) and dialog.isVisible() and not dialog.isModal()
    assert dialog.windowTitle() == "Options" and dialog.current == REPLAYGAIN
    assert dialog.stack.currentWidget().isAncestorOf(window.tools.cards.replaygain_card)
    window.tools.cards.replaygain_card.target.setValue(-18)
    assert window.settings.replaygain_options["target"] == -18.0  # saved as it changes
    dialog.close()
    window.tools.open_options(RENAME)
    assert window.tools.cards.options_window is dialog and dialog.isVisible()  # the same window, raised
    assert dialog.current == RENAME
    dialog.close()
    assert not window.showing_tools  # the file list stays


def test_file_options_holds_every_option(qtbot, window, service, library_dir):
    assert [a.text() for a in window.menuBar().actions()] == ["&File", "&Help"]  # no Options menu
    file_menu = window.menuBar().actions()[0].menu()
    # No Add folder… or Rescan library since 0.8.4: the sidebar adds folders (Ctrl+O still works).
    assert [a.text() for a in file_menu.actions() if not a.isSeparator()] == ["&Options…", "E&xit"]
    assert window.add_folder_action in window.actions()
    options = next(a for a in file_menu.actions() if a.text() == "&Options…")
    assert options.statusTip()
    album = library_dir / "Artist" / ALBUM
    window.open_folder(folder_id(service, album))
    window.file_view.select_ids([])
    options.trigger()
    dialog = window.tools.cards.options_window
    assert dialog is not None and dialog.isVisible()
    assert [dialog.list.item(i).text() for i in range(dialog.list.count())] == [
        "File names", "Rename", "ReplayGain", "Clean up", "Split", "Trim", "Browser"]  # every setting (SPEC §12)
    assert dialog.current == NAMES  # first opened on the first page
    dialog.list.setCurrentRow(2)  # a click in the list shows that card
    assert dialog.current == REPLAYGAIN
    assert dialog.stack.currentWidget().isAncestorOf(window.tools.cards.replaygain_card)
    qtbot.waitUntil(lambda: window.tools.cards.names_card.example.text() == "Example: 01 First.flac")
    dialog.close()
    options.trigger()
    assert dialog.current == REPLAYGAIN  # File > Options… reopens where it was
    dialog.close()


def test_no_tools_entries_left(window, service, library_dir):
    artist = folder_id(service, library_dir / "Artist")
    window.open_folder(artist)
    window.side_panel.show_folder(artist)
    assert not hasattr(window.side_panel.summary, "tools_button")
    assert "Tools…" not in [a.text() for a in window.list_menu([]).actions()]


def test_rsgain_unavailable_disables_replaygain(window, service, library_dir):
    window.open_folder(folder_id(service, library_dir / "Artist" / ALBUM))
    window.show_rsgain_version(None)
    assert window.tools.hover_text(REPLAYGAIN) == ("ReplayGain: rsgain couldn't run", False)
    assert window.tools.hover_text(TIDY) == ("Tidy: rsgain couldn't run", False)
    window.show_rsgain_version("rsgain 3.6")
    assert window.tools.hover_text(REPLAYGAIN)[1]


def test_every_page_restores_its_defaults_and_restore_all_asks(qtbot, window):
    """SPEC §12: Restore defaults per page, at once; Restore all defaults asks first (no Recycle Bin for settings)."""
    cards = window.tools.cards
    dialog = cards.open_options()
    for which in dialog.pages:
        assert cards.card(which).reset_button.text() == "Restore defaults", which
    cards.rename_card.recursive.setChecked(False)
    cards.trim_card.set_value("songs")
    cards.browser_card.hide_without_music.setChecked(True)
    cards.cleanup_card.reset_button.click()  # one page: only its own settings
    assert window.settings.trim_subfolder == "songs"
    asked = []
    dialog.confirm_restore_all = lambda: asked.append(True) or False  # Cancel: nothing changes
    dialog.restore_all.click()
    assert asked and window.settings.trim_subfolder == "songs" and not cards.rename_card.options().recursive
    dialog.confirm_restore_all = lambda: True
    dialog.restore_all.click()
    assert window.settings.trim_subfolder == "lite" and cards.rename_card.options().recursive
    assert not window.settings.hide_folders_without_music and not window.settings.save_cue_beside
    assert window.settings.rename_options == rename.options_to_json(rename.DEFAULT_OPTIONS)
    dialog.close()


def test_the_cards_go_with_the_window(qtbot, service):
    """A closed window takes its option cards with it (before 2026-10-23 they stayed behind, and the hundreds a
    test run left crashed Python at exit, SPEC §18), and they stay hidden until Options… shows them."""
    import shiboken6
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    from music_desk.ui.main_window import MainWindow
    from music_desk.ui.tools.tool_cards import PAGE_TITLES

    window = MainWindow(service)
    window.show()
    cards = [window.tools.cards.card(which) for which in PAGE_TITLES]  # Options… never opened
    assert not any(card.isVisible() for card in cards)
    window.close()
    window.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not any(shiboken6.isValid(card) for card in cards)
