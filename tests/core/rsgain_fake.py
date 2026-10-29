"""A scripted stand-in for core.rsgain.scan: it writes known track values through TaggedFile, as rsgain would,
so ReplayGain tests are fast and exact. The real rsgain runs in the parity and real-tool tests (SPEC §18)."""

from pathlib import Path

from refrain.core.replaygain import TRACK_GAIN, TRACK_PEAK
from refrain.core.rsgain import ScanResult
from refrain.core.tags import TaggedFile

# Values the real rsgain 3.6 writes for these fixtures (see the parity tests).
VALUES = {
    "01 Tone.flac": ("6.75 dB", "0.124969"),
    "02 Spike.flac": ("4.11 dB", "0.990021"),
    "03 Spike.mp3": ("4.53 dB", "0.956879"),
    "01 Loud.flac": ("-10.40 dB", "0.900024"),
    "02 Quiet.mp3": ("7.20 dB", "0.118866"),
}


class FakeScanner:
    def __init__(self, values=None, result=None, before=None):
        self.values = VALUES if values is None else values
        self.result = result
        self.before = before  # called with the folder before tagging
        self.calls = []

    def __call__(self, files, folder, target, exe, cancel=None, on_line=None):
        self.calls.append((list(files), Path(folder), target, exe))
        if self.result is not None:
            return self.result
        for name in files:
            if name in self.values:
                gain, peak = self.values[name]
                tagged = TaggedFile(Path(folder) / name)
                tagged.set_replaygain(TRACK_GAIN, gain)
                tagged.set_replaygain(TRACK_PEAK, peak)
                tagged.save()
        if on_line:
            on_line("fake rsgain done")
        if self.before:
            self.before(Path(folder))
        return ScanResult(0, "done\n", "", 0.5)
