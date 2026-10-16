"""Inline rename in the window (SPEC §11): list and tree, F2 with the stem selected, the extension prompt,
refusals, the .lrc sidecar, Undo, ids kept in the index, and the rename hint after a save (SPEC §7)."""

from browser_helpers import column, entry
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLineEdit

from music_desk.core.tags import TaggedFile


def rename_in_list(qtbot, window, name, new_name):
    """Commit an inline edit of the Name cell, as typing in the editor and pressing Enter does."""
    proxy = window.file_view.proxy
    index = proxy.index(column(window, "name").index(name), 0)
    with qtbot.waitSignal(window.renameDone, timeout=5000):
        proxy.setData(index, new_name)


def wait_name(qtbot, window, name):
    qtbot.waitUntil(lambda: name in column(window, "name"), timeout=10000)


def test_rename_a_file_with_its_sidecar_and_undo(qtbot, window, service, library_dir):
    album = library_dir / "Artist" / "Album"
    (album / "Track 2.lrc").write_text("[00:01.00]la")
    qtbot.waitUntil(lambda: service.conn.execute("SELECT count(*) FROM entries WHERE name = 'Track 2.lrc'"
                                                 ).fetchone()[0] == 1, timeout=10000)
    window.open_folder(entry(service, "Artist", "Album"))
    track_id = entry(service, "Artist", "Album", "Track 2.flac")
    rename_in_list(qtbot, window, "Track 2.flac", "02 Second.flac")
    assert (album / "02 Second.flac").exists() and (album / "02 Second.lrc").exists()
    wait_name(qtbot, window, "02 Second.flac")
    assert entry(service, "Artist", "Album", "02 Second.flac") == track_id  # updated in place
    assert window.toast.isVisible() and "its lyrics file Track 2.lrc" in window.toast.label.text()
    assert window.toast.button.text() == "Undo"
    with qtbot.waitSignal(window.renameDone, timeout=5000):
        window.toast.button.click()
    assert (album / "Track 2.flac").exists() and (album / "Track 2.lrc").exists()
    wait_name(qtbot, window, "Track 2.flac")
    assert entry(service, "Artist", "Album", "Track 2.flac") == track_id


def test_f2_selects_the_stem(qtbot, window, service):
    window.open_folder(entry(service, "Artist", "Album"))
    view = window.file_view.view
    window.file_view.select_ids([entry(service, "Artist", "Album", "Track 3.mp3")])
    view.setFocus()
    qtbot.keyClick(view, Qt.Key.Key_F2)
    qtbot.waitUntil(lambda: isinstance(QApplication.focusWidget(), QLineEdit), timeout=3000)
    editor = QApplication.focusWidget()
    qtbot.waitUntil(lambda: editor.selectedText() == "Track 3", timeout=3000)
    assert editor.text() == "Track 3.mp3"
    qtbot.keyClick(editor, Qt.Key.Key_Escape)


def test_extension_change_asks_first(qtbot, window, service, library_dir, monkeypatch):
    window.open_folder(entry(service, "Artist", "Album"))
    asked = []
    monkeypatch.setattr(window, "confirm_extension_change", lambda old, new: asked.append((old, new)) or False)
    window.rename_entry(entry(service, "Artist", "Album", "cover.jpg"), "cover.png")
    qtbot.wait(200)
    assert asked == [("cover.jpg", "cover.png")] and (library_dir / "Artist" / "Album" / "cover.jpg").exists()
    monkeypatch.setattr(window, "confirm_extension_change", lambda old, new: True)
    with qtbot.waitSignal(window.renameDone, timeout=5000):
        window.rename_entry(entry(service, "Artist", "Album", "cover.jpg"), "cover.png")
    assert (library_dir / "Artist" / "Album" / "cover.png").exists()


def test_refusals_are_shown(qtbot, window, service, library_dir):
    window.open_folder(entry(service, "Artist", "Album"))
    with qtbot.waitSignal(window.renameFailed, timeout=5000):
        window.rename_entry(entry(service, "Artist", "Album", "Track 2.flac"), "Track 10.flac")  # same extension
    qtbot.waitUntil(window.toast.isVisible, timeout=3000)
    assert "already exists" in window.toast.label.text()
    with qtbot.waitSignal(window.renameFailed, timeout=5000):
        window.rename_entry(entry(service, "Artist", "Album", "Track 2.flac"), "bad?name.flac")
    with service.locks.lock([library_dir / "Artist"], "ReplayGain (album)"), \
            qtbot.waitSignal(window.renameFailed, timeout=5000) as blocker:
        window.rename_entry(entry(service, "Artist", "Album", "Track 2.flac"), "x.flac")
    assert "while ReplayGain (album) is running" in blocker.args[0]
    assert (library_dir / "Artist" / "Album" / "Track 2.flac").exists()


