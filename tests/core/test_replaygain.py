"""core/replaygain.py with a fake scanner: the post-processing, the RunLog and discography (SPEC §10B, §17).

The fake scanner writes known track values through TaggedFile, as rsgain would, so these tests are fast and exact.
The real rsgain runs in test_replaygain_parity.py.
"""

import shutil
from datetime import datetime
from pathlib import Path

import pytest
from rsgain_fake import VALUES, FakeScanner  # noqa: F401 - VALUES is used by the tests below

from music_desk.core.cancel import CancelToken
from music_desk.core.replaygain import (
    ALBUM_GAIN,
    ALBUM_PEAK,
    TRACK_GAIN,
    ReplayGainOptions,
    files_summary,
    find_album_folders,
    level_album,
    process_album,
    process_discography,
)
from music_desk.core.rsgain import ScanResult
from music_desk.core.tags import TaggedFile

ARTIST = "replaygain/Fixture Artist"
NOW = datetime(2026, 1, 2, 3, 4, 5)
EXE = Path(r"C:\fake\rsgain.exe")

def run_album(folder, scanner=None, **kwargs):
    return process_album(folder, scanner=scanner or FakeScanner(), exe=EXE, now=lambda: NOW, **kwargs)


def rg(path, key):
    return TaggedFile(path).get_replaygain(key)


def log_text(folder):
    return (folder / "replaygain.log").read_text(encoding="utf-8")


@pytest.fixture
def album_a(fixture_copy):
    return fixture_copy(f"{ARTIST}/Album A")


def test_album_run(album_a):
    scanner = FakeScanner()
    run_log = run_album(album_a, scanner)

    files, folder, target, exe = scanner.calls[0]
    assert sorted(files) == ["01 Tone.flac", "02 Spike.flac", "03 Spike.mp3"]
    assert (folder, target, exe) == (album_a, "-15", EXE)
    assert run_log.result() == "OK"

    # Clipping: the spikes' gains are lowered; the album gets the lowest gain and highest peak.
    assert rg(album_a / "02 Spike.flac", TRACK_GAIN) == "0.08 dB"
    assert rg(album_a / "03 Spike.mp3", TRACK_GAIN) == "0.37 dB"
    assert rg(album_a / "01 Tone.flac", TRACK_GAIN) == "6.75 dB"
    for name in ("01 Tone.flac", "02 Spike.flac", "03 Spike.mp3"):
        assert rg(album_a / name, ALBUM_GAIN) == "0.08 dB"
        assert rg(album_a / name, ALBUM_PEAK) == "0.990021"
        assert TaggedFile(album_a / name).fields().comment == "LUFS Target : -15"

    # Subfolders: album data everywhere, track data only in lite, for same-named files.
    for path in ("lite/01 Tone.flac", "lite/02 Spike.flac", "bonus/01 Tone.flac", "CD2/01 Disc Two.flac"):
        assert rg(album_a / path, ALBUM_GAIN) == "0.08 dB", path
    assert rg(album_a / "lite/02 Spike.flac", TRACK_GAIN) == "0.08 dB"
    assert rg(album_a / "lite/01 Tone.flac", TRACK_GAIN) == "6.75 dB"
    assert rg(album_a / "bonus/01 Tone.flac", TRACK_GAIN) is None

    text = log_text(album_a)
    assert text.startswith("=" * 78 + "\n")
    lines = text.splitlines()
    assert lines[1] == rf"Run 2026-01-02 03:04:05   target -15 LUFS   rsgain: {EXE}"
    assert lines[2] == f"Folder: {album_a}"
    assert lines[3] == "Files: 3 (2 FLAC, 1 MP3)"
    assert lines[4] == "Scan: OK (0.5 s)"
    assert "Album: gain 0.08 dB (lowest track gain), peak 0.990021 (highest track peak)" in lines
    assert {"  lite: 2/2 files got the album data, 2 also got their track data",
            "  bonus: 1/1 files got the album data", "  CD2: 1/1 files got the album data"} <= set(lines)
    spike_row = next(line for line in lines if line.startswith("02 Spike.flac"))
    assert spike_row.split() == ["02", "Spike.flac", "-19.11", "LUFS", "-0.09", "dB", "+4.11", "dB", "+0.08", "dB",
                                 "reduced", "to", "prevent", "clipping"]
    assert "Warnings / errors:\n  none\n" in text
    assert lines[-1] == ""  # the script ends each block with a blank line
    assert lines[-2].startswith("Result: OK. 3 tracks, 2 clipping fixes, 0 warnings, 0 errors, ")


def test_each_run_replaces_the_log(album_a):
    """Hugo (2026-10-16): replaygain.log holds the last run only; the script appended (SPEC §17)."""
    (album_a / "replaygain.log").write_text("an older run", encoding="utf-8")
    run_album(album_a)
    run_album(album_a)
    text = log_text(album_a)
    assert text.count("=" * 78) == 1 and "an older run" not in text


