"""The Tools page and its cards: options, inline validation, saved defaults, scope, and how it opens (SPEC §10)."""

from pathlib import Path

import pytest

from music_desk.core import rename, replaygain
from music_desk.core.index.store import path_of
from music_desk.settings import Settings
from music_desk.ui.tools.tools_page import RENAME, REPLAYGAIN, TIDY, ToolScope, ToolsPage

ALBUM = "Fixture Artist - Fixture Album"


@pytest.fixture
def page(qtbot):
    page = ToolsPage(Settings())
    qtbot.addWidget(page)
    page.set_scope(ToolScope(Path("C:/lib/Album"), 1))
    page.show()
    return page


def requests(page) -> list:
    received = []
    page.runRequested.connect(received.append)
    return received


# -- the File names card (SPEC §21) and the Rename card --

def test_file_names_card_comes_first_with_the_defaults(page):
    names = page.names_card
    assert names.y() < page.rename_card.y() < page.replaygain_card.y()  # first, above the tool cards
    assert [names.masks.item(i).text() for i in range(names.masks.count())] == ["{track:02} {title}", "{title}"]
    assert names.replacement.text() == "-" and not names.problems.isVisible()
    assert "Split albums and the rename suggestion" in names.description.text()


def test_sections_order_the_cards(page):
    shared, tools, all_in_one = page.sections
    assert [label.text() for label in page.sections] == ["SHARED SETTINGS", "TOOLS", "ALL IN ONE"]
    assert shared.y() < page.names_card.y() < tools.y() < page.rename_card.y() < page.replaygain_card.y() \
        < all_in_one.y() < page.tidy_card.y()
    for card in (page.rename_card, page.replaygain_card, page.tidy_card):
        assert card.run_button.font().bold() and card.title.font().bold()
        assert card.title.font().pointSizeF() > card.description.font().pointSizeF()


def test_rename_card_defaults(page):
    card = page.rename_card
    assert card.options() == rename.DEFAULT_OPTIONS
    assert card.run_button.isEnabled() and not card.problems.isVisible()
    assert card.masks_line.text() == "File names: the masks in the File names card above."
    assert card.junk.text() == ".cue .lrc .nfo .sfv .txt"


def test_validation_is_inline(page):
    names, card, tidy = page.names_card, page.rename_card, page.tidy_card
    names.masks.item(0).setText("{track:02} {bogus}")
    assert names.problems.text() == "• mask '{track:02} {bogus}': unknown field(s) bogus"
    assert not card.run_button.isEnabled() and card.problems.text() == "• Fix the file names first."
    assert not tidy.run_button.isEnabled() and tidy.problems.text() == "• Fix the file names first."
    names.masks.item(0).setText("{track:02} {title}")
    assert card.run_button.isEnabled() and tidy.run_button.isEnabled()
    card.junk.setText(".cue .m3u .jpg")
    card.junk.textEdited.emit(card.junk.text())
    assert card.problems.text() == ("• .m3u can't be junk: these are playlists\n"
                                    "• .jpg can't be junk: these are cover images")
    names.replacement.setText("/")
    names.replacement.textEdited.emit("/")
    assert "• The replacement contains a character not allowed in file names" in names.problems.text()
    names.reset()
    card.reset()
    assert card.run_button.isEnabled() and card.options() == rename.DEFAULT_OPTIONS


def test_masks_can_be_added_filled_and_removed(page):
    names = page.names_card
    names.masks.setCurrentRow(1)
    names.remove_mask.click()
    assert page.rename_card.options().masks == ("{track:02} {title}",)
    names._add_mask()
    names.insert("{artist}")
    assert page.rename_card.options().masks == ("{track:02} {title}", "{title} {artist}")
    names.masks.clear()
    names._edited()
    assert names.problems.text() == "• No rename mask" and not page.rename_card.run_button.isEnabled()


def test_live_example(page):
    names = page.names_card
    assert names.example.text() == ""
    page.set_example({"track": 3, "title": "Who: Me?"}, ".FLAC")
    assert names.example.text() == "Example: 03 Who- Me-.flac"  # the replacement character, the lowercased ext
    names.masks.item(0).setText("{disc}-{track:02} {title}")  # no disc: the next mask
    assert names.example.text() == "Example: Who- Me-.flac"
    page.set_example({"genre": "Rock"}, ".mp3")
    assert names.example.text() == "Example: the first file fits no mask and keeps its name."


def test_masks_are_the_shared_setting(qtbot):
    settings = Settings(name_masks=["{album} - {title}"], name_replacement="_")
    page = ToolsPage(settings)
    qtbot.addWidget(page)
    assert page.rename_card.options().masks == ("{album} - {title}",)
    assert page.rename_card.options().replacement == "_"
    page.names_card.masks.item(0).setText("{title}")
    assert settings.name_masks == ["{title}"]
    assert "masks" not in settings.rename_options  # saved once, as the shared setting


def test_rename_card_for_a_selection(page):
    page.set_scope(ToolScope(Path("C:/lib/Album"), 1, (Path("C:/lib/Album/a.flac"),)))
    card = page.rename_card
    assert not card.cleanup.isEnabled() and not card.recursive.isEnabled() and not card.junk.isEnabled()
    assert card.description.text().startswith("Renames the selected music files")
    assert page.heading.text() == "<b>Tools</b> · 1 selected file in C:\\lib\\Album"


# -- the ReplayGain and Tidy cards --

def test_replaygain_card_defaults_and_validation(page):
    card = page.replaygain_card
    assert card.options() == replaygain.DEFAULT_OPTIONS and card.run_button.isEnabled()
    card.subfolders.setText("lite, Bonus")
    card.subfolders.textEdited.emit(card.subfolders.text())
    assert not card.run_button.isEnabled()
    assert "'Bonus' must be lowercase" in card.problems.text()
    assert not page.tidy_card.run_button.isEnabled()
    assert page.tidy_card.problems.text() == "• The ReplayGain card's options need fixing."
    card.reset()
    assert card.run_button.isEnabled() and page.tidy_card.run_button.isEnabled()


