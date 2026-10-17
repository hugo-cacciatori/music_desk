"""core/cue_edit.py's M13 additions (SPEC §22): the new sheet of New cue sheet…, and Fill from track lengths… (the
dialog's checks and the starts it sets)."""

from pathlib import Path

import pytest

from music_desk.core.cue_edit import (
    check_lengths,
    format_cue,
    format_length,
    new_document,
    parse_document,
    parse_length,
    validate,
)

SECOND = 75  # CD frames


def test_a_new_sheet_is_filled_from_the_tags():
    doc = new_document("Album.flac", "Album Artist", 'Say "Hi"')
    assert format_cue(doc) == (b'\xef\xbb\xbfPERFORMER "Album Artist"\r\nTITLE "Say \'Hi\'"\r\n'
                               b'FILE "Album.flac" WAVE\r\n  TRACK 01 AUDIO\r\n    INDEX 01 00:00:00\r\n')
    assert not doc.modified and doc.path is None and doc.encoding == "UTF-8 (BOM)"
    assert [p.message for p in validate(doc)] == ["A sheet needs at least two tracks to be split."]


def test_a_missing_tag_leaves_its_line_out():
    assert new_document("x.wav").text() == 'FILE "x.wav" WAVE\r\n  TRACK 01 AUDIO\r\n    INDEX 01 00:00:00\r\n'
    assert new_document("x.wav", performer="  ", title="Album").text().startswith('TITLE "Album"\r\nFILE')


def test_a_new_sheet_is_modified_only_once_changed():
    doc = new_document("x.flac", "A", "B")
    doc.set_album("title", "B")
    assert not doc.modified
    doc.set_album("genre", "Rock")
    assert doc.modified


@pytest.mark.parametrize(("text", "frames"), [
    ("4:12", 252 * SECOND), ("0:05", 5 * SECOND), ("12:00:74", 720 * SECOND + 74), (" 3:07 ", 187 * SECOND),
    ("3:60", None), ("3:07:75", None), ("3", None), ("", None), ("a:bc", None)])
def test_lengths_are_read_as_m_ss_or_m_ss_ff(text, frames):
    assert parse_length(text) == frames


def test_lengths_are_shown_as_a_tracklist_writes_them():
    assert [format_length(f) for f in (0, 252 * SECOND, 187 * SECOND + 5)] == ["0:00", "4:12", "3:07:05"]


def test_the_dialogs_checks():
    audio = 600 * SECOND
    assert check_lengths(["4:00", "3:00:10"], 2 * SECOND, audio) == ([240 * SECOND, 180 * SECOND + 10], "")
    assert check_lengths(["4:00", "x"], 0, audio) == (None, "Track 2's length isn't m:ss or m:ss:ff.")
    assert check_lengths(["0:00"], 0, audio) == (None, "Track 1 has no length.")
    assert check_lengths(["6:00", "4:00"], 0, audio) == (
        None, "The lengths before the last track reach 10:00, past the end of the audio (10:00).")
    assert check_lengths([], 0, audio) == ([], "")


SHEET = (
    'TITLE "Album"\r\nFILE "image.flac" WAVE\r\n'
    '  TRACK 01 AUDIO\r\n    TITLE "One"\r\n    INDEX 01 00:00:32\r\n'
    '  TRACK 02 AUDIO\r\n    TITLE "Two"\r\n    INDEX 00 03:58:00\r\n    INDEX 01 04:00:00\r\n'
    '  TRACK 03 AUDIO\r\n    TITLE "Three"\r\n    INDEX 01 07:00:00\r\n'
)


def doc_of(text=SHEET):
    return parse_document(text.encode("utf-8"), None, Path("C:/Music/image.cue"))


def starts(doc):
    return [(v.values["title"], v.values["gap"], v.values["start"]) for v in doc.tracks()]


def test_fill_sets_the_starts_and_removes_a_moved_gap():
    doc = doc_of()
    doc.fill_from_lengths([4 * 60 * SECOND, 3 * 60 * SECOND + 1])
    assert starts(doc) == [("One", "", "00:00:32"), ("Two", "", "04:00:32"), ("Three", "", "07:00:33")]


def test_fill_keeps_a_track_that_doesnt_move():
    doc = doc_of()
    doc.fill_from_lengths([4 * 60 * SECOND - 32, 3 * 60 * SECOND])
    assert starts(doc) == [("One", "", "00:00:32"), ("Two", "03:58:00", "04:00:00"), ("Three", "", "07:00:00")]
    assert not doc.modified


def test_fill_adds_and_deletes_tracks_at_the_end():
    doc = doc_of()
    doc.fill_from_lengths([60 * SECOND, 60 * SECOND, 60 * SECOND, 30 * SECOND])
    assert starts(doc)[2:] == [("Three", "", "02:00:32"), ("", "", "03:00:32"), ("", "", "03:30:32")]
    assert [v.number for v in doc.tracks()] == [1, 2, 3, 4, 5]
    assert doc.text().endswith('  TRACK 04 AUDIO\r\n    INDEX 01 03:00:32\r\n'
                               '  TRACK 05 AUDIO\r\n    INDEX 01 03:30:32\r\n')
    doc.fill_from_lengths([10 * SECOND])
    assert starts(doc) == [("One", "", "00:00:32"), ("Two", "", "00:10:32")]


def test_fill_needs_a_valid_first_start():
    from music_desk.core.cue import CueError

    doc = doc_of(SHEET.replace("00:00:32", "0:x"))
    with pytest.raises(CueError, match="Track 1's start isn't valid"):
        doc.fill_from_lengths([SECOND])
