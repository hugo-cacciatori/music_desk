"""core/cue_edit.py: the Split page's editable sheet (SPEC §22): the byte-identical round trip, edits that touch
only their own lines, add, delete and renumber, live validation, the encoding question, and Save / Save as through
the (injected) Recycle Bin."""

from pathlib import Path

import pytest

from music_desk.core import cue
from music_desk.core.cue_edit import (
    CueEncodingError,
    format_cue,
    format_time,
    parse_document,
    read_document,
    save_cue,
    validate,
)
from music_desk.core.fsops import CannotRecycle

SHEET = (
    'REM GENRE "Alternative Rock"\r\n'
    "REM DATE 1997\r\n"
    "REM DISCID 8C0B6A0B\r\n"
    'REM COMMENT "ExactAudioCopy v1.6"\r\n'
    'PERFORMER "Fixture Artist"\r\n'
    'TITLE "Fixture Album"\r\n'
    'FILE "image.flac" WAVE\r\n'
    "  TRACK 01 AUDIO\r\n"
    '    TITLE "Opening"\r\n'
    '    SONGWRITER "A. Writer"\r\n'
    "    ISRC GBAYE9700001\r\n"
    "    INDEX 01 00:00:00\r\n"
    "  TRACK 02 AUDIO\r\n"
    '    TITLE "Second Song"\r\n'
    "    FLAGS DCP\r\n"
    "    INDEX 00 04:41:50\r\n"
    "    INDEX 01 04:43:37\r\n"
    "  TRACK 03 AUDIO\r\n"
    '    TITLE "Duet"\r\n'
    '    PERFORMER "Fixture Artist & Guest"\r\n'
    "    REM COMPOSER x\r\n"
    "    INDEX 01 09:01:05\r\n"
)


def doc(text=SHEET, encoding=None):
    return parse_document(text.encode("utf-8"), encoding, Path("C:/Music/Album/image.cue"))


def lines(document) -> list[str]:
    return document.text().split("\r\n")


def changed(before: str, document) -> list[tuple[str, str]]:
    """The lines that differ, with difflib's view of what was removed and added."""
    import difflib

    diff = difflib.ndiff(before.split("\r\n"), lines(document))
    return [(d[0], d[2:]) for d in diff if d[0] in "+-"]


# -- the round trip --


def fixture_sheets(fixtures_dir) -> list[Path]:
    return sorted((fixtures_dir / "split").rglob("*.cue"))


def test_every_fixture_sheet_saved_unedited_is_byte_identical(fixtures_dir):
    sheets = fixture_sheets(fixtures_dir)
    assert len(sheets) >= 10
    for path in sheets:
        data = path.read_bytes()
        for encoding in (None, "utf-8", "cp1252", "shift_jis"):
            document = read_document(path, encoding)
            assert format_cue(document) == data, (path.name, encoding)
            assert not document.modified


@pytest.mark.parametrize("data", [
    SHEET.replace("\r\n", "\n").encode(),  # LF
    SHEET.replace("\r\n", "\r").encode(),  # old Mac
    SHEET.encode()[:-2],  # no final line ending
    b"\xef\xbb\xbf" + SHEET.encode(),  # UTF-8 with a BOM
    "TITLE \"Caf\u00e9 \u2013 \x81\"\r\n".encode("cp1252", errors="ignore") + b"\x81\x8d\r\n",  # bytes cp1252 lacks
    "\ufeffTITLE \"\u65e5\u672c\"\r\n".encode("utf-16-le"),  # UTF-16 with its BOM
    b"",
    b"\r\n\r\n  \r\n",
])
def test_odd_files_round_trip(data):
    assert format_cue(parse_document(data)) == data


def test_auto_encoding_and_the_sheet(fixtures_dir):
    split = fixtures_dir / "split" / "sheets"
    assert read_document(split / "bom.cue").encoding == "UTF-8 (BOM)"
    cp1252 = read_document(split / "cp1252.cue")
    assert cp1252.encoding == "Windows-1252" and cp1252.fell_back
    assert read_document(split / "sjis.cue", "shift_jis").encoding == "Shift-JIS (Japanese)"
    for path in fixture_sheets(fixtures_dir):
        if path.parent.name == "multi_file":
            continue
        sheet, document = cue.read_cue_sheet(path), read_document(path)  # undecodable bytes show as \ufffd
        parsed = document.sheet()
        assert (parsed.title, parsed.performer, parsed.files, parsed.data_tracks) == (
            sheet.title, sheet.performer, sheet.files, sheet.data_tracks)
        assert [(t.number, t.title, t.index00, t.index01) for t in parsed.tracks] == [
            (t.number, t.title, t.index00, t.index01) for t in sheet.tracks]


