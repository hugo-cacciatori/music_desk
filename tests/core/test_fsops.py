"""core/fsops.py: reads that never block Explorer."""

import os
from pathlib import Path

import pytest

from music_desk.core.fsops import CannotRecycle, can_recycle, open_shared, send_to_recycle_bin
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


# -- the Recycle Bin only (SPEC §19); the real bin is never used here --

def test_send_to_recycle_bin_refuses_where_windows_cant_recycle(tmp_path):
    sent = []
    with pytest.raises(CannotRecycle, match="network location"):
        send_to_recycle_bin(tmp_path / "a.txt", check=lambda _p: "X: is a network location", send=sent.append)
    assert sent == []


def test_send_to_recycle_bin_sends_when_the_check_passes(tmp_path):
    sent = []
    send_to_recycle_bin(tmp_path / "a.txt", check=lambda _p: None, send=sent.append)
    assert sent == [str(tmp_path / "a.txt")]


def test_cannot_recycle_is_an_os_error():
    assert issubclass(CannotRecycle, OSError)  # callers that catch OSError never miss it


def test_can_recycle_a_local_file(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("x", encoding="utf-8")
    assert can_recycle(path) is None


def test_cannot_recycle_on_a_network_path(tmp_path):
    """Measured in M8 task 1: send2trash deletes such a file for good. Only reads (no file is sent)."""
    drive, rest = str(tmp_path).split(":", 1)
    reason = can_recycle(Path(rf"\\localhost\{drive}$" + rest))
    assert reason is not None and "network location" in reason
