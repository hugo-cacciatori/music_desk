"""core/fsops.py: reads that never block Explorer."""

import os

import pytest

from music_desk.core.fsops import open_shared
from music_desk.core.tags import TaggedFile


def test_a_file_being_read_can_be_deleted_and_renamed(tmp_path):
    path = tmp_path / "cover.jpg"
    path.write_bytes(b"0123456789")
    with open_shared(path) as f:
        assert f.read(4) == b"0123"
        os.rename(path, tmp_path / "renamed.jpg")  # what Explorer does meanwhile
        os.remove(tmp_path / "renamed.jpg")
        assert f.read() == b"456789"  # the open handle still reads
    assert not any(tmp_path.iterdir())


def test_shared_read_errors(tmp_path):
    try:
        open_shared(tmp_path / "missing.flac")
    except OSError as e:
        assert e.filename == str(tmp_path / "missing.flac")
    else:
        raise AssertionError("expected OSError")


def test_shared_tagged_file_can_still_save(fixture_copy):
    path = fixture_copy("formats/tone.mp3")
    tagged = TaggedFile(path, shared=True)
    tagged.set_fields({"title": "Saved after a shared read"})
    tagged.save()
    assert TaggedFile(path).fields().title == "Saved after a shared read"


FORMATS = ["tone.flac", "tone.ogg", "tone.oga", "tone.opus", "tone.mp3", "tone.mp2", "tone.wav", "tone.aiff",
           "tone_aac.m4a", "tone_alac.m4a", "tone.m4b", "tone.wv", "tone.tta", "tone.wma", "tone.aac"]


@pytest.mark.parametrize("name", FORMATS)
def test_shared_reads_match_normal_reads(fixtures_dir, name):
    path = fixtures_dir / "formats" / name
    shared, normal = TaggedFile(path, shared=True), TaggedFile(path)
    assert shared.fields() == normal.fields()
    assert shared.stream_info() == normal.stream_info()
