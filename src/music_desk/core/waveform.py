"""The Trim page's waveform (SPEC §23): a minimum and a maximum per column, all channels together, reduced from
flac's raw PCM as its chunks arrive. No Qt.

The whole audio is never held: a 70-minute CD image is 740 MB of PCM. numpy isn't in the stack, so the reduction
keeps only each sample's most significant byte (`data[1::2]` for 16-bit, `data[2::3]` for 24-bit), which slicing
gives at C speed, maps it to offset binary with `bytes.translate` (so unsigned order is signed order), and takes
`min` and `max` of each column's bytes. 256 levels are more than a waveform a few hundred pixels tall can show;
a very quiet stretch (under about -42 dB at 16-bit) draws as a flat line, as silence does.

The peaks are computed once, at `COLUMNS` columns; the page reduces them to its width (`fit`), so dragging a handle
or resizing never decodes again.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from music_desk.core.cancel import CancelToken
from music_desk.core.cue import SourceInfo

COLUMNS = 4096  # more than any screen is wide; `fit` reduces them to the widget's width
_OFFSET = bytes((b + 128) & 0xFF for b in range(256))  # signed byte -> offset binary, so min and max order right


@dataclass(frozen=True)
class Peaks:
    """Per column, the lowest and highest sample value, from -1.0 to 1.0. Fewer than `COLUMNS` for a track with
    fewer frames; a column of silence has both at 0."""

    lows: list[float]
    highs: list[float]

    def __len__(self) -> int:
        return len(self.lows)


class PeakReducer:
    """Fed raw PCM (little-endian, signed, interleaved: what `FlacRunner.decode` gives) in chunks of any size."""

    def __init__(self, samples: int, channels: int, bits: int, columns: int = COLUMNS) -> None:
        self.width = (bits + 7) // 8  # bytes per sample, as flac writes raw PCM
        self.frame = self.width * channels
        self.samples = samples
        self.columns = max(1, min(columns, samples))
        # The top byte of a 20-bit sample (in 3 bytes) only reaches ±8: scale by what it can reach.
        self._scale = float(1 << max(0, bits - 8 * (self.width - 1) - 1))
        self._carry = b""
        self._done = 0  # frames reduced so far
        self._column = 0
        self._end = self._column_end(0)
        self._low: int | None = None
        self._high: int | None = None
        self.lows: list[float] = []
        self.highs: list[float] = []

    def _column_end(self, column: int) -> int:
        """One past the last frame of `column`; the last column takes any frames beyond the declared length."""
        if column >= self.columns - 1:
            return 1 << 62
        return (column + 1) * self.samples // self.columns

    def feed(self, chunk: bytes) -> None:
        data = self._carry + chunk if self._carry else chunk
        usable = len(data) - len(data) % self.frame
        self._carry = data[usable:]
        position = 0
        while position < usable:
            frames = min(self._end - self._done, (usable - position) // self.frame)
            stop = position + frames * self.frame
            top = data[position + self.width - 1:stop:self.width].translate(_OFFSET)
            low, high = min(top), max(top)
            self._low = low if self._low is None else min(self._low, low)
            self._high = high if self._high is None else max(self._high, high)
            self._done += frames
            position = stop
            if self._done == self._end:
                self._close_column()

    def _close_column(self) -> None:
        assert self._low is not None and self._high is not None
        self.lows.append((self._low - 128) / self._scale)
        self.highs.append((self._high - 128) / self._scale)
        self._low = self._high = None
        self._column += 1
        self._end = self._column_end(self._column)

    def result(self) -> Peaks:
        """The columns read; a column begun but not finished (the stream was shorter than declared) counts."""
        lows, highs = list(self.lows), list(self.highs)
        if self._low is not None and self._high is not None:
            lows.append((self._low - 128) / self._scale)
            highs.append((self._high - 128) / self._scale)
        return Peaks(lows, highs)


def reduce(chunks: Iterable[bytes], info: SourceInfo, columns: int = COLUMNS) -> Peaks:
    reducer = PeakReducer(info.samples, info.channels, info.bits, columns)
    for chunk in chunks:
        reducer.feed(chunk)
    return reducer.result()


def read_peaks(runner, path: str | Path, info: SourceInfo, cancel: CancelToken | None = None,
               columns: int = COLUMNS) -> Peaks:
    """The waveform of a whole track, read by flac. A FLAC is decoded directly (one `flac -d`); WAV and AIFF go
    through `decode_range`, so flac reads every format. Raises FlacError (Cancelled on Stop)."""
    path = Path(path)
    chunks = runner.decode(path, cancel) if info.format == "FLAC" else runner.decode_range(path, cancel=cancel)
    return reduce(chunks, info, columns)


def fit(peaks: Peaks, width: int) -> list[tuple[float, float]]:
    """The peaks reduced (or stretched) to `width` pixel columns: each pixel takes the lowest low and the highest
    high of the columns it covers."""
    count = len(peaks)
    if count == 0 or width <= 0:
        return []
    result = []
    for x in range(width):
        first = x * count // width
        last = max(first + 1, (x + 1) * count // width)
        result.append((min(peaks.lows[first:last]), max(peaks.highs[first:last])))
    return result

