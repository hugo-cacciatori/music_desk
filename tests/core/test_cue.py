"""core/cue.py: cue sheets, the source audio and the split plan (SPEC §22). Nothing is written: the plan only
says what the job would do."""

import shutil
import struct

import pytest

from music_desk.core import cue
from music_desk.core.masks import DEFAULT_MASKS

SPLIT = "split"


@pytest.fixture
def split_dir(fixtures_dir):
    return fixtures_dir / SPLIT


def plan(path, masks=DEFAULT_MASKS, **kwargs):
    return cue.plan_album(path, masks, "-", **kwargs)


def bounds(album_plan):
    return [(t.number, t.start, t.end) for t in album_plan.tracks]


# -- the sheet --

def test_hugos_eac_sheet(split_dir):
    """The kind of sheet the split is for (Hugo's, 2026-10-16)."""
    sheet = cue.read_cue_sheet(split_dir / "sheets" / "eac_example.cue")
    assert (sheet.performer, sheet.title, sheet.date, sheet.genre) == (
        "Gloryhammer", "Space 1993: Wrath of Kor-Virliath", "2026", "")
    assert sheet.files == ["Gloryhammer - Space 1993- Wrath of Kor-Virliath.flac"]
    assert [t.number for t in sheet.tracks] == list(range(1, 12)) and sheet.encoding == "UTF-8"
    silver = sheet.tracks[4]
    assert silver.title == "Silver Talon" and silver.performer == "Gloryhammer"
    assert silver.index00 == cue.frames_from_time("13:22:51") and silver.index01 == cue.frames_from_time("13:23:19")
    assert sheet.tracks[-1].title.endswith("(Final Assault on Battlemoon X-22)")


def test_hugos_sheet_plans_eleven_tracks_with_gaps_kept_before(split_dir, tmp_path):
    album = tmp_path / "Gloryhammer - Space 1993"
    album.mkdir()
    shutil.copy2(split_dir / "sheets" / "eac_example.cue", album / "album.cue")
    (album / "Gloryhammer - Space 1993- Wrath of Kor-Virliath.flac").write_bytes(b"")
    info = cue.SourceInfo("FLAC", 44100, 44100 * 60 * 41, 2, 16)
    result = plan(album / "album.cue", read_info=lambda _p: info)
    assert result.refusal is None and len(result.tracks) == 11
    assert result.tracks[3].end == result.tracks[4].start == 60244 * 588  # 13:23:19: the gap stays in track 4
    assert result.tracks[0].start == 0 and result.tracks[-1].end == info.samples
    assert all(a.end == b.start for a, b in zip(result.tracks, result.tracks[1:], strict=False))
    assert result.tracks[4].name == "05 Silver Talon.flac"
    assert result.tracks[1].fields == {"track": 2, "track_total": 11, "title": "Ultimate Sacrifice",
                                       "artist": "Gloryhammer", "albumartist": "Gloryhammer",
                                       "album": "Space 1993: Wrath of Kor-Virliath", "year": "2026"}


def test_album_and_track_fields(split_dir):
    sheet = cue.read_cue_sheet(split_dir / "hidden_track" / "Split Album.cue")
    assert (sheet.genre, sheet.date, sheet.disc, sheet.disc_total) == ("Test Rock", "2024", 1, 2)
    one, two, three = sheet.tracks
    assert (one.songwriter, one.isrc) == ("A. Writer", "GBAYE2400001")
    assert two.index00 == 6 * 75 and two.index01 == 6 * 75 + 37
    assert three.performer == "Fixture Artist & Guest"


@pytest.mark.parametrize(("name", "encoding", "fell_back"), [
    ("cp1252.cue", "Windows-1252", True),
    ("bom.cue", "UTF-8 (BOM)", False),
])
def test_auto_encoding(split_dir, name, encoding, fell_back):
    sheet = cue.read_cue_sheet(split_dir / "sheets" / name)
    assert (sheet.performer, sheet.title, sheet.tracks[0].title) == ("Björk", "Début", "Café Noël")
    assert (sheet.encoding, sheet.fell_back) == (encoding, fell_back)