def test_library_roots_cant_be_renamed(window, service, library_dir):
    top = service.roots()[0].top_entry_id
    assert not window.sidebar.model.flags(window.sidebar.model.index_for(top)) & Qt.ItemFlag.ItemIsEditable
    window.rename_entry(top, "Elsewhere")
    assert "library folder can't be renamed" in window.toast.label.text() and library_dir.exists()


def test_rename_a_folder_in_the_tree(qtbot, window, service, library_dir):
    album = entry(service, "Artist", "Album")
    window.open_folder(album)
    index = window.sidebar.model.index_for(album)
    assert window.sidebar.model.flags(index) & Qt.ItemFlag.ItemIsEditable
    with qtbot.waitSignal(window.renameDone, timeout=5000):
        window.sidebar.model.setData(index, "Album (2024)")
    qtbot.waitUntil(lambda: window.sidebar.model.index_for(album).data() == "Album (2024)", timeout=10000)
    assert window.folder_id == album  # the open folder kept its id
    qtbot.waitUntil(lambda: window.folder_path == str(library_dir / "Artist" / "Album (2024)"), timeout=10000)
    assert window.address_bar.segment_labels()[-1] == "Album (2024)"
    assert (library_dir / "Artist" / "Album (2024)" / "Track 2.flac").exists()


def test_rename_from_the_menu(qtbot, window, service):
    window.open_folder(entry(service, "Artist", "Album"))
    menu = window.list_menu([entry(service, "Artist", "Album", "Track 2.flac")])
    next(a for a in menu.actions() if a.text() == "Rename\tF2").trigger()
    qtbot.waitUntil(lambda: isinstance(QApplication.focusWidget(), QLineEdit), timeout=3000)
    assert QApplication.focusWidget().text() == "Track 2.flac"
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Escape)


def test_the_panel_follows_a_renamed_file(qtbot, window, service):
    window.open_folder(entry(service, "Artist", "Album"))
    window.file_view.select_ids([entry(service, "Artist", "Album", "Track 2.flac")])
    tab = window.side_panel.metadata
    qtbot.waitUntil(lambda: tab.snapshot is not None, timeout=10000)
    rename_in_list(qtbot, window, "Track 2.flac", "Second.flac")
    qtbot.waitUntil(lambda: tab.snapshot is not None and tab.name_label.text() == "Second.flac", timeout=10000)


def test_unsaved_edits_are_asked_about_first(qtbot, window, service, library_dir, monkeypatch):
    window.open_folder(entry(service, "Artist", "Album"))
    window.file_view.select_ids([entry(service, "Artist", "Album", "Track 2.flac")])
    tab = window.side_panel.metadata
    qtbot.waitUntil(lambda: tab.snapshot is not None, timeout=10000)
    tab.edits["genre"].setText("Jazz")
    tab.edits["genre"].textEdited.emit("Jazz")
    monkeypatch.setattr(tab, "ask_leave", lambda: "cancel")
    window.rename_entry(entry(service, "Artist", "Album", "Track 2.flac"), "Other.flac")
    qtbot.wait(200)
    assert (library_dir / "Artist" / "Album" / "Track 2.flac").exists() and tab.dirty


def test_rename_hint_after_a_save(qtbot, window, service, library_dir):
    window.open_folder(entry(service, "Artist", "Album"))
    window.file_view.select_ids([entry(service, "Artist", "Album", "Track 2.flac")])
    tab = window.side_panel.metadata
    qtbot.waitUntil(lambda: tab.snapshot is not None, timeout=10000)
    assert not tab.rename_hint.isVisibleTo(tab)  # only after a save
    tab.edits["title"].setText("Second Song")
    tab.edits["title"].textEdited.emit("Second Song")
    with qtbot.waitSignal(tab.fileSaved, timeout=10000):
        tab.save()
    assert tab.rename_hint.isVisibleTo(tab) and '"01 Second Song.flac"' in tab.rename_hint.text()
    with qtbot.waitSignal(window.renameDone, timeout=5000):
        tab.rename_hint.linkActivated.emit("rename")
    assert (library_dir / "Artist" / "Album" / "01 Second Song.flac").exists()
    assert TaggedFile(library_dir / "Artist" / "Album" / "01 Second Song.flac").fields().title == "Second Song"
    wait_name(qtbot, window, "01 Second Song.flac")


def test_the_rename_hint_follows_the_users_masks(qtbot, window, service, library_dir):
    """The shared File names masks (SPEC §21), not the defaults."""
    window.settings.name_masks = ["{title} [{track}]"]
    window.open_folder(entry(service, "Artist", "Album"))
    window.file_view.select_ids([entry(service, "Artist", "Album", "Track 2.flac")])
    tab = window.side_panel.metadata
    qtbot.waitUntil(lambda: tab.snapshot is not None, timeout=10000)
    tab.edits["title"].setText("Second: Song")
    tab.edits["title"].textEdited.emit("Second: Song")
    with qtbot.waitSignal(tab.fileSaved, timeout=10000):
        tab.save()
    assert '"Second- Song [1].flac"' in tab.rename_hint.text()
