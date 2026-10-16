"""core/split.py and core/flac.py: cutting, the proof, and cleaning up only this run's .partial files (SPEC §22).

A fake runner stands in for flac in most tests: its "audio" is a byte string, one byte per sample. The `slow`
tests run the real bundled flac on copies of the split fixtures.
"""

import hashlib
import shutil

import pytest

from music_desk import paths
from music_desk.core import cue, split
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


# -- the real flac (slow) --

@pytest.fixture
def runner():
    return FlacRunner(paths.flac_path())


def real_plan(fixtures_dir, tmp_path, name, runner):
    folder = tmp_path / name
    shutil.copytree(fixtures_dir / "split" / name, folder)
    sheet = next(folder.glob("*.cue"))
    return cue.plan_album(sheet, DEFAULT_MASKS, "-", is_silent=split.is_silent(runner))


@pytest.mark.slow
def test_the_bundled_flac_runs():
    assert flac_version(paths.flac_path()) == "flac 1.5.0"


@pytest.mark.slow
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


@pytest.mark.slow
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


@pytest.mark.slow
def test_stopping_the_real_flac(fixtures_dir, tmp_path, runner):
    token = CancelToken()
    token.cancel()
    plan = real_plan(fixtures_dir, tmp_path, "hidden_track", runner)
    before = names(plan.cue.parent)
    result = split.cut_album(plan, runner, token)
    assert result.cancelled and names(plan.cue.parent) == before
