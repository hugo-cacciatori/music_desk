"""The file preview page (SPEC §20): text in its encodings, images scaled to fit, read-only, stale reads ignored,
reloads when the file changes, and the other selections unchanged."""

import os
import shutil
import time

import pytest
from browser_helpers import entry
from PySide6.QtGui import QImage, QImageIOHandler, QImageWriter
from PySide6.QtWidgets import QAbstractButton, QLineEdit, QPlainTextEdit

from refrain.core import preview
from refrain.core.path_locks import PathLocks
from refrain.services.tag_io import TagIO
from refrain.services.tag_writer import TagWriter
from refrain.ui.side_panel.panel import SelectedItem, SidePanel


@pytest.fixture
def panel(qtbot):
    io = TagIO()
    writer = TagWriter(PathLocks())
    panel = SidePanel(io, writer)
    qtbot.addWidget(panel)
    panel.resize(360, 600)
    panel.show()
    yield panel
    writer.close()
    io.close()


def show(qtbot, panel, path, stamp=None):
    with qtbot.waitSignal(panel.io.previewRead, timeout=5000):
        panel.show_selection([SelectedItem(path, False, False)])
    qtbot.waitUntil(lambda: panel.preview.result is not None, timeout=5000)
    return panel.preview


# A 40 × 30 one-colour GIF, written by hand: Qt reads GIF but can't write it.
GIF = (b"GIF89a" + (40).to_bytes(2, "little") + (30).to_bytes(2, "little") + b"\x80\x00\x00"
       + b"\x33\x66\xcc\x00\x00\x00" + b",\x00\x00\x00\x00" + (40).to_bytes(2, "little")
       + (30).to_bytes(2, "little") + b"\x00\x02\x02\x44\x01\x00;")


def save_image(path, width, height, fmt=None, transformation=None):
    if path.suffix == ".gif":
        path.write_bytes(GIF)
        return
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(0x3366CC)
    writer = QImageWriter(str(path), fmt or path.suffix[1:].encode())
    if transformation is not None:
        writer.setTransformation(transformation)
    assert writer.write(image), writer.errorString()


def test_a_text_and_its_encoding(qtbot, panel, fixtures_dir):
    """One encoding shows the page's wiring; tests/core/test_preview.py reads every encoding (SPEC §18)."""
    name, encoding, snippet = "eac.log", "UTF-16 LE", "Fixture Artist / Café Album"
    page = show(qtbot, panel, fixtures_dir / "preview" / name)
    assert panel.stack.currentWidget() is page and page.pages.currentWidget() is page.text
    assert snippet in page.text.toPlainText()
    assert page.name_label.text() == name and page.details.text().endswith(" · " + encoding)
    assert "modified " in page.details.text() and not page.note.isVisible()


def test_every_listed_type_is_previewed(qtbot, panel, tmp_path):
    for suffix in sorted(preview.TEXT_TYPES):
        path = tmp_path / f"a{suffix}"
        path.write_text("hello", encoding="utf-8")
        page = show(qtbot, panel, path)
        assert page.text.toPlainText() == "hello", suffix
    for suffix in sorted(preview.IMAGE_TYPES):
        path = tmp_path / f"a{suffix}"
        save_image(path, 40, 30, b"jpeg" if suffix == ".jpeg" else None)
        page = show(qtbot, panel, path)
        assert page.result.error is None and page.result.pixels == (40, 30), suffix
        assert page.pages.currentWidget() is page.image_area


def test_nothing_on_the_page_can_write(qtbot, panel, fixtures_dir):
    page = show(qtbot, panel, fixtures_dir / "preview" / "eac.log")
    assert page.text.isReadOnly()
    assert page.findChildren(QLineEdit) == []
    editors = page.findChildren(QPlainTextEdit)
    assert editors == [page.text] and all(e.isReadOnly() for e in editors)
    assert [b.text() for b in page.findChildren(QAbstractButton) if b.isVisible()] == ["Open"]  # Split: .cue only


def test_a_text_over_the_cap(qtbot, panel, tmp_path):
    path = tmp_path / "big.log"
    path.write_bytes((b"x" * 99 + b"\n") * 20000)  # about 2 MB
    page = show(qtbot, panel, path)
    assert page.note.isVisible()
    assert page.note.text() == "Showing the first 512 KB of 1.9 MB. Open it to see the rest."
    assert len(page.text.toPlainText()) == preview.TEXT_LIMIT
    assert page.text.lineWrapMode() == QPlainTextEdit.LineWrapMode.NoWrap  # lines of 99: columns stay aligned


def test_long_lines_are_wrapped(qtbot, panel, tmp_path):
    path = tmp_path / "one_line.txt"
    path.write_bytes(b"word " * 1000)
    page = show(qtbot, panel, path)
    assert page.text.lineWrapMode() == QPlainTextEdit.LineWrapMode.WidgetWidth


