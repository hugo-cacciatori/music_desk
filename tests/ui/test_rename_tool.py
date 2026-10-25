"""The Rename tool in the window: preview, apply, locks and the index (SPEC §10 A). Files are copies. Renaming
never sends anything to the Recycle Bin (SPEC §32): `recycled` checks that nothing left the folders."""

import os
import shutil

import pytest

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


def preview(qtbot, window, service, folder, *, ids=None):
    window.open_folder(folder_id(service, folder))
    window.file_view.select_ids(ids or [])
    window.tools.start(RENAME)
    qtbot.waitUntil(lambda: window.rename_preview.state in ("preview", "error"), timeout=10000)
    return window.rename_preview


def apply(qtbot, window):
    window.rename_preview.apply_button.click()
    qtbot.waitUntil(lambda: window.rename_preview.state in ("result", "error"), timeout=10000)
    return window.rename_preview.report


def test_preview_then_apply(qtbot, window, service, library_dir, recycled):
    album = library_dir / "Artist" / ALBUM
    view = preview(qtbot, window, service, album)
    assert view.apply_button.text() == "Apply renames" and view.back_bottom.isVisible()
    assert not view.done_button.isVisible()  # in the preview: Apply, or back to the options
    assert view.message.text().endswith("Back changes nothing.")
    rows = {row[1]: row for row in view.visible_rows()}
    assert rows["track2.flac"][2:4] == ("02 Second.flac", "rename")
    assert rows["missing_title.flac"][3:] == ("missing tags", "missing: title")
    assert rows["no_track.mp3"][2:] == ("Loose.mp3", "fallback mask", "missing: track")
    assert rows["01 First.flac"][3] == "unchanged"
    assert view.summary.text().startswith(f"{sum(1 for r in rows.values() if r[3] in ('rename', 'fallback mask'))}/")
    assert (album / "track2.flac").exists()  # a preview changes nothing

    # the filter
    index = next(i for i in range(view.filter.count()) if view.filter.itemData(i) == "unchanged")
    view.filter.setCurrentIndex(index)
    assert {row[3] for row in view.visible_rows()} == {"unchanged"}

    old_id = next(r.id for r in window.file_view.model.rows if r.name == "track2.flac")
    report = apply(qtbot, window)
    assert "02 Second.flac" in report.renamed and (album / "02 Second.flac").exists()
    assert recycled == []
    assert view.extras.toPlainText().startswith(f"{len(report.renamed)}/")  # the script's wording
    # the index follows in place: same id, new name (the list shows it once the indexer is done)
    qtbot.waitUntil(lambda: any(r.id == old_id and r.name == "02 Second.flac" for r in window.file_view.model.rows),
                    timeout=10000)
    view.done_button.click()
    assert not window.showing_tools


def test_renaming_never_recycles(qtbot, window, service, library_dir, recycled):
    """The junk cleanup left Rename for Clean up (SPEC §32): no junk in the preview, the run or the report."""
    folder = library_dir / "Cleanup"
    before = {p.name for p in folder.rglob("*") if p.suffix.lower() not in (".flac", ".lrc")}
    view = preview(qtbot, window, service, folder)
    assert "Recycle Bin" not in view.extras.toPlainText() and "junk" not in view.extras.toPlainText()
    assert not hasattr(window.tools.cards.rename_card, "cleanup")  # no such option on the card
    apply(qtbot, window)
    assert "Recycle Bin" not in view.extras.toPlainText()
    assert {p.name for p in folder.rglob("*") if p.suffix.lower() not in (".flac", ".lrc")} == before
    assert service.locks.owner(folder) is None  # the lock is released after the run


def test_a_job_lock_refuses_the_run(qtbot, window, service, library_dir, recycled):
    album = library_dir / "Artist" / ALBUM
    view = preview(qtbot, window, service, album)
    token = service.locks.acquire([library_dir / "Artist"], "ReplayGain")
    view.apply_button.click()
    assert window.toast.label.text().endswith("is busy: ReplayGain is running on it.")
    assert view.state == "preview" and (album / "track2.flac").exists()
    service.locks.release(token)