# -- reading the fields --


def test_album_and_tracks_as_the_page_shows_them():
    document = doc()
    album = document.album()
    assert (album["performer"], album["title"], album["genre"], album["date"]) == (
        "Fixture Artist", "Fixture Album", "Alternative Rock", "1997")
    assert album["other"] == 'REM DISCID 8C0B6A0B\nREM COMMENT "ExactAudioCopy v1.6"'
    assert document.file == "image.flac" and document.file_count == 1
    tracks = document.tracks()
    assert [t.number for t in tracks] == [1, 2, 3]
    assert tracks[0].values == {"title": "Opening", "performer": "", "songwriter": "A. Writer",
                                "isrc": "GBAYE9700001", "flags": "", "gap": "", "start": "00:00:00"}
    assert (tracks[1].values["gap"], tracks[1].values["flags"]) == ("04:41:50", "DCP")
    assert tracks[2].other == "    REM COMPOSER x"


# -- edits change only their own lines --


def test_each_edit_changes_only_its_line():
    document = doc()
    document.set_track(1, "title", "Second")
    assert changed(SHEET, document) == [("-", '    TITLE "Second Song"'), ("+", '    TITLE "Second"')]
    document = doc()
    document.set_album("date", "1998")
    assert changed(SHEET, document) == [("-", "REM DATE 1997"), ("+", "REM DATE 1998")]
    document = doc()
    document.set_file("image.wav")
    assert changed(SHEET, document) == [("-", 'FILE "image.flac" WAVE'), ("+", 'FILE "image.wav" WAVE')]
    document = doc()
    document.set_track(0, "start", "0:0:0")  # the same time, typed differently: unchanged
    document.set_track(2, "performer", "Fixture Artist & Guest")
    document.set_album_other('REM DISCID 8C0B6A0B\nREM COMMENT "ExactAudioCopy v1.6"')
    assert format_cue(document) == SHEET.encode() and not document.modified


def test_new_lines_go_where_eac_puts_them():
    document = doc()
    document.set_track(1, "isrc", "GBAYE9700002")  # after TITLE, before FLAGS
    document.set_track(2, "songwriter", "B. Writer")  # after PERFORMER
    document.set_album("disc", "1")  # after the last REM
    document.set_album("catalog", "0123456789012")  # before FILE
    assert changed(SHEET, document) == [
        ("+", "REM DISCNUMBER 1"), ("+", "CATALOG 0123456789012"), ("+", "    ISRC GBAYE9700002"),
        ("+", '    SONGWRITER "B. Writer"')]
    text = lines(document)
    assert text.index("REM DISCNUMBER 1") == text.index('REM COMMENT "ExactAudioCopy v1.6"') + 1
    assert text.index("CATALOG 0123456789012") == text.index('FILE "image.flac" WAVE') - 1
    assert text.index("    ISRC GBAYE9700002") == text.index('    TITLE "Second Song"') + 1
    assert text.index('    SONGWRITER "B. Writer"') == text.index('    PERFORMER "Fixture Artist & Guest"') + 1


def test_clearing_a_value_removes_its_line_and_times_are_normalized():
    document = doc()
    document.set_track(1, "gap", "")
    document.set_album("genre", "  ")
    assert changed(SHEET, document) == [("-", 'REM GENRE "Alternative Rock"'), ("-", "    INDEX 00 04:41:50")]
    document.set_track(2, "start", "9:1:6")
    assert "    INDEX 01 09:01:06" in lines(document)
    document.set_track(2, "start", "nine")  # kept as typed, for validation to mark
    assert "    INDEX 01 nine" in lines(document)
    assert format_time(75 * 61 * 60 + 4) == "61:00:04"


def test_other_lines():
    document = doc()
    document.set_track_other(2, "REM COMPOSER y\nPREGAP 00:02:00")
    assert changed(SHEET, document) == [("-", "    REM COMPOSER x"), ("+", "    REM COMPOSER y"),
                                        ("+", "    PREGAP 00:02:00")]
    document.set_album_other("")
    assert "REM DISCID 8C0B6A0B" not in lines(document) and document.album()["other"] == ""
    document.set_track_other(0, "INDEX 02 00:01:00")
    assert document.tracks()[0].other == "    INDEX 02 00:01:00"


