"""core/split.py and core/flac.py: cutting, the proof, and cleaning up only this run's .partial files (SPEC §22).

A fake runner stands in for flac in most tests: its "audio" is a byte string, one byte per sample. Some
tests run the real bundled flac on copies of the split fixtures.
"""

import hashlib
import shutil

import pytest

from music_desk import paths
from music_desk.core import cue, playlists, split
from music_desk.core.cancel import CancelToken
from music_desk.core.flac import Cancelled, FlacError, FlacRunner, flac_version
from music_desk.core.masks import DEFAULT_MASKS

SAMPLES = 10 * 75  # the fake source: 10 s at a "rate" of 75 samples a second, so one CD frame is one sample
PCM = bytes((i * 7 + i // 13) % 251 for i in range(SAMPLES))  # never repeats over this length


class FakeRunner:
    """flac's three operations over PCM (8-bit mono, so one byte is one sample)."""

    def __init__(self, shift: dict[int, int] | None = None, fail_at: int | None = None,
                 cancel_at: int | None = None, token: CancelToken | None = None) -> None:
        self.shift = shift or {}  # track index -> samples to move its start by (a wrong cut)
        self.fail_at, self.cancel_at, self.token = fail_at, cancel_at, token
        self.encoded = 0

    def encode(self, source, start, end, output, cancel=None):
        index = self.encoded
        self.encoded += 1
        output.write_bytes(b"half")  # flac has begun writing it
        if index == self.cancel_at:
            self.token.cancel()
            raise Cancelled()
        if index == self.fail_at:
            raise FlacError("ERROR: something went wrong")
        start += self.shift.get(index, 0)
        output.write_bytes(PCM[start:end])

    def decode(self, path, cancel=None):
        yield path.read_bytes()

    def decode_range(self, source, start=0, end=None, cancel=None):
        yield PCM[start:end]


@pytest.fixture
def album(tmp_path):
    folder = tmp_path / "Album"
    folder.mkdir()
    (folder / "image.flac").write_bytes(b"x" * 100)
    (folder / "image.cue").write_text(
        'PERFORMER "Artist"\nTITLE "Album"\nFILE "image.flac" WAVE\n'
        '  TRACK 01 AUDIO\n    TITLE "One"\n    INDEX 01 00:00:00\n'
        '  TRACK 02 AUDIO\n    TITLE "Two"\n    INDEX 00 00:03:00\n    INDEX 01 00:03:40\n'
        '  TRACK 03 AUDIO\n    TITLE "Three"\n    INDEX 01 00:07:10\n', encoding="utf-8")
    (folder / "notes.txt").write_text("keep me", encoding="utf-8")
    return folder


def make_plan(folder, md5=None):
    info = cue.SourceInfo("FLAC", 75, SAMPLES, 1, 8, md5)
    plan = cue.plan_album(folder / "image.cue", DEFAULT_MASKS, "-", read_info=lambda _p: info)
    assert plan.refusal is None
    return plan


def names(folder):
    return sorted(p.name for p in folder.iterdir())


ORIGINALS = ["image.cue", "image.flac", "notes.txt"]


@pytest.mark.parametrize("md5", [None, hashlib.md5(PCM).hexdigest()])
def test_a_good_cut_is_proved(album, md5):
    """Against the source's STREAMINFO MD5, or (WAV, AIFF, no MD5) the hash of its decoded audio."""
    plan = make_plan(album, md5)
    result = split.cut_album(plan, FakeRunner(), free_space=lambda _f: 10**9)
    assert result.ok and result.error is None
    assert result.lengths == [265, 535 - 265, SAMPLES - 535]  # 00:03:40, 00:07:10: the gap stays in track 1
    assert result.md5 == result.expected_md5 == hashlib.md5(PCM).hexdigest()
    assert [p.name for p in result.partials] == ["01 One.flac.partial", "02 Two.flac.partial",
                                                 "03 Three.flac.partial"]
    assert names(album) == sorted(ORIGINALS + [p.name for p in result.partials])


def test_a_cut_one_sample_off_fails_and_leaves_nothing(album):
    """The tracks' end-to-end MD5 alone could pass a moved boundary; the length check catches it."""
    plan = make_plan(album)
    result = split.cut_album(plan, FakeRunner(shift={1: 1}), free_space=lambda _f: 10**9)
    assert not result.ok and result.error.startswith("The proof failed: track 2 has 269 samples, but the cut "
                                                     "planned 270")
    assert "doesn't match the original's" in result.error
    assert names(album) == ORIGINALS  # this run's .partial files are gone, nothing else touched


def test_a_flac_error_deletes_this_runs_partials(album):
    plan = make_plan(album)
    result = split.cut_album(plan, FakeRunner(fail_at=1), free_space=lambda _f: 10**9)
    assert not result.ok and result.error == "flac: ERROR: something went wrong"
    assert names(album) == ORIGINALS  # track 1's finished file and track 2's half-written one


def test_stop_deletes_this_runs_partials(album):
    token = CancelToken()
    plan = make_plan(album)
    result = split.cut_album(plan, FakeRunner(cancel_at=2, token=token), token, free_space=lambda _f: 10**9)
    assert result.cancelled and not result.ok and names(album) == ORIGINALS


def test_a_leftover_partial_is_never_overwritten_or_deleted(album):
    (album / "02 Two.flac.partial").write_bytes(b"someone else's")
    plan = make_plan(album)
    result = split.cut_album(plan, FakeRunner(), free_space=lambda _f: 10**9)
    assert result.error.startswith("02 Two.flac.partial already exists, maybe left by an earlier split.")
    assert (album / "02 Two.flac.partial").read_bytes() == b"someone else's" and len(names(album)) == 4


def test_not_enough_free_space(album):
    plan = make_plan(album)
    result = split.cut_album(plan, FakeRunner(), free_space=lambda _f: 10)
    assert result.error.startswith("Not enough free space: ") and names(album) == ORIGINALS


def test_a_refused_plan_is_not_cut(album):
    plan = make_plan(album)
    plan.refusal = "No."
    assert split.cut_album(plan, FakeRunner()).error == "No."


def test_the_silence_check_decodes_just_that_range():
    calls = []

    class Runner(FakeRunner):
        def decode_range(self, source, start=0, end=None, cancel=None):
            calls.append((start, end))
            yield b"\0" * (end - start) if start < 100 else b"\0\0\x01"

    check = split.is_silent(Runner())
    assert check("x.flac", 0, 50) and not check("x.flac", 100, 103) and calls == [(0, 50), (100, 103)]


def test_flac_version_when_it_cant_run(tmp_path):
    assert flac_version(tmp_path / "missing.exe") is None


# -- the real flac (about 0.3 s each, SPEC §18) --

@pytest.fixture
def runner():
    return FlacRunner(paths.flac_path())


def real_plan(fixtures_dir, tmp_path, name, runner):
    folder = tmp_path / name
    shutil.copytree(fixtures_dir / "split" / name, folder)
    sheet = next(folder.glob("*.cue"))
    return cue.plan_album(sheet, DEFAULT_MASKS, "-", is_silent=split.is_silent(runner))


def test_the_bundled_flac_runs():
    assert flac_version(paths.flac_path()) == "flac 1.5.0"


@pytest.mark.parametrize(("name", "lengths"), [
    ("hidden_track", [3 * 44100, 3 * 44100 + 37 * 588, 3 * 44100 - 37 * 588, 3 * 44100]),
    ("silent_pregap", [6 * 44100, 3 * 44100]),  # the silent 3 s joined track 1
    ("wav_image", [3 * 44100, 3 * 44100]),
])
def test_real_cuts_are_proved(fixtures_dir, tmp_path, runner, name, lengths):
    plan = real_plan(fixtures_dir, tmp_path, name, runner)
    result = split.cut_album(plan, runner)
    assert result.ok, result.error
    assert result.lengths == lengths and result.md5 == result.expected_md5
    assert all(p.exists() for p in result.partials)


@pytest.mark.parametrize("name", ["hidden_track", "wav_image"])
def test_a_real_cut_one_sample_off_fails(fixtures_dir, tmp_path, runner, name):
    class Shifted(FlacRunner):
        def encode(self, source, start, end, output, cancel=None):
            super().encode(source, start + 1 if start else start, end, output, cancel)

    plan = real_plan(fixtures_dir, tmp_path, name, runner)
    before = names(plan.cue.parent)
    result = split.cut_album(plan, Shifted(paths.flac_path()))
    assert not result.ok and result.error.startswith("The proof failed: track ")
    assert "doesn't match the original's" in result.error
    assert names(plan.cue.parent) == before


def test_stopping_the_real_flac(fixtures_dir, tmp_path, runner):
    token = CancelToken()
    token.cancel()
    plan = real_plan(fixtures_dir, tmp_path, "hidden_track", runner)
    before = names(plan.cue.parent)
    result = split.cut_album(plan, runner, token)
    assert result.cancelled and names(plan.cue.parent) == before


# -- finishing an album --

def test_finish_names_and_tags_and_touches_nothing_else(fixtures_dir, tmp_path, runner):
    plan = real_plan(fixtures_dir, tmp_path, "hidden_track", runner)
    cut = split.cut_album(plan, runner)
    image, sheet = plan.source.read_bytes(), plan.cue.read_bytes()
    done = split.finish_album(plan, cut)
    assert done.error is None and done.warnings == []
    assert plan.source.read_bytes() == image and plan.cue.read_bytes() == sheet  # a split is just a split
    assert [p.name for p in done.tracks] == ["00 Hidden track.flac", "01 Opening.flac", "02 Second Song.flac",
                                             "03 Duet.flac"]
    from mutagen.flac import FLAC

    from music_desk.core.tags import TaggedFile

    opening = TaggedFile(done.tracks[1])
    fields = opening.fields()
    assert (fields.track, fields.track_total, fields.disc, fields.disc_total) == (1, 3, 1, 2)
    assert (fields.title, fields.artist, fields.album, fields.year, fields.genre, fields.composer) == (
        "Opening", "Fixture Artist", "Split Album", "2024", "Test Rock", "A. Writer")
    assert opening.cover() is not None and FLAC(str(done.tracks[1]))["ISRC"] == ["GBAYE2400001"]
    assert not list(plan.cue.parent.glob("*.partial"))


def test_a_name_taken_meanwhile_renames_nothing(album):
    plan = make_plan(album)
    cut = split.cut_album(plan, FakeRunner(), free_space=lambda _f: 10**9)
    (album / "02 Two.flac").write_bytes(b"arrived meanwhile")
    done = split.finish_album(plan, cut)
    assert done.error == "Nothing is ever overwritten, and these names were taken meanwhile: 02 Two.flac."
    assert names(album) == sorted(ORIGINALS + ["02 Two.flac"])
    assert (album / "02 Two.flac").read_bytes() == b"arrived meanwhile"


def test_tracks_go_to_the_chosen_destination(album, tmp_path):
    """The destination folder (Hugo, 2026-10-17): names are checked, and numbered, there."""
    elsewhere = tmp_path / "Elsewhere"
    elsewhere.mkdir()
    (elsewhere / "02 Two.flac").write_bytes(b"an earlier split")
    info = cue.SourceInfo("FLAC", 75, SAMPLES, 1, 8)
    path = album / "image.cue"
    plan = cue.plan_sheet(cue.read_cue_sheet(path), path, DEFAULT_MASKS, "-", read_info=lambda _p: info,
                          destination=elsewhere)
    assert plan.refusal is None and plan.folder == elsewhere
    assert [t.path for t in plan.tracks] == [elsewhere / "01 One.flac", elsewhere / "02 Two (2).flac",
                                             elsewhere / "03 Three.flac"]
    cut = split.cut_album(plan, FakeRunner(), free_space=lambda folder: 10**9 if folder == elsewhere else 0)
    assert cut.ok and all(p.parent == elsewhere for p in cut.partials)
    for partial in cut.partials:
        partial.write_bytes(b"")
    done = split.finish_album(plan, cut)
    assert sorted(p.name for p in elsewhere.iterdir()) == ["01 One.flac", "02 Two (2).flac", "02 Two.flac",
                                                           "03 Three.flac"]
    assert names(album) == ORIGINALS and (elsewhere / "02 Two.flac").read_bytes() == b"an earlier split"
    assert [p.name for p in done.tracks] == ["01 One.flac", "02 Two (2).flac", "03 Three.flac"]

    missing = cue.plan_sheet(cue.read_cue_sheet(path), path, DEFAULT_MASKS, "-", read_info=lambda _p: info,
                             destination=tmp_path / "Nowhere")
    assert missing.refusal.startswith("The destination folder ") and "doesn't exist" in missing.refusal
    assert not (tmp_path / "Nowhere").exists()  # the app never creates folders


def test_progress_is_reported_before_each_track(album):
    seen = []
    plan = ticked_plan(album, {1, 3})
    split.cut_album(plan, FakeRunner(), free_space=lambda _f: 10**9,
                    on_track=lambda done, total, track: seen.append((done, total, track.number)))
    assert seen == [(0, 2, 1), (1, 2, 3)]



# -- ticked tracks (the Split page) --

def ticked_plan(folder, ticked, md5=None):
    info = cue.SourceInfo("FLAC", 75, SAMPLES, 1, 8, md5)
    path = folder / "image.cue"
    plan = cue.plan_sheet(cue.read_cue_sheet(path), path, DEFAULT_MASKS, "-", ticked=ticked,
                          read_info=lambda _p: info)
    assert plan.refusal is None
    return plan


def test_only_the_ticked_track_is_written_and_proved_against_its_range(album):
    plan = ticked_plan(album, {2}, hashlib.md5(PCM).hexdigest())
    result = split.cut_album(plan, FakeRunner(), free_space=lambda _f: 10**9)
    assert result.ok and [p.name for p in result.partials] == ["02 Two.flac.partial"]
    assert result.lengths == [270] and result.track_md5s == [hashlib.md5(PCM[265:535]).hexdigest()]
    assert result.md5 is None and result.expected_md5 is None  # no end-to-end check without every track


class Corrupting(FakeRunner):
    """Writes the right number of samples, one of them wrong: only the per-track MD5 can see it."""

    def encode(self, source, start, end, output, cancel=None):
        super().encode(source, start, end, output, cancel)
        data = bytearray(output.read_bytes())
        data[len(data) // 2] ^= 1
        output.write_bytes(bytes(data))


def test_a_track_with_the_right_length_but_wrong_audio_fails(album):
    plan = ticked_plan(album, {3})
    result = split.cut_album(plan, Corrupting(), free_space=lambda _f: 10**9)
    assert not result.ok and result.error.startswith("The proof failed: track 3's audio doesn't match its part "
                                                     "of the original")
    assert names(album) == ORIGINALS


def test_free_space_counts_the_ticked_tracks_only(album):
    size = (album / "image.flac").stat().st_size  # 100 bytes for SAMPLES samples
    needed = -(-size * 270 // SAMPLES)  # track 2's share, rounded up
    plan = ticked_plan(album, {2})
    assert split.cut_album(plan, FakeRunner(), free_space=lambda _f: needed).ok
    plan = ticked_plan(album, {2})
    for partial in album.glob("*.partial"):
        partial.unlink()
    result = split.cut_album(plan, FakeRunner(), free_space=lambda _f: needed - 1)
    assert result.error.startswith("Not enough free space")


def test_a_leftover_partial_of_an_unticked_track_is_ignored(album):
    (album / "02 Two.flac.partial").write_bytes(b"someone else's")
    result = split.cut_album(ticked_plan(album, {1, 3}), FakeRunner(), free_space=lambda _f: 10**9)
    assert result.ok and (album / "02 Two.flac.partial").read_bytes() == b"someone else's"


def test_finishing_some_tracks_writes_only_those(album):
    plan = ticked_plan(album, {1, 3})
    cut = split.cut_album(plan, FakeRunner(), free_space=lambda _f: 10**9)
    for partial in cut.partials:
        partial.write_bytes(b"")
    done = split.finish_album(plan, cut)
    assert [p.name for p in done.tracks] == ["01 One.flac", "03 Three.flac"]
    assert names(album) == sorted(ORIGINALS + ["01 One.flac", "03 Three.flac"])


def test_extracting_only_track_3_with_the_real_flac(fixtures_dir, tmp_path, runner):
    plan = real_plan(fixtures_dir, tmp_path, "hidden_track", runner)
    plan = cue.plan_sheet(plan.sheet, plan.cue, DEFAULT_MASKS, "-", ticked={3}, is_silent=split.is_silent(runner))
    result = split.cut_album(plan, runner)
    assert result.ok, result.error
    assert result.lengths == [3 * 44100] and [p.name for p in result.partials] == ["03 Duet.flac.partial"]
    expected = hashlib.md5(b"".join(runner.decode_range(plan.source, 9 * 44100, None))).hexdigest()
    assert result.track_md5s == [expected]


@pytest.mark.parametrize("shift_end", [False, True])
def test_extracting_track_2_one_sample_off_fails_with_the_real_flac(fixtures_dir, tmp_path, runner, shift_end):
    """Moving only its start changes its length; moving both keeps the length, and the MD5 catches it."""
    class Shifted(FlacRunner):
        def encode(self, source, start, end, output, cancel=None):
            super().encode(source, start + 1, end + 1 if shift_end else end, output, cancel)

    plan = real_plan(fixtures_dir, tmp_path, "hidden_track", runner)
    plan = cue.plan_sheet(plan.sheet, plan.cue, DEFAULT_MASKS, "-", ticked={2}, is_silent=split.is_silent(runner))
    before = names(plan.cue.parent)
    result = split.cut_album(plan, Shifted(paths.flac_path()))
    assert not result.ok
    reason = "doesn't match its part of the original" if shift_end else "samples, but the cut planned"
    assert result.error.startswith("The proof failed: track 2") and reason in result.error
    assert names(plan.cue.parent) == before


def test_tracks_get_nothing_else_of_the_images_tags(fixtures_dir, tmp_path, runner):
    """flac carries a FLAC image's tags and pictures into each cut; a track keeps only the sheet's fields, its ISRC
    and the front cover (SPEC §22): the image's ReplayGain, lyrics, comment and cue sheet described the whole file."""
    from mutagen.flac import FLAC, Picture

    plan = real_plan(fixtures_dir, tmp_path, "hidden_track", runner)
    image = FLAC(str(plan.source))
    image["REPLAYGAIN_TRACK_GAIN"], image["LYRICS"], image["COMMENT"] = "-7.10 dB", "la la", "EAC rip"
    image["CUESHEET"], image["COMPOSER"] = plan.cue.read_text(encoding="utf-8"), "Image Composer"
    back = Picture()
    back.type, back.mime, back.data = 4, "image/png", b"\x89PNG back"
    image.add_picture(back)
    image.save()
    front = next(p.data for p in FLAC(str(plan.source)).pictures if p.type == 3)

    done = split.finish_album(plan, split.cut_album(plan, runner))
    assert done.error is None and done.warnings == []
    hidden, opening = (FLAC(str(path)) for path in done.tracks[:2])
    assert sorted(k for k, _v in opening.tags) == ["ALBUM", "ALBUMARTIST", "ARTIST", "COMPOSER", "DATE",
                                                    "DISCNUMBER", "DISCTOTAL", "GENRE", "ISRC", "TITLE",
                                                    "TRACKNUMBER", "TRACKTOTAL"]
    assert opening["COMPOSER"] == ["A. Writer"]
    assert "COMPOSER" not in hidden.tags  # the hidden track has no songwriter: not the image's either
    assert [(p.type, p.data) for p in opening.pictures] == [(3, front)]


# -- Preview in player (SPEC §22) --

def test_the_preview_lists_every_track_where_the_split_cuts_it(album):
    plan = ticked_plan(album, {1})  # unticked tracks are previewed too
    sheet = split.preview_sheet(plan)
    assert sheet.audio == album / "image.flac" and (sheet.title, sheet.performer) == ("Album", "Artist")
    assert [(t.start, t.title, t.performer) for t in sheet.tracks] == [
        (0, "One", "Artist"), (3 * 75 + 40, "Two", "Artist"), (7 * 75 + 10, "Three", "Artist")]  # the gap stays in One
    text = playlists.render_preview(sheet)
    assert "INDEX 00" not in text and "    INDEX 01 00:03:40\r\n" in text


def test_a_hidden_track_is_the_previews_track_1(fixtures_dir, tmp_path):
    folder = tmp_path / "hidden"
    shutil.copytree(fixtures_dir / "split" / "hidden_track", folder)
    plan = cue.plan_album(folder / "Split Album.cue", DEFAULT_MASKS, "-")
    text = playlists.render_preview(split.preview_sheet(plan))
    tracks = text.split("  TRACK ")[1:]
    assert [t.splitlines()[0] for t in tracks] == ["01 AUDIO", "02 AUDIO", "03 AUDIO", "04 AUDIO"]
    assert '    TITLE "Hidden track"' in tracks[0] and "INDEX 01 00:00:00" in tracks[0]
    assert '    TITLE "Opening"' in tracks[1] and "INDEX 01 00:03:00" in tracks[1]
    assert '    PERFORMER "Fixture Artist & Guest"' in tracks[3]
    assert f'FILE "{folder / "Fixture Artist - Split Album.flac"}" WAVE' in text
