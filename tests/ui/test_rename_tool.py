"""The Rename tool in the window: preview, apply, the Recycle Bin confirmation, locks and the index (SPEC §10 A).
Files are copies; the Recycle Bin is never used (the runner's recycle is a recorder)."""

import os
import shutil

import pytest

from music_desk.core.index.store import path_of
from music_desk.core.tags import TaggedFile
from music_desk.ui.tools.tools_page import RENAME

ALBUM = "Fixture Artist - Fixture Album"


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "album" / ALBUM, lib / "Artist" / ALBUM)
    shutil.copytree(fixtures_dir / "cleanup", lib / "Cleanup")
    return lib


@pytest.fixture
def recycled(window):
    paths = []
    window.rename_runner.recycle = paths.append
    return paths


def folder_id(service, path) -> int:
    conn = service.conn
    return next(i for (i,) in conn.execute("SELECT id FROM entries WHERE is_dir = 1") if path_of(conn, i) == path)


def preview(qtbot, window, service, folder, *, cleanup=False, ids=None):
    window.open_folder(folder_id(service, folder))
    window.open_tools(ids or [])
    card = window.tools_page.rename_card
    card.cleanup.setChecked(cleanup)
    window.tools_page.request(RENAME)
    qtbot.waitUntil(lambda: window.rename_preview.state in ("preview", "error"), timeout=10000)
    return window.rename_preview


def apply(qtbot, window):
    window.rename_preview.apply_button.click()
    qtbot.waitUntil(lambda: window.rename_preview.state in ("result", "error"), timeout=10000)
    return window.rename_preview.report


def test_preview_then_apply(qtbot, window, service, library_dir, recycled):
    album = library_dir / "Artist" / ALBUM
    view = preview(qtbot, window, service, album)
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


def test_junk_asks_first_and_can_be_kept(qtbot, window, service, library_dir, recycled):
    view = preview(qtbot, window, service, library_dir / "Cleanup", cleanup=True)
    assert "6 files would be sent to the Recycle Bin as junk (you confirm first)" in view.extras.toPlainText()
    asked = []
    window.ask_recycle = lambda names: asked.append(names) or "cancel"
    view.apply_button.click()
    assert view.state == "preview" and len(asked[0]) == 6  # cancelled: nothing ran
    window.ask_recycle = lambda names: "keep"
    report = apply(qtbot, window)
    assert recycled == [] and report.junk == [] and report.renamed
    assert (library_dir / "Cleanup" / "single_file_album" / "notes.txt").exists()


def test_junk_goes_to_the_recycle_bin_once_confirmed(qtbot, window, service, library_dir, recycled):
    view = preview(qtbot, window, service, library_dir / "Cleanup", cleanup=True)
    owners = []
    window.rename_runner.recycle = lambda path: (recycled.append(path),
                                                 owners.append(service.locks.owner(path)))
    window.ask_recycle = lambda names: "recycle"
    report = apply(qtbot, window)
    assert len(recycled) == len(report.junk) == 6
    assert set(owners) == {"the Rename tool"}  # the folder is locked during the run
    assert service.locks.owner(library_dir / "Cleanup") is None  # and released after
    assert "6 files were sent to the Recycle Bin as junk" in view.extras.toPlainText()


def test_a_job_lock_refuses_the_run(qtbot, window, service, library_dir, recycled):
    album = library_dir / "Artist" / ALBUM
    view = preview(qtbot, window, service, album)
    token = service.locks.acquire([library_dir / "Artist"], "the ReplayGain tool")
    view.apply_button.click()
    assert window.toast.label.text().endswith("is in use by the ReplayGain tool. Try again when it has finished.")
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
    assert window.center.currentWidget() is window.tools_page

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
