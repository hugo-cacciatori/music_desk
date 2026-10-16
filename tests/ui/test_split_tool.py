"""The cue split in the window (SPEC §22): the card and its options, the preview with its encoding picker, the
job with the real bundled flac on copied fixtures, the lock, Stop, recycling through a recorder (never the real
Recycle Bin), ReplayGain afterwards with a fake rsgain, and "Split into tracks…"."""

import shutil
from pathlib import Path

import pytest
from rsgain_fake import FakeScanner

from music_desk.core.index.store import path_of
from music_desk.core.split import SplitOptions
from music_desk.core.tags import TaggedFile
from music_desk.settings import Settings
from music_desk.ui.tools.tools_page import SPLIT, ToolScope, ToolsPage

HIDDEN = "hidden_track"
SOURCE = "Fixture Artist - Split Album.flac"


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    for name in (HIDDEN, "wav_image", "too_long"):
        shutil.copytree(fixtures_dir / "split" / name, lib / "Albums" / name)
    sjis = lib / "Albums" / "sjis"
    sjis.mkdir()
    shutil.copy2(fixtures_dir / "split" / "sheets" / "sjis.cue", sjis / "album.cue")
    shutil.copy2(fixtures_dir / "split" / HIDDEN / SOURCE, sjis / "x.flac")
    return lib


@pytest.fixture
def recycled(window):
    paths = []
    window.split_runner.recycle = paths.append
    return paths


def folder_id(service, path) -> int:
    conn = service.conn
    return next(i for (i,) in conn.execute("SELECT id FROM entries WHERE is_dir = 1") if path_of(conn, i) == path)


def file_id(service, path) -> int:
    conn = service.conn
    return next(i for (i,) in conn.execute("SELECT id FROM entries WHERE is_dir = 0") if path_of(conn, i) == path)


def preview(qtbot, window, service, folder, options=None):
    window.open_folder(folder_id(service, folder))
    window.open_tools([])
    window.tools_page.split_card.set_options(options or SplitOptions())
    window.tools_page.request(SPLIT)
    page = window.split_preview
    qtbot.waitUntil(lambda: page.state in ("preview", "error"), timeout=20000)
    return page


def run(qtbot, window):
    window.split_preview.apply_button.click()
    qtbot.waitUntil(lambda: window.split_preview.state in ("result", "error"), timeout=30000)
    return window.split_preview.result


# -- the card --

@pytest.fixture
def page(qtbot):
    page = ToolsPage(Settings())
    qtbot.addWidget(page)
    page.set_scope(ToolScope(Path("C:/lib/Album"), 1))
    page.show()
    return page


def test_the_card_comes_last_with_its_options(page):
    card = page.split_card
    assert card.y() > page.tidy_card.y()
    assert card.options() == SplitOptions(recursive=True, recycle=False, replaygain=False)
    assert not card.replaygain.isEnabled() and "measures every audio file" in card.replaygain.toolTip()
    card.recycle.setChecked(True)
    assert card.replaygain.isEnabled()
    card.replaygain.setChecked(True)
    assert card.options().replaygain
    card.recycle.setChecked(False)
    assert not card.replaygain.isEnabled() and not card.options().replaygain  # never without recycling
    assert page.settings.split_options == {"recursive": True, "recycle": False, "replaygain": False}


def test_run_is_disabled_by_bad_masks_flac_or_a_selection(page):
    card = page.split_card
    assert card.run_button.isEnabled()
    page.names_card.masks.item(0).setText("{nope}")
    assert not card.run_button.isEnabled() and card.problems.text() == "• Fix the file names first."
    page.names_card.reset()
    page.set_flac_unavailable("flac couldn't run, so the split is unavailable.")
    assert not card.run_button.isEnabled() and "flac couldn't run" in card.note.text()
    page.set_flac_unavailable(None)
    page.set_scope(ToolScope(Path("C:/lib/Album"), 1, (Path("C:/lib/Album/a.flac"),)))
    assert not card.run_button.isEnabled() and "Split into tracks" in card.note.text()


def test_options_are_remembered(qtbot):
    settings = Settings(split_options={"recursive": False, "recycle": True, "replaygain": True})
    page = ToolsPage(settings)
    qtbot.addWidget(page)
    assert page.split_card.options() == SplitOptions(recursive=False, recycle=True, replaygain=True)


# -- the preview and the job --

def test_preview_then_split(qtbot, window, service, library_dir, recycled):
    albums = library_dir / "Albums"
    page = preview(qtbot, window, service, albums)
    names = {p.cue.parent.name: p for p in page.plans}
    assert set(names) == {HIDDEN, "wav_image", "too_long", "sjis"}
    assert names["too_long"].refusal.startswith("Track 2 starts after the end of the audio")
    assert page.apply_button.text() == "Split 3 albums"
    assert page.summary.text().startswith("3 albums → 8 tracks. 1 album left alone.")
    assert "stay where they are" in page.summary.text()
    hidden = next(b for b in page.blocks if b.plan.cue.parent.name == HIDDEN)
    assert hidden.table.item(0, 3).text() == "00 Hidden track.flac" and hidden.table.item(1, 1).text() == "0:03"
    assert not list(albums.rglob("*.partial")) and not (albums / HIDDEN / "01 Opening.flac").exists()

    result = run(qtbot, window)
    assert not result.error and not result.cancelled and sum(a.split for a in result.albums) == 3
    assert sorted(p.name for p in (albums / HIDDEN).iterdir()) == [
        "00 Hidden track.flac", "01 Opening.flac", "02 Second Song.flac", "03 Duet.flac", SOURCE, "Split Album.cue"]
    assert TaggedFile(albums / HIDDEN / "03 Duet.flac").fields().artist == "Fixture Artist & Guest"
    assert recycled == [] and not list(albums.rglob("*.partial"))
    report = page.report.toPlainText()
    assert report.startswith("Done: 3 albums split into 8 tracks, all identical to their originals.")
    assert "Left alone (1):" in report and "The originals stay where they are." in report
    job = window.jobs.jobs[-1]
    assert job.kind == "Split" and job.state == "done" and job.status == "3 albums split, 1 album not split"
    qtbot.waitUntil(lambda: service.conn.execute(
        "SELECT 1 FROM entries WHERE name = '01 Opening.flac'").fetchone() is not None, timeout=10000)
    assert service.locks.held() == []