def test_a_chosen_encoding(split_dir):
    path = split_dir / "sheets" / "sjis.cue"
    auto = cue.read_cue_sheet(path)
    assert auto.fell_back and auto.performer != "宇多田ヒカル"  # garbled: the picker is there for this
    chosen = cue.read_cue_sheet(path, "shift_jis")
    assert (chosen.performer, chosen.tracks[1].title, chosen.encoding) == (
        "宇多田ヒカル", "花束を君に", "Shift-JIS (Japanese)")
    assert cue.read_cue_sheet(split_dir / "sheets" / "bom.cue", "utf-8").performer == "Björk"  # BOM dropped


def test_times():
    assert cue.frames_from_time("01:37:26") == (60 + 37) * 75 + 26
    for bad in ("1:2", "00:60:00", "00:00:75", "aa:bb:cc"):
        with pytest.raises(cue.CueError):
            cue.frames_from_time(bad)
    assert cue.samples_from_frames(1, 44100) == 588 and cue.samples_from_frames(1, 48000) == 640
    assert cue.samples_from_frames(75, 96000) == 96000


def test_parsing_details():
    sheet = cue.parse_cue('FILE album.wav WAVE\nTRACK 01 AUDIO\nTITLE plain words\nINDEX 01 00:00:00\n'
                          'track 02 audio\n  title "Two"\n  index 01 00:01:00\nGARBAGE line\n')
    assert sheet.files == ["album.wav"] and [t.title for t in sheet.tracks] == ["plain words", "Two"]
    with pytest.raises(cue.CueError, match="no INDEX 01"):
        cue.parse_cue('FILE "a.flac" WAVE\n  TRACK 01 AUDIO\n    INDEX 00 00:00:00\n')


# -- the source --

def test_read_source(split_dir, fixtures_dir):
    flac = cue.read_source(split_dir / "hidden_track" / "Fixture Artist - Split Album.flac")
    assert (flac.format, flac.rate, flac.samples, flac.channels, flac.bits) == ("FLAC", 44100, 12 * 44100, 1, 16)
    assert flac.md5 and len(flac.md5) == 32 and flac.cover
    assert flac.tags == {"album": "Source Album", "albumartist": "Source Artist", "year": "1999"}
    wav = cue.read_source(split_dir / "wav_image" / "image.wav")
    assert (wav.format, wav.rate, wav.samples, wav.md5) == ("WAV", 44100, 6 * 44100, None)
    aiff = cue.read_source(fixtures_dir / "formats" / "tone.aiff")
    assert (aiff.format, aiff.rate, aiff.samples, aiff.bits) == ("AIFF", 44100, 44100, 16)


def test_unsupported_sources_are_named(tmp_path):
    with pytest.raises(cue.CueError, match=r"^APE isn't supported: the split reads FLAC, WAV and AIFF\.$"):
        cue.read_source(tmp_path / "x.ape")
    fmt = struct.pack("<HHIIHH", 3, 2, 44100, 44100 * 8, 8, 32)  # IEEE float
    float_wav = tmp_path / "float.wav"
    float_wav.write_bytes(b"RIFF" + struct.pack("<I", 4 + 8 + 16 + 8 + 8) + b"WAVEfmt " + struct.pack("<I", 16)
                          + fmt + b"data" + struct.pack("<I", 8) + b"\0" * 8)
    with pytest.raises(cue.CueError, match="32-bit float WAV isn't supported"):
        cue.read_source(float_wav)


# -- boundaries --

def test_an_audible_hidden_track_becomes_track_0(split_dir):
    result = plan(split_dir / "hidden_track" / "Split Album.cue")
    rate = 44100
    assert result.refusal is None
    assert bounds(result) == [(0, 0, 3 * rate), (1, 3 * rate, 6 * rate + 37 * 588), (2, 6 * rate + 37 * 588, 9 * rate),
                              (3, 9 * rate, 12 * rate)]  # the INDEX 00 gap stays at the end of track 1
    assert [t.name for t in result.tracks] == ["00 Hidden track.flac", "01 Opening.flac", "02 Second Song.flac",
                                               "03 Duet.flac"]


