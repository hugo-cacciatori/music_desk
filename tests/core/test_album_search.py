"""core/index/album_search.py: the Advanced search's album searches (SPEC §26), over a scanned copy of the
fixtures."""

import shutil

import pytest

from music_desk.core.index import album_search
from music_desk.core.index.roots import add_root
from music_desk.core.index.scanner import Scanner
from music_desk.core.index.store import DbWriter, connect_read
from music_desk.core.tags import TaggedFile

RG = {"replaygain_track_gain": "-6.00 dB", "replaygain_track_peak": "0.9", "replaygain_album_gain": "-6.50 dB",
      "replaygain_album_peak": "0.95"}


def tag_replaygain(path, keys=RG):
    tagged = TaggedFile(path)
    for key, value in keys.items():
        tagged.set_replaygain(key, value)
    tagged.save()


@pytest.fixture
def library(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    tone = fixtures_dir / "formats" / "tone.flac"
    # ReplayGain: every track tagged, one track untagged, no track tagged, a raw AAC (can't be tagged).
    for album, tagged in (("All tagged", 2), ("One missing", 1), ("None tagged", 0)):
        (lib / "RG" / album).mkdir(parents=True)
        for n in range(2):
            shutil.copy2(tone, lib / "RG" / album / f"0{n + 1}.flac")
            if n < tagged:
                tag_replaygain(lib / "RG" / album / f"0{n + 1}.flac")
    (lib / "RG" / "Raw").mkdir()
    shutil.copy2(fixtures_dir / "formats" / "tone.aac", lib / "RG" / "Raw" / "tone.aac")
    (lib / "RG" / "Half keys").mkdir()
    shutil.copy2(tone, lib / "RG" / "Half keys" / "01.flac")
    tag_replaygain(lib / "RG" / "Half keys" / "01.flac", {"replaygain_track_gain": "-6.00 dB"})  # partial: has RG

    split = fixtures_dir / "split"
    # Not split: an EAC-style folder (image, cue, log), a WAV image, the image with its split tracks beside it,
    # a multi-file sheet, a sheet whose image is missing, a sheet pointing to another file than the one there.
    shutil.copytree(split / "hidden_track", lib / "Rips" / "EAC")
    (lib / "Rips" / "EAC" / "Split Album.log").write_text("Exact Audio Copy V1.6", encoding="utf-8")
    shutil.copytree(split / "wav_image", lib / "Rips" / "WAV")
    shutil.copytree(split / "hidden_track", lib / "Rips" / "Already split")
    shutil.copy2(tone, lib / "Rips" / "Already split" / "01 Opening.flac")
    shutil.copytree(split / "multi_file", lib / "Rips" / "Multi")
    (lib / "Rips" / "Missing").mkdir()
    shutil.copy2(split / "hidden_track" / "Split Album.cue", lib / "Rips" / "Missing" / "Split Album.cue")
    shutil.copytree(split / "hidden_track", lib / "Rips" / "Other name")
    (lib / "Rips" / "Other name" / "Fixture Artist - Split Album.flac").rename(
        lib / "Rips" / "Other name" / "renamed.flac")
    return lib


@pytest.fixture
def read(tmp_path, library):
    with DbWriter(tmp_path / "db" / "library.db") as writer:
        Scanner(writer).scan_root(add_root(writer, library).root_id)
        conn = connect_read(writer.path)
        yield conn
        conn.close()


def names(results, library):
    return [r.path.relative_to(library).as_posix() for r in results]


def test_albums_without_replaygain(read, library):
    found = names(album_search.run(read, album_search.WITHOUT_REPLAYGAIN), library)
    assert "RG/One missing" in found and "RG/None tagged" in found
    assert not {"RG/All tagged", "RG/Raw", "RG/Half keys"} & set(found)
    assert found == sorted(found, key=str.casefold)


def test_albums_not_split(read, library):
    found = names(album_search.run(read, album_search.NOT_SPLIT), library)
    assert found == ["Rips/EAC", "Rips/WAV"]


def test_a_folder_result_carries_its_entry(read, library):
    result = album_search.run(read, album_search.NOT_SPLIT)[0]
    assert result.path == library / "Rips" / "EAC" and isinstance(result.folder_id, int)


def test_an_unknown_search_is_refused(read):
    with pytest.raises(ValueError):
        album_search.run(read, "nope")


def test_the_not_split_search_stops_when_cancelled(read):
    assert album_search.run(read, album_search.NOT_SPLIT, cancelled=lambda: True) == []
