"""core/preview.py: which files are previewed, and text in the encodings album folders hold (SPEC §20)."""

import pytest

from refrain.core import preview

PREVIEW = "preview"


@pytest.fixture
def preview_dir(fixtures_dir):
    return fixtures_dir / PREVIEW


def test_kinds():
    for name in ("rip.log", "album.CUE", "info.nfo", "a.sfv", "a.md5", "a.ffp", "a.accurip", "a.m3u", "a.m3u8",
                 "a.lrc", "notes.txt"):
        assert preview.kind(name) == "text", name
    for name in ("cover.jpg", "cover.JPEG", "a.png", "a.gif", "a.bmp", "a.webp"):
        assert preview.kind(name) == "image", name
    for name in ("booklet.pdf", "01.flac", "noext"):
        assert preview.kind(name) is None, name
    assert preview.PREVIEW_TYPES == preview.TEXT_TYPES | preview.IMAGE_TYPES


def test_an_eac_log_in_utf16(preview_dir):
    text = preview.read_text(preview_dir / "eac.log")
    assert text.encoding == "UTF-16 LE" and not text.truncated
    assert text.text.startswith("Exact Audio Copy V1.6")  # the BOM is dropped
    assert "Fixture Artist / Café Album" in text.text and "01 Première.wav" in text.text
    assert "\r" not in text.text and text.size == (preview_dir / "eac.log").stat().st_size


@pytest.mark.parametrize(("name", "encoding", "expected"), [
    ("bom.txt", "UTF-8 (BOM)", "Notes with a BOM: déjà vu\n"),
    ("utf8.cue", "UTF-8", 'PERFORMER "Fixture Artist"\nTITLE "Café Album"\n'),
    ("cp1252.txt", "Windows-1252", "Café à la crème — Noël\n"),
    ("utf16.lrc", "UTF-16 LE", "[00:01.00]Première ligne\n"),
])
def test_text_encodings(preview_dir, name, encoding, expected):
    text = preview.read_text(preview_dir / name)
    assert (text.text, text.encoding) == (expected, encoding)


def test_an_nfo_reads_as_dos_text(preview_dir):
    text = preview.read_text(preview_dir / "release.nfo")
    assert text.encoding == "CP437 (DOS)"
    assert text.text.splitlines()[0] == "╔" + "═" * 10 + "╗"  # the box drawing, not Windows-1252
    assert "Release : Café Album" in text.text and "░▒▓█" in text.text


def test_the_cap(tmp_path):
    path = tmp_path / "big.log"
    path.write_bytes(b"0123456789\r\n" * 1000)
    text = preview.read_text(path, limit=120)
    assert text.truncated and text.size == 12000 and text.text == "0123456789\n" * 10
    whole = preview.read_text(path, limit=12000)
    assert not whole.truncated and whole.text.count("\n") == 1000


def test_long_lines_wrap_and_giant_ones_are_cut(tmp_path):
    """Measured in M9: lines over WRAP_FROM are slow without word wrap, one over BREAK_AT is slow even with it."""
    short = tmp_path / "short.log"
    short.write_text(("x" * preview.WRAP_FROM + "\n") * 3, encoding="utf-8")
    assert not preview.read_text(short).wrap
    long = tmp_path / "long.txt"
    long.write_text("a" * (preview.WRAP_FROM + 1) + "\nb\n", encoding="utf-8")
    text = preview.read_text(long)
    assert text.wrap and text.text == "a" * (preview.WRAP_FROM + 1) + "\nb\n"  # wrapped, not cut
    giant = tmp_path / "giant.txt"
    giant.write_text("c" * (2 * preview.BREAK_AT + 5) + "\n\nd", encoding="utf-8")
    text = preview.read_text(giant)
    assert text.wrap and text.text.split("\n") == ["c" * preview.BREAK_AT, "c" * preview.BREAK_AT, "c" * 5, "",
                                                   "d"]


def test_a_cut_inside_a_character_is_never_an_error(tmp_path):
    utf8 = tmp_path / "a.txt"
    utf8.write_bytes("ééé".encode())  # 6 bytes; a cut at 5 splits the last é
    text = preview.read_text(utf8, limit=5)
    assert (text.text, text.encoding, text.truncated) == ("éé�", "UTF-8", True)
    utf16 = tmp_path / "b.log"
    utf16.write_bytes(b"\xff\xfe" + "abc".encode("utf-16-le"))
    text = preview.read_text(utf16, limit=5)  # the BOM, "a", and half of "b"
    assert text.text == "a�" and text.encoding == "UTF-16 LE"


def test_a_whole_cp1252_file_ending_like_a_utf8_start_stays_cp1252(tmp_path):
    path = tmp_path / "a.txt"
    path.write_bytes("Café".encode("cp1252"))  # ends with 0xE9, a UTF-8 lead byte
    text = preview.read_text(path)
    assert (text.text, text.encoding) == ("Café", "Windows-1252")


def test_undecodable_bytes_become_replacement_characters():
    assert preview.decode(b"caf\xe9 \x81") == ("café �", "Windows-1252")  # 0x81 is unassigned in cp1252
    assert preview.decode(b"\xff\xfe\x00") == ("�", "UTF-16 LE")


def test_reading_errors_raise_os_error(tmp_path):
    with pytest.raises(OSError):
        preview.read_text(tmp_path / "missing.txt")


def test_an_lrc_reads_utf16_too(preview_dir):
    assert preview.decode((preview_dir / "utf16.lrc").read_bytes())[0] == "[00:01.00]Première ligne\r\n"
    assert preview.decode("Café".encode("cp1252"))[0] == "Café"