def test_new_lines_keep_a_file_without_a_final_line_ending_so():
    document = parse_document(SHEET.replace("\r\n", "\n").encode()[:-1])
    document.add_track(2, audio_frames=(9 * 60 + 1) * 75 + 7)
    assert document.text().endswith("    INDEX 01 09:01:05\n  TRACK 04 AUDIO\n    INDEX 01 09:01:06")
    document.set_track_other(2, "REM COMPOSER x\nREM X y")  # other lines stay where they were
    assert "    REM COMPOSER x\n    REM X y\n    INDEX 01 09:01:05\n" in document.text()


# -- add, delete, renumber --


def test_add_a_track_halfway_and_renumber():
    document = doc()
    assert document.add_track(0) == 1
    tracks = document.tracks()
    assert [t.number for t in tracks] == [1, 2, 3, 4]
    assert tracks[1].values["start"] == format_time((4 * 60 + 43) * 75 + 37 >> 1)
    assert "  TRACK 02 AUDIO" in lines(document) and "  TRACK 04 AUDIO" in lines(document)
    assert document.add_track(3, audio_frames=10 * 60 * 75) == 4
    assert document.tracks()[4].values["start"] == format_time(((9 * 60 + 1) * 75 + 5 + 10 * 60 * 75) // 2)
    with pytest.raises(cue.CueError):
        document.add_track(4)  # the last track: the audio's end isn't known


def test_delete_tracks():
    document = doc()
    document.delete_tracks([1])  # its audio joins track 1 (nothing to rewrite: track 1 runs to track 3's start)
    assert [(t.number, t.values["title"]) for t in document.tracks()] == [(1, "Opening"), (2, "Duet")]
    assert "  TRACK 02 AUDIO" in lines(document) and "  TRACK 03 AUDIO" not in lines(document)

    document = doc()
    document.delete_tracks([0])  # track 1's audio joins the next one, which takes its start and drops its gap
    first = document.tracks()[0]
    assert (first.number, first.values["title"], first.values["start"], first.values["gap"]) == (
        1, "Second Song", "00:00:00", "")


# -- validation --


def problems(document, info=None):
    return {(p.row, p.column, p.message) for p in validate(document, info)}


def test_a_good_sheet_has_no_problem():
    assert validate(doc(), cue.SourceInfo("FLAC", 44100, 44100 * 600, 2, 16)) == []


def test_validation_marks_each_bad_cell():
    document = doc()
    document.set_track(0, "start", "0:0:99")
    document.set_track(1, "gap", "05:00:00")
    document.set_track(2, "start", "01:00:00")
    document.set_track(2, "title", 'A "quoted" title')
    document.set_album("disc", "one")
    found = problems(document)
    assert (0, "start", "'0:0:99' isn't a cue time (mm:ss:ff).") in found
    assert (1, "gap", "Track 2's gap (INDEX 00) must come before its start.") in found
    assert (2, "start", "Track 3 must start after track 2.") in found
    assert (2, "title", 'A cue value can\'t contain a double quote (").') in found
    assert (None, "disc", "'one' isn't a number.") in found
    assert all(p.blocks_save for p in validate(document))


def test_validation_against_the_audio_and_the_track_count():
    short = cue.SourceInfo("FLAC", 44100, 44100 * 60 * 5, 2, 16)  # 5 minutes
    assert problems(doc(), short) == {(2, "start", "Track 3 starts after the end of the audio (09:01:05, the "
                                                   "audio lasts 05:00:00).")}
    document = doc()
    document.delete_tracks([1, 2])
    assert (None, "", "A sheet needs at least two tracks to be split.") in problems(document)
    document.set_track(0, "start", "")
    assert (0, "start", "Track 1 has no start time (INDEX 01).") in problems(document)


# -- encodings --


def test_a_character_the_encoding_cant_hold(fixtures_dir):
    document = read_document(fixtures_dir / "split" / "sheets" / "cp1252.cue")
    document.set_track(0, "title", "\u65e5\u672c \u00e9")
    with pytest.raises(CueEncodingError) as caught:
        format_cue(document)
    assert caught.value.characters == ["\u65e5", "\u672c"] and caught.value.encoding == "Windows-1252"
    document.use_utf8_bom()
    data = format_cue(document)
    assert data.startswith(b"\xef\xbb\xbf") and "\u65e5\u672c \u00e9".encode() in data
    assert parse_document(data).tracks()[0].values["title"] == "\u65e5\u672c \u00e9"


# -- Save and Save as --


class Bin:
    def __init__(self, refuse: str | None = None) -> None:
        self.recycled: list[tuple[Path, bytes]] = []
        self.refuse = refuse

    def check(self, path):
        return self.refuse

    def recycle(self, path):
        self.recycled.append((path, path.read_bytes()))
        path.unlink()  # where the real bin would take it


@pytest.fixture
def sheet_file(tmp_path):
    path = tmp_path / "Album" / "image.cue"
    path.parent.mkdir()
    path.write_bytes(SHEET.encode())
    return path


def test_save_recycles_the_old_version(sheet_file):
    document = read_document(sheet_file)
    document.set_track(1, "title", "Second")
    assert document.modified
    bin_ = Bin()
    save_cue(document, sheet_file, bin_.recycle, bin_.check)
    assert bin_.recycled == [(sheet_file, SHEET.encode())]
    assert sheet_file.read_bytes() == SHEET.replace("Second Song", "Second").encode()
    assert not document.modified and sorted(p.name for p in sheet_file.parent.iterdir()) == ["image.cue"]


def test_save_is_refused_where_windows_cant_recycle(sheet_file):
    document = read_document(sheet_file)
    document.set_track(1, "title", "Second")
    bin_ = Bin(refuse="D:\\ is a network location, which has no Recycle Bin")
    with pytest.raises(CannotRecycle, match="network location"):
        save_cue(document, sheet_file, bin_.recycle, bin_.check)
    assert sheet_file.read_bytes() == SHEET.encode() and not bin_.recycled
    assert sorted(p.name for p in sheet_file.parent.iterdir()) == ["image.cue"] and document.modified


def test_save_as_a_new_file_and_over_another(sheet_file):
    document = read_document(sheet_file)
    document.set_album("date", "2001")
    bin_ = Bin()
    other = sheet_file.with_name("edited.cue")
    save_cue(document, other, bin_.recycle, bin_.check)
    assert not bin_.recycled and sheet_file.read_bytes() == SHEET.encode()
    assert document.path == other and b"REM DATE 2001" in other.read_bytes()

    replaced = sheet_file.with_name("old.cue")
    replaced.write_bytes(b"old")
    save_cue(document, replaced, bin_.recycle, bin_.check)
    assert bin_.recycled == [(replaced, b"old")] and document.path == replaced


def test_a_failed_recycle_keeps_the_old_sheet(sheet_file):
    document = read_document(sheet_file)
    document.set_track(1, "title", "Second")

    def fail(path):
        raise CannotRecycle("it went wrong")

    with pytest.raises(OSError, match="The new version stays in image.cue.partial, and the old one wasn't "
                                      "replaced"):
        save_cue(document, sheet_file, fail, lambda _p: None)
    assert sheet_file.read_bytes() == SHEET.encode()
    # Nothing is deleted for good (Ground rule 1): the new version stays beside it, and the error names it.
    assert sorted(p.name for p in sheet_file.parent.iterdir()) == ["image.cue", "image.cue.partial"]


def test_a_leftover_partial_is_never_overwritten(sheet_file):
    leftover = sheet_file.with_name("image.cue.partial")
    leftover.write_bytes(b"someone's")
    document = read_document(sheet_file)
    with pytest.raises(FileExistsError):
        save_cue(document, sheet_file, Bin().recycle, Bin().check)
    assert leftover.read_bytes() == b"someone's" and sheet_file.read_bytes() == SHEET.encode()


def test_an_encoding_problem_writes_nothing(sheet_file):
    sheet_file.write_bytes(SHEET.encode("cp1252"))
    document = read_document(sheet_file, "cp1252")
    document.set_track(0, "title", "\u65e5")
    bin_ = Bin()
    with pytest.raises(CueEncodingError):
        save_cue(document, sheet_file, bin_.recycle, bin_.check)
    assert not bin_.recycled and sorted(p.name for p in sheet_file.parent.iterdir()) == ["image.cue"]