def test_a_discography_run_replaces_each_log_once(artist):
    album = artist / "Album A"
    (album / "replaygain.log").write_text("an older run", encoding="utf-8")
    (artist / "replaygain.log").write_text("an older summary", encoding="utf-8")
    process_discography(artist, scanner=FakeScanner(), exe=EXE, now=lambda: NOW)
    text = log_text(album)
    assert "an older run" not in text and text.count("=" * 78) == 1
    assert "Discography leveling" in text  # added after this run's report, not replacing it
    summary = (artist / "replaygain.log").read_text(encoding="utf-8")
    assert "an older summary" not in summary


def test_an_artist_folder_that_is_also_an_album_keeps_its_report(artist):
    import shutil

    shutil.copy2(artist / "Album A" / "01 Tone.flac", artist / "Loose Tone.flac")  # the artist folder has audio
    process_discography(artist, scanner=FakeScanner(), exe=EXE, now=lambda: NOW)
    text = (artist / "replaygain.log").read_text(encoding="utf-8")
    # the artist folder is one of the run's albums (it holds audio): its report stays, the summary follows it
    assert "Loose Tone.flac" in text and text.index("Loose Tone.flac") < text.rindex("Discography")


def test_missing_and_invalid_values_are_warnings(album_a):
    run_log = run_album(album_a, FakeScanner({"01 Tone.flac": ("6.75 dB", "0.124969"),
                                              "02 Spike.flac": ("loud", "0.99")}))
    assert run_log.result() == "OK"
    text = log_text(album_a)
    assert "WARNING  Missing ReplayGain track values in 03 Spike.mp3, skipping" in text
    assert "WARNING  Invalid ReplayGain value in 02 Spike.flac: gain='loud', peak='0.99', skipping" in text
    assert run_log.tracks["03 Spike.mp3"]["note"] == "skipped: no ReplayGain data"
    assert rg(album_a / "01 Tone.flac", ALBUM_GAIN) == "6.75 dB"


def test_peak_zero(album_a):
    run_log = run_album(album_a, FakeScanner({name: ("6.00 dB", "0.000000") for name in VALUES}))
    assert run_log.tracks["01 Tone.flac"]["note"] == "peak is 0"
    assert "Track peak is 0 in 01 Tone.flac, clipping check skipped" in log_text(album_a)


def test_failed_scan_stops_without_touching_tags(album_a):
    result = ScanResult(1, "\x1b[32mpartial\x1b[0m\n", "\x1b[31mboom\x1b[0m\n", 0.2)
    run_log = run_album(album_a, FakeScanner(result=result))
    assert run_log.result() == "STOPPED"
    text = log_text(album_a)
    assert "Scan: FAILED (exit code 1)\n    partial\n    boom\n" in text
    assert "ERROR    rsgain scan failed (exit code 1):\n                    boom" in text
    assert "Album:" not in text
    assert rg(album_a / "01 Tone.flac", ALBUM_GAIN) is None


def test_rsgain_not_found(album_a, tmp_path):
    run_log = process_album(album_a, exe=tmp_path / "missing.exe", now=lambda: NOW)
    assert run_log.scan == "FAILED (rsgain not found)"
    assert f"rsgain not found at {tmp_path / 'missing.exe'}" in log_text(album_a)


def test_command_line_too_long(album_a):
    run_log = run_album(album_a, FakeScanner(result=ScanResult(None, "", "", 0.0, error="command line too long")))
    assert run_log.result() == "STOPPED"
    assert "Scan: FAILED (command line too long)" in log_text(album_a)


def test_cancelled_scan(album_a):
    run_log = run_album(album_a, FakeScanner(result=ScanResult(1, "", "", 0.1, cancelled=True)))
    assert run_log.result() == "STOPPED"
    assert "Scan: CANCELLED" in log_text(album_a)
    assert "Cancelled: rsgain was stopped" in log_text(album_a)


def test_crash_is_recorded_not_raised(album_a):
    def explode(*args, **kwargs):
        raise RuntimeError("kaboom")

    run_log = run_album(album_a, explode)
    assert run_log.result() == "CRASHED"
    text = log_text(album_a)
    assert "ERROR    Unexpected crash:" in text and "RuntimeError: kaboom" in text


def test_unreadable_file_after_scan_is_done_with_errors(album_a):
    def corrupt(folder):
        (folder / "03 Spike.mp3").write_bytes(b"\0" * 100)

    run_log = run_album(album_a, FakeScanner(before=corrupt))
    assert run_log.result() == "DONE WITH ERRORS"
    assert "Clipping check failed for 03 Spike.mp3" in log_text(album_a)


def test_unsupported_formats_are_skipped_with_a_warning(album_a, fixtures_dir):
    shutil.copy2(fixtures_dir / "formats" / "tone.tta", album_a / "04 Tone.tta")
    scanner = FakeScanner()
    run_album(album_a, scanner)
    assert "04 Tone.tta" not in scanner.calls[0][0]
    assert "WARNING  04 Tone.tta: skipped, format not supported by rsgain" in log_text(album_a)


