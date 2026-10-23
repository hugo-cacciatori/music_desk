"""ReplayGain on loose tracks (SPEC §31, the right-click menu only): each track alone is its own album, with no
subfolder copies and no log file. The fake scanner writes the values the real rsgain writes for these fixtures."""

from datetime import datetime
from pathlib import Path

import pytest
from rsgain_fake import FakeScanner

from music_desk.core.cancel import CancelToken
from music_desk.core.replaygain import (
    ALBUM_GAIN,
    ALBUM_PEAK,
    TRACK_GAIN,
    ReplayGainOptions,
    process_loose_tracks,
)
from music_desk.core.rsgain import ScanResult
from music_desk.core.tags import TaggedFile

NOW = datetime(2026, 1, 2, 3, 4, 5)
EXE = Path(r"C:\fake\rsgain.exe")


def run(tracks, scanner=None, **kwargs):
    return process_loose_tracks(tracks, scanner=scanner or FakeScanner(), exe=EXE, now=lambda: NOW, **kwargs)


def rg(path, key):
    return TaggedFile(path).get_replaygain(key)


@pytest.fixture
def album(fixture_copy):
    return fixture_copy("replaygain/Fixture Artist/Album A")


def test_each_track_is_its_own_album(album):
    scanner = FakeScanner()
    result = run([album / "02 Spike.flac", album / "03 Spike.mp3"], scanner)
    assert [call[0] for call in scanner.calls] == [["02 Spike.flac"], ["03 Spike.mp3"]]  # one at a time
    assert result.result() == "OK"
    # The clipping fix lowers each track gain; the album values are the track's own, and the comment is written.
    for name, gain, peak in (("02 Spike.flac", "0.08 dB", "0.990021"), ("03 Spike.mp3", "0.37 dB", "0.956879")):
        assert rg(album / name, TRACK_GAIN) == gain
        assert (rg(album / name, ALBUM_GAIN), rg(album / name, ALBUM_PEAK)) == (gain, peak)
        assert TaggedFile(album / name).fields().comment == "LUFS Target : -15"
    # The rest of the folder is left alone: the unselected track, the subfolders, and no log is written.
    assert rg(album / "01 Tone.flac", ALBUM_GAIN) is None
    assert rg(album / "lite" / "02 Spike.flac", ALBUM_GAIN) is None
    assert not (album / "replaygain.log").exists()


def test_the_report_lists_every_track(album):
    result = run([album / "02 Spike.flac", album / "01 Tone.flac"])
    text = result.text()
    assert text.startswith("ReplayGain, each track on its own (target -15 LUFS): 2 tracks processed\n")
    assert text.count("=" * 78) == 2 and "Folder: " + str(album) in text
    assert "Album: the track's own values, gain 0.08 dB, peak 0.990021" in text
    assert "Album: the track's own values, gain 6.75 dB, peak 0.124969" in text
    assert "Subfolders:" not in text and "reduced to prevent clipping" in text
    assert text.endswith("\nResult: OK\n")


def test_files_rsgain_cant_scan_and_missing_ones_are_left_out(album, tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("x", encoding="utf-8")
    scanner = FakeScanner()
    result = run([notes, album / "gone.flac", album / "01 Tone.flac"], scanner)
    assert len(scanner.calls) == 1 and result.result() == "OK"
    assert result.skipped == [(notes, "skipped, format not supported by rsgain"),
                              (album / "gone.flac", "skipped, not found")]
    assert f"Left out:\n  {notes}: skipped, format not supported by rsgain\n" in result.text()
    assert run([notes]).result() == "NOTHING TO DO"


def test_a_cancel_stops_after_the_current_track(album):
    cancel = CancelToken()
    scanner = FakeScanner(before=lambda _folder: cancel.cancel())
    tracks = [album / "01 Tone.flac", album / "02 Spike.flac", album / "03 Spike.mp3"]
    result = run(tracks, scanner, cancel=cancel)
    assert len(scanner.calls) == 1 and result.logs[0].result() == "OK"  # the current track is finished
    assert result.not_run == tracks[1:] and result.result() == "CANCELLED"
    assert f"  {tracks[1]}: not processed, cancelled" in result.text()
    assert rg(tracks[0], ALBUM_GAIN) == "6.75 dB" and rg(tracks[1], ALBUM_GAIN) is None


def test_a_failed_scan_touches_no_tag(album):
    result = run([album / "01 Tone.flac"], FakeScanner(result=ScanResult(1, "", "boom\n", 0.2)))
    assert result.logs[0].result() == "STOPPED" and result.result() == "DONE WITH ERRORS"
    assert rg(album / "01 Tone.flac", ALBUM_GAIN) is None
    assert "Album:" not in result.text()


def test_the_target_set_in_the_options(album):
    scanner = FakeScanner()
    run([album / "01 Tone.flac"], scanner, options=ReplayGainOptions(target=-18.0))
    assert scanner.calls[0][2] == "-18"
    assert TaggedFile(album / "01 Tone.flac").fields().comment == "LUFS Target : -18"