def test_replaygain_card_target_stays_in_rsgains_range(page):
    card = page.replaygain_card
    card.target.setValue(-40)
    assert card.options().target == -30.0


def test_replaygain_scope_and_unavailable_rsgain(page):
    card = page.replaygain_card
    card.scope_group.buttons()[1].setChecked(True)
    assert card.scope == "discography" and card.scope_note.text().startswith("Discography:")
    page.set_rsgain_unavailable("rsgain couldn't run, so ReplayGain is unavailable.")
    assert not card.run_button.isEnabled() and not page.tidy_card.run_button.isEnabled()
    assert "rsgain couldn't run" in card.scope_note.text()
    page.set_rsgain_unavailable(None)
    assert card.run_button.isEnabled()


def test_replaygain_and_tidy_need_a_folder(page):
    page.set_scope(ToolScope(Path("C:/lib/Album"), 1, (Path("C:/lib/Album/a.flac"),)))
    assert not page.replaygain_card.run_button.isEnabled()
    assert not page.tidy_card.run_button.isEnabled()
    assert page.rename_card.run_button.isEnabled()
    assert page.replaygain_card.scope_note.text().startswith("ReplayGain works on album folders.")


# -- requests and saved options --

def test_requests_carry_the_checked_options(page):
    received = requests(page)
    page.replaygain_card.log_file.setText("rg.log")
    page.replaygain_card.log_file.textEdited.emit("rg.log")
    page.rename_card.run_button.click()
    page.replaygain_card.run_button.click()
    page.tidy_card.run_button.click()
    assert [r.tool for r in received] == [RENAME, REPLAYGAIN, TIDY]
    assert received[0].rename_options.protected_names == ("rg.log",)  # the configured log is never junk
    assert received[1].replaygain_options.log_file == "rg.log" and not received[1].discography
    assert received[2].rename_options.masks == rename.DEFAULT_OPTIONS.masks  # Tidy: always the whole tree
    assert received[2].replaygain_options.log_file == "rg.log"


def test_invalid_options_send_no_request(page):
    received = requests(page)
    page.names_card.masks.item(0).setText("{nope}")
    page.request(RENAME)
    page.request(TIDY)
    assert received == []


def test_options_are_saved_and_restored(qtbot, tmp_path):
    settings = Settings(path=tmp_path / "settings.json")
    page = ToolsPage(settings)
    qtbot.addWidget(page)
    page.rename_card.cleanup.setChecked(True)
    page.replaygain_card.target.setValue(-18)
    settings.save()
    again = ToolsPage(Settings.load(tmp_path / "settings.json"))
    qtbot.addWidget(again)
    assert again.rename_card.options().cleanup
    assert again.replaygain_card.options().target == -18.0


# -- in the window --

@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    import shutil
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "album" / ALBUM, lib / "Artist" / ALBUM)
    (lib / "Artist" / ALBUM / "notes.txt").write_text("x")
    return lib


def folder_id(service, path) -> int:
    conn = service.conn
    return next(i for (i,) in conn.execute("SELECT id FROM entries WHERE is_dir = 1") if path_of(conn, i) == path)


def test_window_opens_tools_for_a_folder_and_back(qtbot, window, service, library_dir):
    album = library_dir / "Artist" / ALBUM
    window.open_folder(folder_id(service, library_dir / "Artist"))
    window.tools.open([folder_id(service, album)])
    assert window.showing_tools and window.tools_page.scope.folder == album
    assert window.tools_page.scope.first_track == album / "01 First.flac"  # natural order
    qtbot.waitUntil(lambda: window.tools_page.names_card.example.text() == "Example: 01 First.flac")
    window.tools_page.back_button.click()
    assert not window.showing_tools


def test_window_tools_for_a_selection_and_the_toolbar(qtbot, window, service, library_dir):
    album = library_dir / "Artist" / ALBUM
    window.open_folder(folder_id(service, album))
    ids = {r.name: r.id for r in window.file_view.model.rows}
    window.file_view.select_ids([ids["track2.flac"], ids["notes.txt"]])
    window.tools_action.trigger()
    scope = window.tools_page.scope
    assert scope.files == (album / "track2.flac",) and scope.folder == album  # the music files only
    window.close_tools()
    window.file_view.select_ids([])
    window.tools_action.trigger()  # nothing selected: the open folder
    assert window.tools_page.scope.folder == album and not window.tools_page.scope.is_selection


def test_window_tools_from_the_folder_summary_and_menus(window, service, library_dir):
    artist = folder_id(service, library_dir / "Artist")
    window.open_folder(artist)
    window.side_panel.show_folder(artist)
    window.side_panel.summary.tools_button.click()
    assert window.showing_tools and window.tools_page.scope.folder_id == artist
    window.close_tools()
    menu = window.list_menu([])
    next(a for a in menu.actions() if a.text() == "Tools…").trigger()
    assert window.showing_tools


def test_opening_another_folder_returns_to_the_list(window, service, library_dir):
    window.open_folder(folder_id(service, library_dir / "Artist"))
    window.tools.open([])
    window.open_folder(folder_id(service, library_dir / "Artist" / ALBUM))
    assert not window.showing_tools


def test_rsgain_unavailable_disables_replaygain(window):
    window.show_rsgain_version(None)
    assert not window.tools_page.replaygain_card.run_button.isEnabled()
    window.show_rsgain_version("rsgain 3.6")
    assert window.tools_page.replaygain_card.run_button.isEnabled()