def test_a_silent_pregap_joins_track_1(split_dir):
    path = split_dir / "silent_pregap" / "image.cue"
    asked = []
    result = plan(path, is_silent=lambda source, start, end: asked.append((source.name, start, end)) or True)
    assert asked == [("image.flac", 0, 3 * 44100)]
    assert bounds(result) == [(1, 0, 6 * 44100), (2, 6 * 44100, 9 * 44100)]
    # Not silent: kept as a hidden track. Without a silence check, 2 s or more is kept too (nothing is lost).
    assert plan(path, is_silent=lambda *_: False).tracks[0].number == 0
    assert plan(path).tracks[0].number == 0


def test_a_short_pregap_joins_track_1_without_a_check(tmp_path):
    (tmp_path / "a.flac").write_bytes(b"")
    (tmp_path / "a.cue").write_text('FILE "a.flac" WAVE\n  TRACK 01 AUDIO\n    INDEX 01 00:01:00\n'
                                    '  TRACK 02 AUDIO\n    INDEX 01 00:05:00\n', encoding="utf-8")
    info = cue.SourceInfo("FLAC", 44100, 10 * 44100, 2, 16)
    result = plan(tmp_path / "a.cue", masks=("{track:02}",), read_info=lambda _p: info,
                  is_silent=lambda *_: pytest.fail("under 2 s needs no check"))
    assert bounds(result) == [(1, 0, 5 * 44100), (2, 5 * 44100, 10 * 44100)]


def test_a_wav_image_and_a_data_track(split_dir):
    wav = plan(split_dir / "wav_image" / "image.cue")
    assert wav.refusal is None and bounds(wav) == [(1, 0, 3 * 44100), (2, 3 * 44100, 6 * 44100)]
    data = plan(split_dir / "data_track" / "image.cue")
    assert data.refusal is None and len(data.tracks) == 2 and data.warnings == ["1 data track skipped."]


# -- refusals --

@pytest.mark.parametrize(("folder", "refusal"), [
    ("too_long", "Track 2 starts after the end of the audio (0:08.00 of 0:06.00)."),
    ("multi_file", "Not a single-file album: the sheet names several files."),
    ("name_clash", "Nothing is ever overwritten, and these names clash: 02 Two.flac (track 2) already exists."),
])
def test_refusals(split_dir, folder, refusal):
    cue_path = next((split_dir / folder).glob("*.cue"))
    assert plan(cue_path).refusal == refusal


def test_an_ape_source_is_refused_by_name(tmp_path):
    (tmp_path / "image.ape").write_bytes(b"MAC ")  # made here: a fake APE fixture would break the tag parity test
    (tmp_path / "image.cue").write_text('FILE "image.ape" WAVE\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n'
                                        '  TRACK 02 AUDIO\n    INDEX 01 00:01:00\n', encoding="utf-8")
    assert plan(tmp_path / "image.cue").refusal == "APE isn't supported: the split reads FLAC, WAV and AIFF."


def test_more_refusals(tmp_path):
    info = cue.SourceInfo("FLAC", 44100, 10 * 44100, 2, 16)

    def sheet(text, masks=DEFAULT_MASKS):
        (tmp_path / "a.cue").write_text(text, encoding="utf-8")
        return plan(tmp_path / "a.cue", masks=masks, read_info=lambda _p: info).refusal

    head = 'FILE "a.flac" WAVE\n'
    assert sheet(head + '  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n').startswith("Not a single-file album: a.flac")
    (tmp_path / "a.flac").write_bytes(b"")
    assert sheet(head + '  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n') == "The sheet has fewer than two audio tracks."
    two = head + '  TRACK 01 AUDIO\n    TITLE "One"\n    INDEX 01 00:02:00\n' \
                 '  TRACK 02 AUDIO\n    TITLE "Two"\n    INDEX 01 00:01:00\n'
    assert sheet(two) == "Track 2 doesn't start after track 1: the times must increase."
    no_title = head + '  TRACK 01 AUDIO\n    TITLE "One"\n    INDEX 01 00:00:00\n' \
                      '  TRACK 02 AUDIO\n    INDEX 01 00:01:00\n'
    assert sheet(no_title, masks=("{track:02} {title}",)) == "Track 2 fits no file name mask (missing: title)."
    assert sheet("not a cue sheet\n").startswith("Not a single-file album: the sheet names no file")


