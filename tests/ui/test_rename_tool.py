"""The toolbar's Rename (SPEC §10 A, §30, §35): the Rename entry's window on the selection or the open folder, its
preview, apply, locks, and the main window's index renamed in place. Files are copies. Renaming never sends
anything to the Recycle Bin (SPEC §32): `recycled` checks that nothing left the folders."""

import os
import shutil

import pytest

from music_desk.context_menu.rename_entry import RenameWindow
from music_desk.core.index.store import path_of
from music_desk.core.tags import TaggedFile
from music_desk.ui.tools.tool_cards import RENAME

ALBUM = "Fixture Artist - Fixture Album"


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "album" / ALBUM, lib / "Artist" / ALBUM)
    shutil.copytree(fixtures_dir / "cleanup", lib / "Cleanup")
    return lib


@pytest.fixture
def recycled(library_dir):
    """The files that disappeared without being renamed: none, ever (a rename keeps every file, renamed)."""
    return []


def folder_id(service, path) -> int:
    conn = service.conn
    return next(i for (i,) in conn.execute("SELECT id FROM entries WHERE is_dir = 1") if path_of(conn, i) == path)


def preview(qtbot, open_app, service, folder, *, names=()) -> RenameWindow:
    """The toolbar's Rename on `folder` (open), or on the named files selected in it."""
    main = open_app.window
    main.open_folder(folder_id(service, folder))
    ids = {r.name: r.id for r in main.file_view.model.rows}
    main.file_view.select_ids([ids[name] for name in names])
    before = list(open_app.entry_windows)
    main.tools.start(RENAME)
    [window] = [w for w in open_app.entry_windows if w not in before]
    assert isinstance(window, RenameWindow)
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: window.view.state in ("preview", "error"), timeout=10000)
    return window


def apply(qtbot, window: RenameWindow):
    window.view.apply_button.click()
    qtbot.waitUntil(lambda: window.view.state in ("result", "error"), timeout=10000)
    return window.view.report


def test_preview_then_apply(qtbot, open_app, service, library_dir, recycled):
    album = library_dir / "Artist" / ALBUM
    main = open_app.window
    window = preview(qtbot, open_app, service, album)
    view = window.view
    assert window.parent() is None and main.center.currentWidget() is main.file_view  # its own window (SPEC §35)
    assert view.heading.text().endswith(str(album)) and view.apply_button.text() == "Apply renames"
    rows = {row[1]: row for row in view.visible_rows()}
    assert rows["track2.flac"][2:4] == ("02 Second.flac", "rename")
    assert rows["missing_title.flac"][3:] == ("missing tags", "missing: title")
    assert rows["no_track.mp3"][2:] == ("Loose.mp3", "fallback mask", "missing: track")
    assert rows["01 First.flac"][3] == "unchanged"
    assert (album / "track2.flac").exists()  # a preview changes nothing

    old_id = next(r.id for r in main.file_view.model.rows if r.name == "track2.flac")
    report = apply(qtbot, window)
    assert "02 Second.flac" in report.renamed and (album / "02 Second.flac").exists()
    assert recycled == []
    assert view.extras.toPlainText().startswith(f"{len(report.renamed)}/")  # the script's wording
    # the main window's index follows in place: same id, new name
    qtbot.waitUntil(lambda: any(r.id == old_id and r.name == "02 Second.flac" for r in main.file_view.model.rows),
                    timeout=10000)
    view.done_button.click()
    assert open_app.entry_windows == []


def test_renaming_never_recycles(qtbot, open_app, service, library_dir, recycled):
    """The junk cleanup left Rename for Clean up (SPEC §32): no junk in the preview, the run or the report."""
    folder = library_dir / "Cleanup"
    before = {p.name for p in folder.rglob("*") if p.suffix.lower() not in (".flac", ".lrc")}
    window = preview(qtbot, open_app, service, folder)
    assert "Recycle Bin" not in window.view.extras.toPlainText() and "junk" not in window.view.extras.toPlainText()
    assert not hasattr(open_app.window.tools.cards.rename_card, "cleanup")  # no such option on the card
    apply(qtbot, window)
    assert "Recycle Bin" not in window.view.extras.toPlainText()
    assert {p.name for p in folder.rglob("*") if p.suffix.lower() not in (".flac", ".lrc")} == before
    assert service.locks.owner(folder) is None  # the lock is released after the run


