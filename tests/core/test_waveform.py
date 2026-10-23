"""core/waveform.py: the Trim page's waveform peaks (SPEC §23).

The reducer is fed known PCM (little-endian, signed, interleaved, as flac writes it). One test reads a
fixture through the real bundled flac.
"""

import struct

import pytest

from music_desk import paths
from music_desk.core import trim, waveform
from music_desk.core.cue import SourceInfo
from music_desk.core.flac import FlacRunner
from music_desk.core.waveform import PeakReducer, Peaks


def pcm(values: list[int], bits: int) -> bytes:
    """Interleaved samples as flac's raw output: 1, 2 or 3 bytes each, little-endian, signed."""
    width = (bits + 7) // 8
    return b"".join(v.to_bytes(width, "little", signed=True) for v in values)


def feed_in_pieces(reducer: PeakReducer, data: bytes, size: int) -> Peaks:
    for i in range(0, len(data), size):
        reducer.feed(data[i:i + size])
    return reducer.result()


def test_16_bit_stereo_peaks_take_both_channels_and_the_top_byte():
    # 4 columns of 2 frames: (left, right) pairs.
    frames = [(0, 0), (0, 0),  # silence: a flat line
              (16384, -1), (0, 0),  # +0.5 on the left, -1 (dither) on the right
              (-32768, 32767), (0, 0),  # full scale both ways
              (256, -256), (-255, 255)]
    data = pcm([v for frame in frames for v in frame], 16)
    peaks = waveform.reduce([data], SourceInfo("FLAC", 44_100, 8, 2, 16), columns=4)
    assert peaks.lows == [0.0, -1 / 128, -1.0, -1 / 128]
    assert peaks.highs == [0.0, 0.5, 127 / 128, 1 / 128]


def test_24_bit_mono_uses_the_third_byte():
    data = pcm([0, 0x400000, -0x800000, 0x10000], 24)  # 0, +0.5, -1.0, +1/128
    peaks = waveform.reduce([data], SourceInfo("FLAC", 96_000, 4, 1, 24), columns=4)
    assert peaks.highs == [0.0, 0.5, -1.0, 1 / 128]
    assert peaks.lows == peaks.highs


def test_20_bit_and_8_bit_are_scaled_to_their_range():
    peaks = waveform.reduce([pcm([0x40000, -0x80000], 20)], SourceInfo("FLAC", 48_000, 2, 1, 20), columns=2)
    assert peaks.highs == [0.5, -1.0]
    peaks = waveform.reduce([pcm([64, -128], 8)], SourceInfo("WAV", 8_000, 2, 1, 8), columns=2)
    assert peaks.highs == [0.5, -1.0]


@pytest.mark.parametrize("size", [1, 3, 5, 7, 1000])
def test_chunks_of_any_size_give_the_same_peaks(size):
    values = [((i * 7919) % 65536) - 32768 for i in range(2 * 1000)]  # stereo, never-repeating
    data = pcm(values, 16)
    whole = waveform.reduce([data], SourceInfo("FLAC", 44_100, 1000, 2, 16), columns=37)
    pieces = feed_in_pieces(PeakReducer(1000, 2, 16, columns=37), data, size)
    assert pieces == whole and len(whole) == 37
    # Each column against a direct computation over its frames.
    for column in range(37):
        first, last = column * 1000 // 37, (column + 1) * 1000 // 37
        tops = [v >> 8 for v in values[2 * first:2 * last]]
        assert whole.lows[column] == min(tops) / 128 and whole.highs[column] == max(tops) / 128


def test_a_short_track_has_one_column_a_frame():
    peaks = waveform.reduce([pcm([100 << 8, -(50 << 8), 0], 16)], SourceInfo("FLAC", 44_100, 3, 1, 16))
    assert len(peaks) == 3 and peaks.highs == [100 / 128, -50 / 128, 0.0]


def test_a_stream_shorter_than_declared_keeps_what_was_read():
    peaks = waveform.reduce([pcm([1 << 8] * 15, 16)], SourceInfo("FLAC", 44_100, 40, 1, 16), columns=4)
    assert peaks.highs == [1 / 128, 1 / 128]  # one full column of 10 frames, then the 5 begun


def test_frames_past_the_declared_length_join_the_last_column():
    peaks = waveform.reduce([pcm([0] * 4 + [127 << 8] * 2, 16)], SourceInfo("FLAC", 44_100, 4, 1, 16), columns=2)
    assert peaks.highs == [0.0, 127 / 128]


class Recorder:
    def __init__(self, data: bytes) -> None:
        self.data, self.calls = data, []

    def decode(self, path, cancel=None):
        self.calls.append("decode")
        yield self.data

    def decode_range(self, source, start=0, end=None, cancel=None):
        self.calls.append("decode_range")
        yield self.data


@pytest.mark.parametrize(("kind", "call"), [("FLAC", "decode"), ("WAV", "decode_range"), ("AIFF", "decode_range")])
def test_a_flac_is_decoded_directly_and_others_through_flac(tmp_path, kind, call):
    runner = Recorder(pcm([0, 64 << 8], 16))
    peaks = waveform.read_peaks(runner, tmp_path / "x", SourceInfo(kind, 44_100, 2, 1, 16))
    assert runner.calls == [call] and peaks.highs == [0.0, 0.5]


def test_fit_reduces_and_stretches():
    peaks = Peaks([-0.1, -0.5, 0.0, -0.2], [0.1, 0.2, 0.9, 0.3])
    assert waveform.fit(peaks, 2) == [(-0.5, 0.2), (-0.2, 0.9)]
    assert waveform.fit(peaks, 1) == [(-0.5, 0.9)]
    assert waveform.fit(peaks, 8) == [(-0.1, 0.1), (-0.1, 0.1), (-0.5, 0.2), (-0.5, 0.2),
                                      (0.0, 0.9), (0.0, 0.9), (-0.2, 0.3), (-0.2, 0.3)]
    assert waveform.fit(peaks, 0) == [] and waveform.fit(Peaks([], []), 10) == []


def test_a_real_silent_stretch_is_a_flat_line(fixture_copy):
    """3 s of silence, then 6 s of a quarter-amplitude chirp, read by the bundled flac."""
    source = fixture_copy("split/silent_pregap/image.flac")
    info = trim.read_source(source)
    peaks = waveform.read_peaks(FlacRunner(paths.flac_path()), source, info, columns=90)
    assert len(peaks) == 90
    assert all(low == high == 0.0 for low, high in zip(peaks.lows[:30], peaks.highs[:30], strict=True))
    assert all(high >= 0.23 and low <= -0.23 for low, high in zip(peaks.lows[31:], peaks.highs[31:], strict=True))
    assert max(peaks.highs) <= 0.25


def test_the_top_byte_is_the_sample_shifted():
    """What the reduction relies on: a little-endian sample's last byte is its value >> 8 (16-bit)."""
    for value in (-32768, -257, -256, -1, 0, 255, 256, 32767):
        assert struct.unpack("b", struct.pack("<h", value)[1:])[0] == value >> 8
