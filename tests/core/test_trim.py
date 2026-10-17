"""core/trim.py: the plan and its checks, the cut and its proof through the split's code, the subfolder, the tags
(SPEC §23).

A fake runner stands in for flac in most tests: its "audio" is a byte string, one byte per sample, at a "rate" of
1000 samples a second, so one millisecond is one sample. The `slow` tests run the real bundled flac on copies of
the split fixtures.
"""

import hashlib
import shutil
import struct

import pytest

from music_desk import paths
from music_desk.core import cue, playlists, split, trim
from music_desk.core.cancel import CancelToken
from music_desk.core.flac import Cancelled, FlacError, FlacRunner

RATE = 1000
SAMPLES = 10 * RATE  # 10 s
PCM = bytes((i * 7 + i // 13) % 251 for i in range(SAMPLES))  # never repeats over this length
INFO = cue.SourceInfo("FLAC", RATE, SAMPLES, 1, 8)


class FakeRunner:
    """flac's three operations over PCM (8-bit mono, so one byte is one sample)."""

    def __init__(self, shift: int = 0, fail: bool = False, token: CancelToken | None = None,
                 meanwhile=None) -> None:
        self.shift, self.fail, self.token, self.meanwhile = shift, fail, token, meanwhile
        self.encoded: list[tuple[int, int | None]] = []

    def encode(self, source, start, end, output, cancel=None):
        self.encoded.append((start, end))
        output.write_bytes(b"half")  # flac has begun writing it
        if self.meanwhile:
            self.meanwhile(output)
        if self.token is not None:
            self.token.cancel()
            raise Cancelled()
        if self.fail:
            raise FlacError("ERROR: something went wrong")
        output.write_bytes(PCM[start + self.shift:end])

    def decode(self, path, cancel=None):
        yield path.read_bytes()

    def decode_range(self, source, start=0, end=None, cancel=None):
        yield PCM[start:end]


@pytest.fixture
def track(tmp_path):
    folder = tmp_path / "Album"
    folder.mkdir()
    path = folder / "03 Song.flac"
    path.write_bytes(b"x" * 1000)
    (folder / "notes.txt").write_text("keep me", encoding="utf-8")
    return path


def plan(path, start="0:01.000", end="0:09.000", subfolder="lite", info=INFO, existing=None):
    return trim.plan_trim(path, info, start, end, subfolder, existing)


def tree(folder):
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*"))


ROOMY = {"free_space": lambda _f: 10**9}


# -- times --

@pytest.mark.parametrize(("text", "ms"), [
    ("0:00.000", 0), ("3:41.200", 221_200), ("75:02.5", 4_502_500), ("1:05", 65_000), (" 0:02.35 ", 2_350),
    ("0:5.000", 5_000),
])
def test_times_are_read(text, ms):
    assert trim.parse_time(text) == ms


@pytest.mark.parametrize("text", ["", "3", "3:60.000", "3:41.2000", "-0:01.000", "1:2:3", "a:bc", "3:41,200"])
def test_malformed_times(text):
    assert trim.parse_time(text) is None


def test_times_are_written_with_minutes_past_59():
    assert [trim.format_time(ms) for ms in (0, 2_350, 221_200, 4_502_500)] == [
        "0:00.000", "0:02.350", "3:41.200", "75:02.500"]


@pytest.mark.parametrize(("ms", "rate", "samples"), [
    (2_350, 44_100, 103_635), (2_350, 48_000, 112_800), (5, 44_100, 221),  # 220.5 rounds up
    (1, 44_100, 44), (221_200, 96_000, 21_235_200)])
def test_times_to_samples(ms, rate, samples):
    assert trim.to_samples(ms, rate) == samples
    assert trim.to_ms(samples, rate) == ms


def test_an_end_left_at_the_shown_length_keeps_the_last_sample(track):
    """The shown length is rounded to the millisecond; End at it still means the very end."""
    info = cue.SourceInfo("FLAC", 44_100, 3 * 44_100 + 7, 2, 16)
    assert trim.length_ms(info) == 3_000
    p = plan(track, "0:01.000", "0:03.000", info=info)
    assert p.ok and p.end == info.samples and p.start == 44_100


# -- the plan --

def test_a_plan_names_the_copy_in_the_subfolder(track):
    p = plan(track)
    assert p.ok and p.refusal is None
    assert (p.start, p.end, p.length) == (1_000, 9_000, 8_000)
    assert p.path == track.parent / "lite" / "03 Song.flac" and p.shown_path == "lite\\03 Song.flac"
    assert p.note is None
    assert not (track.parent / "lite").exists()  # planning writes nothing


def test_a_wav_or_aiff_gives_a_flac(tmp_path):
    for name in ("03 Song.wav", "03 Song.aiff"):
        assert plan(tmp_path / name).name == "03 Song.flac"


def test_a_taken_name_is_numbered(track):
    p = plan(track, existing=["03 song.flac", "03 Song (2).flac"])
    assert p.ok and p.name == "03 Song (3).flac"
    assert p.note == "03 Song.flac is already in lite, so the copy is named 03 Song (3).flac."


def test_the_subfolders_files_are_read_when_it_exists(track):
    (track.parent / "lite").mkdir()
    (track.parent / "lite" / "03 Song.flac").write_bytes(b"an earlier trim")
    assert plan(track).name == "03 Song (2).flac"


@pytest.mark.parametrize(("start", "end", "where", "why"), [
    ("1:00", "0:09.000", "start", "The start is past the track's length (0:10.000)."),
    ("0:01.0000", "0:09.000", "start", "Type the start as m:ss.mmm, for example 0:02.350."),
    ("0:01.000", "nine", "end", "Type the end as m:ss.mmm, for example 3:43.550."),
    ("0:01.000", "0:10.001", "end", "The end is past the track's length (0:10.000)."),
    ("0:05.000", "0:05.000", "end", "The start must be before the end."),
    ("0:06.000", "0:05.000", "end", "The start must be before the end."),
    ("0:00.000", "0:10.000", None, "Nothing to trim: move a handle."),
])
def test_bad_times_are_refused(track, start, end, where, why):
    p = plan(track, start, end)
    assert not p.ok and p.problems == [(where, why)] and p.problem(where) == why


@pytest.mark.parametrize(("name", "why"), [
    ("", "Type the subfolder's name."),
    ("  ", "Type the subfolder's name."),
    ("a/b", 'A folder name can\'t contain any of \\ / : * ? " < > |.'),
    ("what?", 'A folder name can\'t contain any of \\ / : * ? " < > |.'),
    ("lite.", "A folder name can't end with a dot or a space."),
    ("lite ", "A folder name can't end with a dot or a space."),
    ("CON", "CON is a name Windows reserves."),
    ("com1.old", "com1.old is a name Windows reserves."),
    ("notes.txt", "notes.txt is a file in this folder, not a folder."),
])
def test_bad_subfolders_are_refused(track, name, why):
    p = plan(track, subfolder=name)
    assert p.problems == [("subfolder", why)]


def test_an_existing_subfolder_is_fine(track):
    (track.parent / "Lite").mkdir()
    assert plan(track, subfolder="lite").ok


def test_a_path_too_long_is_refused(track):
    name = "x" * (cue.MAX_PATH - len(str(track.parent)) - len("\\\\03 Song.flac.partial") + 1)
    p = plan(track, subfolder=name)
    assert p.problem("subfolder").startswith(f"The copy's path would have {cue.MAX_PATH + 1} characters")
    assert plan(track, subfolder=name[:-1]).ok


def test_the_summary(track):
    assert plan(track).summary == "Keeps 0:08.000. Removes 0:01.000 at the start and 0:01.000 at the end."
    assert plan(track, "0:00.000", "0:07.650").summary == "Keeps 0:07.650. Removes 0:02.350 at the end."
    assert plan(track, "0:02.350", "0:10.000").summary == "Keeps 0:07.650. Removes 0:02.350 at the start."


def test_unsupported_formats_are_named(tmp_path):
    assert trim.unsupported_reason(tmp_path / "x.flac") is None
    assert trim.unsupported_reason(tmp_path / "x.AIF") is None
    assert trim.unsupported_reason(tmp_path / "x.mp3") == "MP3 isn't supported: trimming reads FLAC, WAV and AIFF."
    with pytest.raises(cue.CueError, match=r"^OPUS isn't supported: trimming reads FLAC, WAV and AIFF\.$"):
        trim.read_source(tmp_path / "x.opus")
    fmt = struct.pack("<HHIIHH", 3, 2, 44100, 44100 * 8, 8, 32)  # IEEE float
    float_wav = tmp_path / "float.wav"
    float_wav.write_bytes(b"RIFF" + struct.pack("<I", 4 + 8 + 16 + 8 + 8) + b"WAVEfmt " + struct.pack("<I", 16)
                          + fmt + b"data" + struct.pack("<I", 8) + b"\0" * 8)
    with pytest.raises(cue.CueError, match="32-bit float WAV isn't supported"):
        trim.read_source(float_wav)


# -- the cut and its proof --

def test_a_good_cut_is_proved_in_a_new_subfolder(track):
    runner = FakeRunner()
    result = trim.cut_trim(plan(track), runner, **ROOMY)
    assert result.ok and result.error is None and result.created_folder
    assert runner.encoded == [(1_000, 9_000)]
    assert result.partial == track.parent / "lite" / "03 Song.flac.partial"
    assert result.samples == 8_000 and result.md5 == hashlib.md5(PCM[1_000:9_000]).hexdigest()
    assert result.partial.read_bytes() == PCM[1_000:9_000]


def test_an_end_at_the_length_cuts_to_the_end(track):
    runner = FakeRunner()
    assert trim.cut_trim(plan(track, "0:02.000", "0:10.000"), runner, **ROOMY).ok
    assert runner.encoded == [(2_000, None)]


def test_an_existing_subfolder_is_reused_and_kept(track):
    (track.parent / "lite").mkdir()
    result = trim.cut_trim(plan(track), FakeRunner(fail=True), **ROOMY)
    assert not result.created_folder and result.error == "flac: ERROR: something went wrong"
    assert tree(track.parent) == ["03 Song.flac", "lite", "notes.txt"]


@pytest.mark.parametrize("runner", [FakeRunner(shift=1), FakeRunner(fail=True), FakeRunner(token=CancelToken())],
                         ids=["one sample off", "flac error", "stopped"])
def test_a_failed_run_leaves_the_folder_as_it_was(track, runner):
    before = tree(track.parent)
    result = trim.cut_trim(plan(track), runner, **ROOMY)
    assert not result.ok and result.partial is None
    assert tree(track.parent) == before  # the .partial and the subfolder this run made are gone


def test_a_cut_one_sample_off_fails_the_proof(track):
    result = trim.cut_trim(plan(track), FakeRunner(shift=1), **ROOMY)
    assert result.error.startswith("The proof failed: the copy has 7,999 samples, but the trim planned 8,000.")


def test_right_length_wrong_audio_fails_the_proof(track):
    class Corrupting(FakeRunner):
        def encode(self, source, start, end, output, cancel=None):
            super().encode(source, start, end, output, cancel)
            data = bytearray(output.read_bytes())
            data[100] ^= 1
            output.write_bytes(bytes(data))

    result = trim.cut_trim(plan(track), Corrupting(), **ROOMY)
    assert result.error.startswith("The proof failed: the copy's audio doesn't match its part of the original")


def test_stop_is_reported(track):
    result = trim.cut_trim(plan(track), FakeRunner(token=CancelToken()), **ROOMY)
    assert result.cancelled and result.error == "Stopped."


def test_a_created_subfolder_that_isnt_empty_is_kept(track):
    """Something else arrived in it meanwhile: only this run's .partial goes."""
    def arrives(output):
        (output.parent / "other.txt").write_text("not ours", encoding="utf-8")

    result = trim.cut_trim(plan(track), FakeRunner(fail=True, meanwhile=arrives), **ROOMY)
    assert not result.ok and tree(track.parent / "lite") == ["other.txt"]


def test_not_enough_free_space(track):
    result = trim.cut_trim(plan(track), FakeRunner(), free_space=lambda _f: 799)
    assert result.error == "Not enough free space: the copy needs about 0 MB, and the drive has 0 MB free."
    assert not (track.parent / "lite").exists()
    assert trim.cut_trim(plan(track), FakeRunner(), free_space=lambda _f: 800).ok  # 8 s of 10 s of 1000 bytes


def test_a_leftover_partial_is_never_overwritten_or_deleted(track):
    (track.parent / "lite").mkdir()
    leftover = track.parent / "lite" / "03 Song.flac.partial"
    leftover.write_bytes(b"left by a crash")
    runner = FakeRunner()
    result = trim.cut_trim(plan(track), runner, **ROOMY)
    assert result.error == ("lite\\03 Song.flac.partial already exists, maybe left by an earlier trim. Delete it in "
                            "File Explorer, then trim again.")
    assert runner.encoded == [] and leftover.read_bytes() == b"left by a crash"


def test_a_refused_plan_is_not_cut(track):
    runner = FakeRunner()
    result = trim.cut_trim(plan(track, "0:00.000", "0:10.000"), runner, **ROOMY)
    assert result.error == "Nothing to trim: move a handle." and runner.encoded == []


# -- finishing --

def test_a_name_taken_meanwhile_renames_nothing(track):
    p = plan(track)
    cut = trim.cut_trim(p, FakeRunner(), **ROOMY)
    (track.parent / "lite" / "03 Song.flac").write_bytes(b"arrived meanwhile")
    done = trim.finish_trim(p, cut)
    assert done.error == "Nothing is ever overwritten, and lite\\03 Song.flac was created meanwhile."
    assert done.path is None and tree(track.parent / "lite") == ["03 Song.flac"]


def test_finishing_renames_and_reports_tags_it_couldnt_copy(track):
    p = plan(track)
    cut = trim.cut_trim(p, FakeRunner(), **ROOMY)
    done = trim.finish_trim(p, cut)  # the fake copy isn't a real FLAC: the audio stays, a warning says so
    assert done.error is None and done.path == track.parent / "lite" / "03 Song.flac"
    assert done.path.read_bytes() == PCM[1_000:9_000]
    assert len(done.warnings) == 1 and done.warnings[0].startswith("03 Song.flac: the tags couldn't be copied")
    assert tree(track.parent) == ["03 Song.flac", "lite", "lite\\03 Song.flac", "notes.txt"]


# -- the tags --

@pytest.fixture
def copy_target(fixture_copy, tmp_path):
    """A FLAC standing in for the trimmed copy, with tags of its own that must not survive."""
    target = tmp_path / "copy" / "tone.flac"
    target.parent.mkdir()
    shutil.copy2(fixture_copy("formats/tone.flac"), target)
    from mutagen.flac import FLAC

    audio = FLAC(str(target))
    audio["TITLE"], audio["STALE"] = "Left by flac", "yes"
    audio.save()
    return target


def test_a_flacs_tags_are_copied_without_lyrics(fixtures_dir, tmp_path, copy_target):
    from mutagen.flac import FLAC, Picture

    source = tmp_path / "source.flac"
    shutil.copy2(fixtures_dir / "split" / "hidden_track" / "Fixture Artist - Split Album.flac", source)
    audio = FLAC(str(source))
    audio["TITLE"] = "Song"
    audio["Custom"] = ["one", "two"]
    audio["REPLAYGAIN_TRACK_GAIN"], audio["replaygain_album_gain"] = "-7.10 dB", "-6.50 dB"
    audio["LYRICS"], audio["UnsyncedLyrics"], audio["SYNCEDLYRICS"] = "la", "la la", "[00:01.00]la"
    back = Picture()
    back.type, back.mime, back.data = 4, "image/png", b"\x89PNG back"
    audio.add_picture(back)
    audio.save()
    expected = [(k, v) for k, v in FLAC(str(source)).tags if k.casefold() not in trim.LYRICS_KEYS]
    pictures = [(p.type, p.data) for p in FLAC(str(source)).pictures]

    trim.copy_tags(source, copy_target)
    copy = FLAC(str(copy_target))
    assert list(copy.tags) == expected
    assert ("Custom", "two") in expected and ("REPLAYGAIN_TRACK_GAIN", "-7.10 dB") in expected
    assert [(p.type, p.data) for p in copy.pictures] == pictures and len(pictures) == 2


@pytest.mark.parametrize("name", ["tone.wav", "tone.aiff"])
def test_a_wav_or_aiffs_fields_replaygain_and_cover_are_copied(fixture_copy, fixtures_dir, copy_target, name):
    from mutagen.flac import FLAC

    from music_desk.core.tags import TaggedFile

    source = fixture_copy(f"formats/{name}")
    tagged = TaggedFile(source)
    tagged.set_fields({"title": "Song", "artist": "Artist", "album": "Album", "track": 3, "track_total": 9,
                       "year": "1999", "comment": "a note", "lyrics": "la la"})
    tagged.set_replaygain("replaygain_track_gain", "-7.10 dB")
    tagged.set_replaygain("REPLAYGAIN_ALBUM_PEAK", "0.988")
    cover = (fixtures_dir / "cover.jpg").read_bytes()
    tagged.set_cover(cover)
    tagged.save()

    trim.copy_tags(source, copy_target)
    copy = TaggedFile(copy_target)
    fields = copy.fields()
    assert (fields.title, fields.artist, fields.album, fields.track, fields.track_total, fields.year,
            fields.comment, fields.lyrics) == ("Song", "Artist", "Album", 3, 9, "1999", "a note", None)
    assert copy.get_replaygain("replaygain_track_gain") == "-7.10 dB"
    assert copy.get_replaygain("replaygain_album_peak") == "0.988"
    assert copy.cover().data == cover
    assert "STALE" not in FLAC(str(copy_target)).tags


# -- the real flac (slow) --

@pytest.fixture
def runner():
    return FlacRunner(paths.flac_path())


HIDDEN = "split/hidden_track/Fixture Artist - Split Album.flac"  # 12 s, a 3 s hidden track before the songs


def real_plan(path, start, end, subfolder="lite"):
    return trim.plan_trim(path, trim.read_source(path), start, end, subfolder)


@pytest.mark.slow
@pytest.mark.parametrize(("start", "end", "samples"), [
    ("0:00.000", "0:03.000", 3 * 44_100),  # the hidden part
    ("0:03.000", "0:12.000", 9 * 44_100),  # the songs
    ("0:01.234", "0:10.001", 386_625),  # not on a CD frame
])
def test_real_trims_are_proved_and_leave_the_original(fixture_copy, runner, start, end, samples):
    from mutagen.flac import FLAC

    source = fixture_copy(HIDDEN)
    audio = FLAC(str(source))
    audio["LYRICS"], audio["REPLAYGAIN_TRACK_GAIN"] = "la la", "-7.10 dB"
    audio.save()
    original = source.read_bytes()
    p = real_plan(source, start, end)
    assert p.ok and p.length == samples
    cut = trim.cut_trim(p, runner)
    assert cut.ok, cut.error
    expected = hashlib.md5()
    split.hash_chunks(runner.decode_range(source, p.start, None if p.end == p.info.samples else p.end), expected)
    assert cut.samples == samples and cut.md5 == expected.hexdigest()
    done = trim.finish_trim(p, cut)
    assert done.error is None and done.warnings == []
    assert source.read_bytes() == original  # the original is unchanged
    copy = FLAC(str(done.path))
    assert copy.info.total_samples == samples
    assert "LYRICS" not in copy.tags and copy["REPLAYGAIN_TRACK_GAIN"] == ["-7.10 dB"]
    assert copy["ALBUM"] == ["Source Album"] and len(copy.pictures) == 1
    assert sorted(p.name for p in source.parent.rglob("*")) == ["Fixture Artist - Split Album.flac",
                                                               "Fixture Artist - Split Album.flac", "lite"]


@pytest.mark.slow
@pytest.mark.parametrize("shift_end", [False, True])
def test_a_real_trim_one_sample_off_fails(fixture_copy, runner, shift_end):
    class Shifted(FlacRunner):
        def encode(self, source, start, end, output, cancel=None):
            if shift_end:
                super().encode(source, start, end + 1, output, cancel)
            else:
                super().encode(source, start + 1, end, output, cancel)

    source = fixture_copy(HIDDEN)
    original = source.read_bytes()
    result = trim.cut_trim(real_plan(source, "0:03.000", "0:09.000"), Shifted(paths.flac_path()))
    assert not result.ok and result.error.startswith("The proof failed: the copy has ")
    assert sorted(p.name for p in source.parent.iterdir()) == [source.name]  # no .partial, no subfolder
    assert source.read_bytes() == original


@pytest.mark.slow
def test_a_real_trim_of_a_wav_gives_a_proved_flac(fixture_copy, runner):
    source = fixture_copy("split/wav_image/image.wav")
    original = source.read_bytes()
    p = real_plan(source, "0:00.500", "0:05.500", subfolder="trimmed")
    cut = trim.cut_trim(p, runner)
    assert cut.ok, cut.error
    done = trim.finish_trim(p, cut)
    assert done.path == source.parent / "trimmed" / "image.flac" and done.warnings == []
    assert source.read_bytes() == original


@pytest.mark.slow
def test_stopping_the_real_flac(fixture_copy, runner):
    token = CancelToken()
    token.cancel()
    source = fixture_copy(HIDDEN)
    result = trim.cut_trim(real_plan(source, "0:03.000", "0:09.000"), runner, token)
    assert result.cancelled and sorted(p.name for p in source.parent.iterdir()) == [source.name]


# -- Preview in player --

def preview_text(path, start, end, title="Song", info=None):
    info = info or cue.SourceInfo("FLAC", 44_100, 60 * 44_100, 2, 16)
    sheet = trim.preview_sheet(trim.plan_trim(path, info, start, end, "lite"), title, "Artist", "Album")
    return playlists.render_preview(sheet)


def test_the_preview_has_three_parts_rounded_to_frames(track):
    text = preview_text(track, "0:02.350", "0:41.004")  # 2.350 s is 176.25 frames, 41.004 s 3075.3
    assert text == ("﻿"
                    'PERFORMER "Artist"\r\nTITLE "Album"\r\n'
                    f'FILE "{track}" WAVE\r\n'
                    '  TRACK 01 AUDIO\r\n    TITLE "Cut from the start"\r\n    PERFORMER "Artist"\r\n'
                    "    INDEX 01 00:00:00\r\n"
                    '  TRACK 02 AUDIO\r\n    TITLE "Song (kept)"\r\n    PERFORMER "Artist"\r\n'
                    "    INDEX 01 00:02:26\r\n"
                    '  TRACK 03 AUDIO\r\n    TITLE "Cut from the end"\r\n    PERFORMER "Artist"\r\n'
                    "    INDEX 01 00:41:00\r\n")


@pytest.mark.parametrize(("start", "end", "titles"), [
    ("0:00.000", "0:41.000", ["Song (kept)", "Cut from the end"]),
    ("0:02.000", "1:00.000", ["Cut from the start", "Song (kept)"]),
    ("0:00.003", "1:00.000", ["Song (kept)"]),  # 3 ms rounds to frame 0: nothing to hear there
])
def test_a_part_of_no_length_is_left_out(track, start, end, titles):
    sheet = trim.preview_sheet(trim.plan_trim(track, cue.SourceInfo("FLAC", 44_100, 60 * 44_100, 2, 16),
                                              start, end, "lite"), "Song")
    text = playlists.render_preview(sheet)
    assert [line.strip()[7:-1] for line in text.splitlines() if line.strip().startswith("TITLE")] == titles
    assert "INDEX 01 00:00:00" in text and "TRACK 01" in text


def test_the_preview_needs_only_valid_times(track):
    plan = trim.plan_trim(track, INFO, "0:01.000", "0:09.000", "a:b")  # a bad subfolder doesn't matter
    assert not plan.ok and plan.times_ok
    sheet = trim.preview_sheet(plan, "  ")
    assert [t.title for t in sheet.tracks] == ["Cut from the start", "03 Song (kept)", "Cut from the end"]
    assert [t.start for t in sheet.tracks] == [0, 1000, 9000]
    assert not trim.plan_trim(track, INFO, "0:01.000", "0:x", "lite").times_ok
    assert not trim.plan_trim(track, INFO, "0:05.000", "0:04.000", "lite").times_ok
    assert trim.plan_trim(track, INFO, "0:00.000", "0:10.000", "lite").times_ok  # nothing to trim: still playable