def test_a_busy_folder_refuses_the_run(qtbot, open_app, service, library_dir, recycled):
    album = library_dir / "Artist" / ALBUM
    window = preview(qtbot, open_app, service, album)
    token = service.locks.acquire([library_dir / "Artist"], "ReplayGain")
    window.view.apply_button.click()
    assert window.view.message.text() == f"{ALBUM} is busy: ReplayGain is running on it."
    assert window.view.state == "preview" and (album / "track2.flac").exists()
    service.locks.release(token)


def test_a_file_changed_since_the_preview_is_skipped(qtbot, open_app, service, library_dir, recycled):
    album = library_dir / "Artist" / ALBUM
    window = preview(qtbot, open_app, service, album)
    stat = os.stat(album / "track2.flac")
    os.utime(album / "track2.flac", ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))
    report = apply(qtbot, window)
    assert report.skipped == ["track2.flac: changed since the preview, not renamed"]
    assert (album / "track2.flac").exists()


def test_a_selection_renames_only_those_files(qtbot, open_app, service, library_dir, recycled):
    album = library_dir / "Artist" / ALBUM
    window = preview(qtbot, open_app, service, album, names=["track2.flac"])
    assert [row[1] for row in window.view.visible_rows()] == ["track2.flac"]
    assert window.view.heading.text().endswith(str(album / "track2.flac"))  # what was selected
    report = apply(qtbot, window)
    assert report.renamed == ["02 Second.flac"] and (album / "track6.FLAC").exists()


def test_unsaved_edits_are_asked_first(qtbot, open_app, service, library_dir, recycled):
    """Unsaved Metadata edits on a file Rename would change: Save, Discard or Cancel before its window opens."""
    album = library_dir / "Artist" / ALBUM
    main = open_app.window
    main.open_folder(folder_id(service, album))
    ids = {r.name: r.id for r in main.file_view.model.rows}
    main.file_view.select_ids([ids["track2.flac"]])
    metadata = main.side_panel.metadata
    qtbot.waitUntil(lambda: metadata.snapshot is not None, timeout=5000)
    metadata.edits["genre"].setText("Edited")
    metadata.edits["genre"].textEdited.emit("Edited")
    metadata.ask_leave = lambda: "cancel"
    main.tools.start(RENAME)
    assert open_app.entry_windows == [] and (album / "track2.flac").exists()  # Cancel: no window, nothing renamed
    metadata.revert()


def test_tags_are_untouched_by_a_rename(qtbot, open_app, service, library_dir, recycled):
    album = library_dir / "Artist" / ALBUM
    before = TaggedFile(album / "track2.flac").fields()
    apply(qtbot, preview(qtbot, open_app, service, album))
    assert TaggedFile(album / "02 Second.flac").fields() == before


def test_renames_in_subfolders_reach_the_index(qtbot, open_app, service, library_dir, recycled):
    """A recursive rename touches several folders: each folder's renames are applied in place (ids kept), not
    only the first folder's. The watcher is off, so only the window's own report can bring them in."""
    album = library_dir / "Artist" / ALBUM
    lite = album / "lite"
    service.watcher.sync({})
    (lite / "02 Second.flac").rename(lite / "second.flac")
    service.indexer.request_path(str(lite))
    qtbot.waitUntil(lambda: service.conn.execute(
        "SELECT 1 FROM entries WHERE parent_id = ? AND name = 'second.flac'", (folder_id(service, lite),)
    ).fetchone() is not None, timeout=10000)
    sub_id = service.conn.execute("SELECT id FROM entries WHERE parent_id = ? AND name = 'second.flac'",
                                  (folder_id(service, lite),)).fetchone()[0]
    top_id = service.conn.execute("SELECT id FROM entries WHERE parent_id = ? AND name = 'track2.flac'",
                                  (folder_id(service, album),)).fetchone()[0]
    report = apply(qtbot, preview(qtbot, open_app, service, album))
    assert (lite / "02 Second.flac").exists() and (album / "02 Second.flac").exists() and report.renamed

    def name(entry_id):
        row = service.conn.execute("SELECT name FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return row[0] if row else None
    qtbot.waitUntil(lambda: name(sub_id) == "02 Second.flac" and name(top_id) == "02 Second.flac", timeout=10000)


def test_close_changes_nothing(qtbot, open_app, service, library_dir, recycled):
    album = library_dir / "Artist" / ALBUM
    window = preview(qtbot, open_app, service, album)
    window.view.back_bottom.click()  # Close
    assert open_app.entry_windows == [] and (album / "track2.flac").exists() and recycled == []