def test_a_file_changed_since_the_preview_is_skipped(qtbot, window, service, library_dir, recycled):
    album = library_dir / "Artist" / ALBUM
    preview(qtbot, window, service, album)
    stat = os.stat(album / "track2.flac")
    os.utime(album / "track2.flac", ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))
    report = apply(qtbot, window)
    assert report.skipped == ["track2.flac: changed since the preview, not renamed"]
    assert (album / "track2.flac").exists()


def test_a_selection_renames_only_those_files(qtbot, window, service, library_dir, recycled):
    album = library_dir / "Artist" / ALBUM
    window.open_folder(folder_id(service, album))
    ids = {r.name: r.id for r in window.file_view.model.rows}
    view = preview(qtbot, window, service, album, ids=[ids["track2.flac"]])
    assert [row[1] for row in view.visible_rows()] == ["track2.flac"]
    assert "1 selected file" in view.heading.text()
    report = apply(qtbot, window)
    assert report.renamed == ["02 Second.flac"] and (album / "track6.FLAC").exists()


def test_back_cancels_a_preview_and_unsaved_edits_are_asked(qtbot, window, service, library_dir, recycled):
    album = library_dir / "Artist" / ALBUM
    view = preview(qtbot, window, service, album)
    view.back_button.click()
    assert window.center.currentWidget() is window.file_view

    window.open_folder(folder_id(service, album))
    ids = {r.name: r.id for r in window.file_view.model.rows}
    window.file_view.select_ids([ids["track2.flac"]])
    metadata = window.side_panel.metadata
    qtbot.waitUntil(lambda: metadata.snapshot is not None, timeout=5000)
    metadata.edits["genre"].setText("Edited")
    metadata.edits["genre"].textEdited.emit("Edited")
    metadata.ask_leave = lambda: "cancel"
    window.file_view.select_ids([])
    view = preview(qtbot, window, service, album)
    view.apply_button.click()
    assert view.state == "preview" and (album / "track2.flac").exists()  # Cancel: nothing renamed
    metadata.revert()


def test_tags_are_untouched_by_a_rename(qtbot, window, service, library_dir, recycled):
    album = library_dir / "Artist" / ALBUM
    before = TaggedFile(album / "track2.flac").fields()
    preview(qtbot, window, service, album)
    apply(qtbot, window)
    assert TaggedFile(album / "02 Second.flac").fields() == before


def test_renames_in_subfolders_reach_the_index(qtbot, window, service, library_dir, recycled):
    """A recursive rename touches several folders: each folder's renames are applied in place (ids kept), not
    only the first folder's. The watcher is off, so only the tool's own refresh can bring them in."""
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
    preview(qtbot, window, service, album)
    report = apply(qtbot, window)
    assert (lite / "02 Second.flac").exists() and (album / "02 Second.flac").exists() and report.renamed

    def name(entry_id):
        row = service.conn.execute("SELECT name FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return row[0] if row else None
    qtbot.waitUntil(lambda: name(sub_id) == "02 Second.flac" and name(top_id) == "02 Second.flac", timeout=10000)


def test_back_to_options_changes_nothing(qtbot, window, service, library_dir, recycled):
    album = library_dir / "Artist" / ALBUM
    view = preview(qtbot, window, service, album)
    window.tools.cards.rename_card.recursive.setChecked(False)  # an option set before; the card keeps it
    view.back_bottom.click()
    assert window.center.currentWidget() is window.file_view
    assert not window.tools.cards.rename_card.recursive.isChecked()
    window.tools.cards.rename_card.recursive.setChecked(True)
    assert (album / "track2.flac").exists() and recycled == []  # nothing was renamed