def test_recycling_then_replaygain(qtbot, window, service, library_dir, recycled):
    scanner = FakeScanner(values={})
    window.split_runner.scanner = scanner
    album = library_dir / "Albums" / "wav_image"
    page = preview(qtbot, window, service, album, SplitOptions(recycle=True, replaygain=True))
    assert "Then ReplayGain runs on each album." in page.summary.text()
    result = run(qtbot, window)
    assert recycled == [album / "image.wav", album / "image.cue"]  # the recorder: nothing really moved
    # The recorder left the album file in place, so this run's ReplayGain saw both files; the real Recycle Bin
    # would have taken it. What matters here: it ran, on that folder, after the split.
    assert scanner.calls and scanner.calls[0][1] == album
    assert result.albums[0].replaygain is not None
    assert "ReplayGain:" in page.report.toPlainText()


def test_originals_that_cant_be_recycled_stay_and_skip_replaygain(qtbot, window, service, library_dir):
    from music_desk.core.fsops import CannotRecycle

    def refuse(path):
        raise CannotRecycle("X: is a network location, which has no Recycle Bin")

    window.split_runner.recycle = refuse
    scanner = FakeScanner(values={})
    window.split_runner.scanner = scanner
    album = library_dir / "Albums" / "wav_image"
    preview(qtbot, window, service, album, SplitOptions(recycle=True, replaygain=True))
    result = run(qtbot, window)
    assert result.albums[0].finished.kept_reason == "image.wav stayed: X: is a network location, which has no " \
                                                    "Recycle Bin"
    assert scanner.calls == [] and "ReplayGain wasn't run" in window.split_preview.report.toPlainText()
    assert (album / "image.wav").exists() and (album / "01 One.flac").exists()


def test_the_encoding_picker_reads_the_album_again(qtbot, window, service, library_dir):
    page = preview(qtbot, window, service, library_dir / "Albums" / "sjis")
    block = page.blocks[0]
    assert block.encoding.currentText().startswith("Auto (Windows-1252")
    assert block.plan.sheet.performer != "宇多田ヒカル"
    assert "pick the cue sheet's encoding" in block.notes.text()
    block.encoding.setCurrentIndex(block.encoding.findData("shift_jis"))
    qtbot.waitUntil(lambda: page.blocks[0].plan.sheet.performer == "宇多田ヒカル", timeout=10000)
    assert page.blocks[0].table.item(1, 4).text() == "花束を君に"
    assert page.blocks[0].encoding.currentData() == "shift_jis"


def test_a_job_lock_refuses_the_split(qtbot, window, service, library_dir):
    album = library_dir / "Albums" / "wav_image"
    preview(qtbot, window, service, album)
    token = service.locks.acquire([album], "the Rename tool")
    try:
        window.split_preview.apply_button.click()
        assert window.toast.label.text().endswith("is in use by the Rename tool. Try again when it has finished.")
        assert window.split_preview.state == "preview"
    finally:
        service.locks.release(token)


def test_stop_leaves_no_partial_files(qtbot, window, service, library_dir):
    album = library_dir / "Albums" / HIDDEN
    preview(qtbot, window, service, album)
    window.split_preview.apply_button.click()
    window.split_runner.cancel()  # at once: before the first album, or while flac writes it
    qtbot.waitUntil(lambda: window.split_preview.state == "result", timeout=30000)
    assert window.split_preview.result.cancelled
    assert sorted(p.name for p in album.iterdir()) == [SOURCE, "Split Album.cue"]
    assert window.jobs.jobs[-1].state == "stopped"


# -- "Split into tracks…" --

def test_split_into_tracks_from_the_menu_and_the_cue_preview(qtbot, window, service, library_dir):
    album = library_dir / "Albums" / HIDDEN
    window.open_folder(folder_id(service, album))
    texts = lambda ids: [a.text() for a in window.list_menu(ids).actions()]  # noqa: E731
    sheet, source = file_id(service, album / "Split Album.cue"), file_id(service, album / SOURCE)
    assert "Split into tracks…" in texts([sheet]) and "Split into tracks…" in texts([source])

    window.split_album(album / SOURCE)
    page = window.split_preview
    qtbot.waitUntil(lambda: page.state == "preview", timeout=20000)
    assert [p.cue.name for p in page.plans] == ["Split Album.cue"] and window.center.currentWidget() is page

    window.close_tools()
    window.open_folder(folder_id(service, album))
    window.file_view.select_ids([sheet])
    qtbot.waitUntil(lambda: window.side_panel.preview.result is not None, timeout=5000)
    button = window.side_panel.preview.split_button
    assert button.isVisible()
    button.click()
    qtbot.waitUntil(lambda: page.state == "preview" and window.center.currentWidget() is page, timeout=20000)
    assert [p.cue.name for p in page.plans] == ["Split Album.cue"]