def test_two_tracks_given_one_name(tmp_path):
    (tmp_path / "a.flac").write_bytes(b"")
    (tmp_path / "a.cue").write_text('FILE "a.flac" WAVE\n  TRACK 01 AUDIO\n    TITLE "Same"\n    INDEX 01 00:00:00\n'
                                    '  TRACK 02 AUDIO\n    TITLE "Same"\n    INDEX 01 00:01:00\n', encoding="utf-8")
    info = cue.SourceInfo("FLAC", 44100, 10 * 44100, 2, 16)
    refusal = plan(tmp_path / "a.cue", masks=("{title}",), read_info=lambda _p: info).refusal
    assert refusal == ("Nothing is ever overwritten, and these names clash: tracks 1 and 2 would both be named "
                       "Same.flac.")


def test_a_path_too_long_for_windows(tmp_path):
    album = tmp_path / ("x" * (cue.MAX_PATH - len(str(tmp_path)) - 20))
    album.mkdir()
    (album / "a.flac").write_bytes(b"")
    (album / "a.cue").write_text('FILE "a.flac" WAVE\n  TRACK 01 AUDIO\n    TITLE "A rather long title"\n'
                                 '    INDEX 01 00:00:00\n  TRACK 02 AUDIO\n    TITLE "B"\n    INDEX 01 00:01:00\n',
                                 encoding="utf-8")
    info = cue.SourceInfo("FLAC", 44100, 10 * 44100, 2, 16)
    refusal = plan(album / "a.cue", read_info=lambda _p: info).refusal
    assert refusal.startswith("Track 1's file would have a path of ") and "Shorten the masks" in refusal


# -- tags --

def test_track_fields_fall_back_to_the_source_tags(split_dir):
    result = plan(split_dir / "hidden_track" / "Split Album.cue")
    hidden, opening, _second, duet = result.tracks
    assert opening.fields == {"track": 1, "track_total": 3, "disc": 1, "disc_total": 2, "title": "Opening",
                              "artist": "Fixture Artist", "albumartist": "Fixture Artist", "album": "Split Album",
                              "year": "2024", "genre": "Test Rock", "composer": "A. Writer"}
    assert opening.isrc == "GBAYE2400001"
    assert duet.fields["artist"] == "Fixture Artist & Guest" and duet.fields["albumartist"] == "Fixture Artist"
    assert hidden.fields["title"] == "Hidden track" and hidden.fields["track"] == 0
    wav = plan(split_dir / "wav_image" / "image.cue")  # a source without tags: only the sheet's values
    assert "year" not in wav.tracks[0].fields and wav.tracks[0].fields["album"] == "Split Album"


def test_source_tags_fill_what_the_sheet_lacks(tmp_path):
    (tmp_path / "a.flac").write_bytes(b"")
    (tmp_path / "a.cue").write_text('FILE "a.flac" WAVE\n  TRACK 01 AUDIO\n    TITLE "One"\n    INDEX 01 00:00:00\n'
                                    '  TRACK 02 AUDIO\n    TITLE "Two"\n    INDEX 01 00:01:00\n', encoding="utf-8")
    info = cue.SourceInfo("FLAC", 44100, 10 * 44100, 2, 16,
                          tags={"album": "From Tags", "albumartist": "Tag Artist", "year": "1999", "genre": "Jazz"})
    fields = plan(tmp_path / "a.cue", read_info=lambda _p: info).tracks[0].fields
    assert (fields["album"], fields["albumartist"], fields["artist"], fields["year"], fields["genre"]) == (
        "From Tags", "Tag Artist", "Tag Artist", "1999", "Jazz")


def test_names_follow_the_shared_masks(split_dir):
    result = plan(split_dir / "hidden_track" / "Split Album.cue", masks=("{disc}-{track:02} {artist} - {title}",))
    assert result.tracks[3].name == "1-03 Fixture Artist & Guest - Duet.flac"
