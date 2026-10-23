"""A Monkey's Audio .ape is a plain file everywhere (SPEC §1 "Ignored", Hugo 2026-10-22): an EAC folder's .ape
beside its .flac image must not count as audio in the index, Rename, ReplayGain, Play folder or playlist drops."""

import shutil

import pytest

from music_desk.core import cue
from music_desk.core.formats import AUDIO_EXTENSIONS, is_audio
from music_desk.core.index import queries
from music_desk.core.index.roots import add_root
from music_desk.core.index.scanner import Scanner
from music_desk.core.index.store import DbWriter, connect_read
from music_desk.core.metadata import LOSSLESS_EXTENSIONS
from music_desk.core.rename import options_from_json, plan_renames
from music_desk.core.replaygain import list_audio_files


@pytest.fixture
def album(tmp_path, fixtures_dir):
    """An EAC-like folder: a FLAC image, its sheet, and a .ape (a Monkey's Audio header is enough)."""
    folder = tmp_path / "lib" / "Album"
    folder.mkdir(parents=True)
    shutil.copy2(fixtures_dir / "formats" / "tone.flac", folder / "image.flac")
    (folder / "image.ape").write_bytes(b"MAC " + bytes(60))
    (folder / "Other.APE").write_bytes(b"MAC " + bytes(60))
    return folder


@pytest.fixture
def indexed(tmp_path, album):
    with DbWriter(tmp_path / "db" / "library.db") as writer:
        root_id = add_root(writer, album.parent).root_id
        Scanner(writer).scan_root(root_id)
        conn = connect_read(writer.path)
        yield conn
        conn.close()


def ids(conn) -> dict[str, int]:
    return {name: i for i, name in conn.execute("SELECT id, name FROM entries WHERE parent_id IS NOT NULL")}


def test_ape_is_not_audio():
    assert ".ape" not in AUDIO_EXTENSIONS and ".ape" not in LOSSLESS_EXTENSIONS
    assert not is_audio("image.ape") and not is_audio("IMAGE.APE")


def test_index_lists_it_as_a_file_without_a_track(indexed):
    entries = ids(indexed)
    assert {"image.flac", "image.ape", "Other.APE"} <= set(entries)
    tracks = {row[0] for row in indexed.execute("SELECT entry_id FROM tracks")}
    assert tracks == {entries["image.flac"]}
    n_tracks, formats = indexed.execute("SELECT n_tracks, formats FROM folder_stats WHERE entry_id = ?",
                                        (entries["Album"],)).fetchone()
    assert (n_tracks, formats) == (1, "FLAC")
    found = indexed.execute("SELECT rowid FROM search WHERE search MATCH '\"image\"'").fetchall()
    assert {row[0] for row in found} == {entries["image.flac"], entries["image.ape"]}  # findable by name


def test_play_folder_and_playlist_drops_leave_it_out(indexed):
    entries = ids(indexed)
    assert [e.path.name for e in queries.playlist_for_folder(indexed, entries["Album"])] == ["image.flac"]
    assert queries.track_ids_for(indexed, [entries["Album"]]) == [entries["image.flac"]]
    assert queries.track_ids_for(indexed, [entries["image.ape"], entries["Other.APE"]]) == []


def test_rename_leaves_it_alone(album):
    plan = plan_renames(album)
    assert [item.source.name for item in plan.items if item.is_audio] == ["image.flac"]
    assert not any(item.source.suffix.lower() == ".ape" for item in plan.items)


def test_replaygain_does_not_list_it(album):
    supported, unsupported = list_audio_files(album)
    assert supported == ["image.flac"] and unsupported == []


def test_saved_music_types_drop_it():
    options = options_from_json({"audio_extensions": [".flac", ".ape", ".APE", ".mp3"]})
    assert options.audio_extensions == {".flac", ".mp3"}


def test_split_still_refuses_a_sheet_naming_it(album):
    with pytest.raises(cue.CueError, match="APE isn't supported"):
        cue.read_source(album / "image.ape")
