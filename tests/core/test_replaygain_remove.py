"""Removing ReplayGain from files (SPEC §10 B, Hugo 2026-10-16): every REPLAYGAIN_* value, in every tag family,
and the comment lines the ReplayGain step writes; other tags and other comment lines stay."""

import shutil

import pytest

from music_desk.core.replaygain import (
    ALBUM_GAIN,
    ALBUM_PEAK,
    DEFAULT_OPTIONS,
    TRACK_GAIN,
    TRACK_PEAK,
    ReplayGainOptions,
    lufs_comment_pattern,
    remove_replaygain,
)
from music_desk.core.tags import TaggedFile

RG_KEYS = (TRACK_GAIN, TRACK_PEAK, ALBUM_GAIN, ALBUM_PEAK)

TAGGED = ["tone.flac", "tone.ogg", "tone.oga", "tone.opus", "tone.mp3", "tone.mp2", "tone.wav", "tone.aiff",
          "tone_aac.m4a", "tone_alac.m4a", "tone.m4b", "tone.wv", "tone.tta", "tone.wma"]


def with_replaygain(path, comment="LUFS Target : -15"):
    tagged = TaggedFile(path)
    for key in RG_KEYS:
        tagged.set_replaygain(key, "-3.21 dB" if key.endswith("gain") else "0.987654")
    tagged.set_replaygain("replaygain_reference_loudness", "89.0 dB")  # another tool's extra value
    tagged.set_comment(comment)
    tagged.save()


@pytest.mark.parametrize("name", TAGGED)
def test_every_family_loses_its_replaygain(tmp_path, fixtures_dir, name):
    path = tmp_path / name
    shutil.copy2(fixtures_dir / "formats" / name, path)
    title = TaggedFile(path).fields().title
    with_replaygain(path)
    assert TaggedFile(path).get_replaygain("replaygain_track_gain") == "-3.21 dB"
    assert remove_replaygain(path) is True
    tagged = TaggedFile(path)
    for key in (*RG_KEYS, "replaygain_reference_loudness"):
        assert tagged.get_replaygain(key) is None, key
    assert tagged.fields().comment is None  # the LUFS comment was the whole comment
    assert tagged.fields().title == title  # nothing else changed
    assert remove_replaygain(path) is False  # nothing left: the file isn't saved again


def test_other_comment_lines_stay(tmp_path, fixtures_dir):
    path = tmp_path / "tone.flac"
    shutil.copy2(fixtures_dir / "formats" / "tone.flac", path)
    with_replaygain(path, comment="Ripped from my CD\nLUFS Target : -18.5\nlike it")
    remove_replaygain(path)
    assert TaggedFile(path).fields().comment == "Ripped from my CD\nlike it"


def test_a_file_without_replaygain_is_left_alone(tmp_path, fixtures_dir):
    path = tmp_path / "tone.mp3"
    shutil.copy2(fixtures_dir / "formats" / "tone.mp3", path)
    before = path.read_bytes()
    assert remove_replaygain(path) is False
    assert path.read_bytes() == before  # not even re-saved


def test_raw_aac_has_nothing_to_remove(tmp_path, fixtures_dir):
    path = tmp_path / "tone.aac"
    shutil.copy2(fixtures_dir / "formats" / "tone.aac", path)
    assert remove_replaygain(path) is False


@pytest.mark.parametrize("line, matches", [
    ("LUFS Target : -15", True), ("LUFS Target : -15.0", True), ("  LUFS Target : -23.5 ", True),
    ("LUFS Target : -18,5", True), ("LUFS Target : loud", False), ("My LUFS Target : -15", False),
    ("lufs target : -15", False), ("", False),
])
def test_the_comment_pattern(line, matches):
    assert bool(lufs_comment_pattern(DEFAULT_OPTIONS.comment).fullmatch(line)) is matches


def test_a_custom_comment_template(tmp_path, fixtures_dir):
    path = tmp_path / "tone.flac"
    shutil.copy2(fixtures_dir / "formats" / "tone.flac", path)
    options = ReplayGainOptions(comment="Leveled to {target} LUFS (rsgain)")
    with_replaygain(path, comment="Leveled to -14 LUFS (rsgain)")
    remove_replaygain(path, options)
    assert TaggedFile(path).fields().comment is None


def test_what_a_real_run_wrote_is_removed(fixture_copy):
    """The bundled rsgain and the ReplayGain step write the tags (any key case rsgain uses); removal clears them."""
    from music_desk import paths
    from music_desk.core.replaygain import process_album

    album = fixture_copy("replaygain/Fixture Artist/Album B")
    run_log = process_album(album, exe=paths.rsgain_path())
    assert run_log.result() == "OK"
    files = [album / "01 Loud.flac", album / "02 Quiet.mp3"]
    assert all(TaggedFile(f).get_replaygain(TRACK_GAIN) for f in files)
    assert all(remove_replaygain(f) for f in files)
    for f in files:
        tagged = TaggedFile(f)
        assert all(tagged.get_replaygain(k) is None for k in RG_KEYS) and tagged.fields().comment is None