def test_no_scannable_files(tmp_path):
    assert run_album(tmp_path) is None
    assert not (tmp_path / "replaygain.log").exists()


def test_files_summary():
    assert files_summary(["a.flac", "b.mp3", "c.opus", "d.OPUS", "e.FLAC"]) == "5 (2 FLAC, 1 MP3, 2 OPUS)"
    assert files_summary(["a.flac"]) == "1 (1 FLAC, 0 MP3)"


def test_custom_target_and_comment(album_a):
    options = ReplayGainOptions(target=-18.5, comment="Target {target}")
    scanner = FakeScanner()
    process_album(album_a, options, scanner=scanner, exe=EXE, now=lambda: NOW)
    assert scanner.calls[0][2] == "-18.5"
    assert TaggedFile(album_a / "01 Tone.flac").fields().comment == "Target -18.5"


# ---------------------------------------------------------------------------
# Discography
# ---------------------------------------------------------------------------

@pytest.fixture
def artist(fixture_copy):
    return fixture_copy(ARTIST)


def test_find_album_folders_prunes_keyword_subfolders(artist):
    assert find_album_folders(artist, ReplayGainOptions()) == [artist / "Album A", artist / "Album B"]


def test_discography(artist):
    result = process_discography(artist, scanner=FakeScanner(), exe=EXE, now=lambda: NOW)
    assert result.target == -10.4
    by_name = {album.name: album for album in result.albums}
    assert (by_name["Album A"].gain, by_name["Album A"].changed) == (0.08, 7)  # 3 files + lite 2 + bonus + CD2
    assert (by_name["Album B"].gain, by_name["Album B"].changed) == (-10.4, 0)

    for path in ("Album A/01 Tone.flac", "Album A/lite/02 Spike.flac", "Album A/CD2/01 Disc Two.flac",
                 "Album B/02 Quiet.mp3"):
        assert rg(artist / path, ALBUM_GAIN) == "-10.4 dB", path

    a_log = log_text(artist / "Album A")
    assert a_log.endswith("Discography leveling 2026-01-02 03:04:05: album gain 0.08 dB -> -10.4 dB, "
                          "7 files changed\n")
    assert log_text(artist / "Album B").endswith(
        "album gain already at the discography target (-10.4 dB), 0 files changed\n")

    summary = log_text(artist).splitlines()
    assert summary[1] == "Discography run 2026-01-02 03:04:05   target -15 LUFS"
    assert summary[2] == f"Folder: {artist}"
    assert summary[3] == "Albums: 2 (2 leveled, 0 not leveled)"
    assert summary[5:8] == ["Album    Result  Album gain  Leveled to  Files changed",
                            "Album A  OK      +0.08 dB    -10.40 dB   7",
                            "Album B  OK      -10.40 dB   -10.40 dB   0"]
    assert "Discography album gain: -10.4 dB (lowest album gain)" in summary
    assert summary[-2].startswith("Result: OK. 2 albums, 2 leveled, ")


def test_discography_cancel_stops_before_the_next_album_and_skips_leveling(artist):
    token = CancelToken()
    scanner = FakeScanner(before=lambda folder: token.cancel())
    result = process_discography(artist, scanner=scanner, exe=EXE, cancel=token, now=lambda: NOW)
    assert len(scanner.calls) == 1
    assert result.cancelled and result.target is None
    assert [album.result for album in result.albums] == ["OK", "CANCELLED"]
    assert all(album.changed is None for album in result.albums)
    summary = log_text(artist)
    assert "Discography album gain: none, the run was cancelled before leveling" in summary
    assert "Album B: CANCELLED, not leveled." in summary
    assert rg(artist / "Album A/01 Tone.flac", ALBUM_GAIN) == "0.08 dB"  # not leveled


def test_level_album_skips_files_without_album_gain(artist):
    run_album(artist / "Album B")
    tagged = TaggedFile(artist / "Album B" / "02 Quiet.mp3")
    for frame in [f for f in tagged.audio.tags.getall("TXXX") if f.desc.lower() == ALBUM_GAIN]:
        tagged.audio.tags.delall(frame.HashKey)
    tagged.save()
    assert level_album(artist / "Album B", -20.0, ReplayGainOptions()) == (1, 1, [])


def test_discography_with_no_album(tmp_path):
    assert process_discography(tmp_path, scanner=FakeScanner(), exe=EXE) is None


# -- the preview (the Tools page) --

def test_discography_reports_each_album_as_it_goes(artist):
    seen = []
    process_discography(artist, scanner=FakeScanner(), exe=EXE, now=lambda: NOW, on_album=seen.append)
    assert [run_log.folder.name for run_log in seen] == ["Album A", "Album B"]
    assert all(run_log.result() == "OK" for run_log in seen)