def test_images_fit_the_width_and_are_never_upscaled(qtbot, panel, tmp_path):
    big = tmp_path / "scan.png"
    save_image(big, 3000, 1500)
    page = show(qtbot, panel, big)
    assert page.result.pixels == (3000, 1500) and page.details.text().startswith("3000 × 1500 px · ")
    shown = page.image_label.pixmap()
    assert shown.width() <= page.image_area.viewport().width() and shown.width() * 2 == pytest.approx(
        shown.height() * 4, abs=4)
    small = tmp_path / "small.png"
    save_image(small, 50, 20)
    page = show(qtbot, panel, small)
    assert page.image_label.pixmap().size().toTuple() == (50, 20)


def test_exif_orientation_is_applied(qtbot, panel, tmp_path):
    path = tmp_path / "portrait.jpg"
    if not QImageWriter(str(path), b"jpeg").supportsOption(QImageIOHandler.ImageOption.ImageTransformation):
        pytest.skip("this Qt build doesn't write EXIF orientation")
    save_image(path, 80, 40, transformation=QImageIOHandler.Transformation.TransformationRotate90)
    page = show(qtbot, panel, path)
    assert page.result.pixels == (40, 80) and page.result.image.size().toTuple() == (40, 80)


def test_a_broken_image_says_why(qtbot, panel, tmp_path):
    path = tmp_path / "cover.jpg"
    path.write_bytes(b"jpg")  # as the UI fixtures' cover
    page = show(qtbot, panel, path)
    assert page.pages.currentWidget() is page.message
    assert page.message.text().startswith("Can't show this image: ")


def test_a_huge_image_is_not_decoded(qtbot, panel, tmp_path, monkeypatch):
    monkeypatch.setattr(preview, "IMAGE_LIMIT", 1000)
    path = tmp_path / "poster.png"
    save_image(path, 300, 300)
    path.write_bytes(path.read_bytes() + b"\0" * 2000)
    page = show(qtbot, panel, path)
    assert page.message.text() == "Too large to preview (0 MB). Open it instead."


def test_a_stale_read_is_ignored(qtbot, panel, fixtures_dir):
    folder = fixtures_dir / "preview"
    with qtbot.waitSignals([panel.io.previewRead, panel.io.previewRead], timeout=5000):
        panel.show_selection([SelectedItem(folder / "cp1252.txt", False, False)])
        panel.show_selection([SelectedItem(folder / "bom.txt", False, False)])
    assert panel.preview.name_label.text() == "bom.txt" and "déjà vu" in panel.preview.text.toPlainText()


def test_other_selections_keep_their_pages(qtbot, panel, fixtures_dir, tmp_path):
    show(qtbot, panel, fixtures_dir / "preview" / "eac.log")
    panel.show_selection([SelectedItem(fixtures_dir / "preview" / "eac.log", False, False),
                          SelectedItem(fixtures_dir / "preview" / "bom.txt", False, False)])
    assert panel.stack.currentWidget() is panel.tabs
    assert "2 items selected" in panel.metadata.message.text()
    panel.show_selection([SelectedItem(tmp_path / "booklet.pdf", False, False)])
    assert panel.stack.currentWidget() is panel.tabs and "isn't an audio file" in panel.metadata.message.text()
    panel.show_selection([SelectedItem(tmp_path, True, False)])
    assert panel.stack.currentWidget() is panel.tabs


# -- in the window: the selection, Open and the reload --

def test_selecting_a_log_in_the_list_previews_it_and_open_launches_it(qtbot, window, service, library_dir):
    launched = []
    window.launch = launched.append
    window.open_folder(entry(service, "Artist", "Album"))
    window.file_view.select_ids([entry(service, "Artist", "Album", "cover.jpg")])
    page = window.side_panel.preview
    qtbot.waitUntil(lambda: page.result is not None, timeout=5000)
    assert window.side_panel.stack.currentWidget() is page and page.message.text().startswith("Can't show")
    page.open_button.click()
    qtbot.waitUntil(lambda: launched == [str(library_dir / "Artist" / "Album" / "cover.jpg")], timeout=5000)
    window.file_view.select_ids([entry(service, "Artist", "Album", "Track 2.flac")])
    assert window.side_panel.stack.currentWidget() is window.side_panel.tabs  # audio keeps the tabs


def test_a_log_being_written_reloads(qtbot, window, service, library_dir, fixtures_dir):
    album = library_dir / "Artist" / "Album"
    log = album / "rip.log"
    shutil.copy2(fixtures_dir / "preview" / "cp1252.txt", log)
    window.library.indexer.request_path(str(album))
    qtbot.waitUntil(lambda: service.conn.execute(
        "SELECT 1 FROM entries WHERE name = 'rip.log'").fetchone() is not None, timeout=5000)
    window.open_folder(entry(service, "Artist", "Album"))
    window.file_view.select_ids([entry(service, "Artist", "Album", "rip.log")])
    page = window.side_panel.preview
    qtbot.waitUntil(lambda: "Noël" in page.text.toPlainText(), timeout=5000)
    with open(log, "ab") as f:
        f.write(b"Track 2 OK\r\n")
    os.utime(log, (time.time() + 5, time.time() + 5))
    window.library.indexer.request_path(str(album))
    qtbot.waitUntil(lambda: "Track 2 OK" in page.text.toPlainText(), timeout=10000)
    assert page.name_label.text() == "rip.log"
