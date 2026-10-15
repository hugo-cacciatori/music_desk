"""core/metadata.py: .lrc sidecars and their encodings (SPEC §7)."""

import shutil

from music_desk.core.metadata import decode_text, lrc_sidecar, read_snapshot


def test_decode_text():
    assert decode_text("Café".encode()) == "Café"
    assert decode_text(b"\xef\xbb\xbf[00:01.00]BOM") == "[00:01.00]BOM"  # the BOM is dropped
    assert decode_text("Café".encode("cp1252")) == "Café"  # old Windows tools
    assert decode_text(b"\x81") == "�"  # undecodable, but never an error


def test_sidecar_matches_the_stem_ignoring_case(tmp_path, fixtures_dir):
    audio = tmp_path / "01 Song.flac"
    shutil.copy2(fixtures_dir / "formats" / "tone.flac", audio)
    assert lrc_sidecar(audio) is None
    (tmp_path / "01 song.LRC").write_bytes(b"[00:01.00]one")
    (tmp_path / "01 Song (live).lrc").write_bytes(b"other")
    assert lrc_sidecar(audio) == ("01 song.LRC", "[00:01.00]one")


def test_snapshot_carries_the_sidecar(fixtures_dir):
    snapshot = read_snapshot(fixtures_dir / "cleanup" / "lrc" / "no_lyrics.flac")
    assert snapshot.lrc_name == "no_lyrics.lrc"
    assert snapshot.lrc_text.startswith("[00:00.00]Fixture lyrics line one")
    assert read_snapshot(fixtures_dir / "formats" / "tone.flac").lrc_name is None
