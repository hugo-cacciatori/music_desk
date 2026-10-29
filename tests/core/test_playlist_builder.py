"""The playlist builder's core (SPEC §15): the model, its persistence, the import order from the index, the rows,
and the .m3u8 export with relative or absolute paths."""

import json
import shutil
from pathlib import Path

import pytest

from refrain.core.index import queries
from refrain.core.index.roots import add_root
from refrain.core.index.scanner import Scanner
from refrain.core.index.store import DbWriter, connect_read, path_of
from refrain.core.playlist_builder import Builder, Item
from refrain.core.playlists import PlaylistEntry, export_playlist, playlist_path, render


def items(*names) -> list[Item]:
    return [Item(i, f"C:\\Music\\{name}") for i, name in enumerate(names, start=1)]


def names(builder: Builder) -> list[str]:
    return [Path(item.path).name for item in builder.items]


# -- the model --


def test_add_at_the_end_or_a_position_and_skip_duplicates():
    b = Builder()
    a, bb, c, d = items("a", "b", "c", "d")
    assert b.add([a, bb]) == (2, 0)
    assert b.add([c], position=1) == (1, 0)
    assert names(b) == ["a", "c", "b"]
    assert b.add([a, d, d]) == (1, 2)  # a is there already, d is given twice
    assert names(b) == ["a", "c", "b", "d"]
    assert b.add([Item(None, "C:\\Music\\X.flac")]) == (1, 0)
    assert b.add([Item(None, "c:\\music\\x.FLAC")]) == (0, 1)  # a missing file: the same path ignoring case


def test_move_rows_by_keyboard():
    b = Builder(items("a", "b", "c", "d", "e"))
    assert b.move_rows([1, 3], -1) == [0, 2]
    assert names(b) == ["b", "a", "d", "c", "e"]
    assert b.move_rows([0, 2], -1) == [0, 2] and names(b) == ["b", "a", "d", "c", "e"]  # at the top: no move
    assert b.move_rows([3, 4], 1) == [3, 4]  # at the bottom: no move
    assert b.move_rows([2, 3], 1) == [3, 4] and names(b) == ["b", "a", "e", "d", "c"]
    assert b.move_rows([], 1) == [] and b.move_rows([9], -1) == []


def test_move_to_by_drag():
    b = Builder(items("a", "b", "c", "d", "e"))
    assert b.move_to([0, 1], 4) == [2, 3] and names(b) == ["c", "d", "a", "b", "e"]  # before "e"
    assert b.move_to([4], 0) == [0] and names(b) == ["e", "c", "d", "a", "b"]
    assert b.move_to([1, 3], 99) == [3, 4] and names(b) == ["e", "d", "b", "c", "a"]  # past the end
    assert b.move_to([], 0) == []


def test_remove_clear_and_sort():
    b = Builder(items("c", "a", "b"))
    b.sort(key=lambda item: Path(item.path).name)
    assert names(b) == ["a", "b", "c"]
    b.sort(key=lambda item: Path(item.path).name, reverse=True)
    assert names(b) == ["c", "b", "a"]
    b.remove([0, 2, 7])
    assert names(b) == ["b"]
    b.clear()
    assert len(b) == 0


def test_save_and_load(tmp_path):
    path = tmp_path / "app" / "playlist.json"
    b = Builder([Item(3, "C:\\Music\\Björk\\Jóga.flac"), Item(None, "D:\\gone.mp3")])
    b.save(path)
    assert Builder.load(path).items == b.items
    assert not (tmp_path / "app" / "playlist.json.tmp").exists()
    assert len(Builder.load(tmp_path / "missing.json")) == 0


@pytest.mark.parametrize("text", ["{not json", "[1, 2]", '{"items": 3}',
                                  '{"items": [{"id": true, "path": "C:/a.flac"}, {"id": 2}, 5, {"path": 7}]}'])
def test_malformed_saves_never_fail(tmp_path, text):
    path = tmp_path / "playlist.json"
    path.write_text(text, encoding="utf-8")
    b = Builder.load(path)
    assert all(isinstance(item.path, str) for item in b.items)
    if "C:/a.flac" in text:
        assert b.items == [Item(None, "C:/a.flac")]  # a bool isn't an id


def test_resolve_follows_the_index():
    b = Builder([Item(1, "C:\\old.flac"), Item(2, "C:\\gone.flac")])
    b.resolve(lambda item: Item(1, "C:\\new.flac") if item.entry_id == 1 else Item(None, item.path))
    assert b.items == [Item(1, "C:\\new.flac"), Item(None, "C:\\gone.flac")]
    assert json.loads(json.dumps(b.to_json()))["version"] == 1


# -- from the index --

ALBUM = "Fixture Artist - Fixture Album"


@pytest.fixture
def indexed(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "album" / ALBUM, lib / ALBUM)
    (lib / ALBUM / "cover.jpg").write_bytes(b"jpg")
    with DbWriter(tmp_path / "db" / "library.db") as writer:
        Scanner(writer).scan_root(add_root(writer, lib).root_id)
        conn = connect_read(writer.path)
        yield lib, conn
        conn.close()


def entry(conn, path: Path) -> int:
    return next(i for (i,) in conn.execute("SELECT id FROM entries") if path_of(conn, i) == path)


def test_a_dropped_folder_brings_its_tracks_in_explorer_order(indexed):
    lib, conn = indexed
    album = lib / ALBUM
    ids = queries.track_ids_for(conn, [entry(conn, album)])
    paths = [path_of(conn, i).relative_to(album).as_posix() for i in ids]
    assert paths[:3] == ["01 First.flac", "bad_chars.flac", "clash.flac"]  # files first, natural order
    assert paths.index("CD2/01 Disc Two.flac") > paths.index("track6.FLAC")  # then subfolders
    assert all(not p.endswith(".jpg") for p in paths)  # tracks only
    assert len(paths) == conn.execute("SELECT count(*) FROM tracks").fetchone()[0]  # every track under it


def test_dropped_items_keep_their_order(indexed):
    lib, conn = indexed
    album = lib / ALBUM
    song, cd2 = entry(conn, album / "track6.FLAC"), entry(conn, album / "CD2")
    ids = queries.track_ids_for(conn, [song, cd2, entry(conn, album / "cover.jpg"), 99999])
    assert [path_of(conn, i).name for i in ids] == ["track6.FLAC", "01 Disc Two.flac"]


def test_track_rows(indexed):
    lib, conn = indexed
    first = entry(conn, lib / ALBUM / "01 First.flac")
    rows = queries.track_rows(conn, [first, 99999])
    row = rows[first]
    assert list(rows) == [first]
    assert (row.title, row.artist, row.album, row.name) == ("First", "Fixture Artist", "Fixture Album", "01 First.flac")
    assert row.path == lib / ALBUM / "01 First.flac" and row.folder == str(lib / ALBUM)


# -- export --


def test_paths_relative_absolute_and_another_drive():
    song = Path("D:\\Music\\Album\\01 Song.flac")
    assert playlist_path(song, None) == "D:\\Music\\Album\\01 Song.flac"
    assert playlist_path(song, Path("D:\\Music")) == "Album\\01 Song.flac"
    assert playlist_path(song, Path("D:\\Playlists")) == "..\\Music\\Album\\01 Song.flac"
    assert playlist_path(song, Path("E:\\Playlists")) == "D:\\Music\\Album\\01 Song.flac"  # no relative path exists
    assert playlist_path(song, Path("d:\\music")) == "Album\\01 Song.flac"  # drive letters ignore case


def test_export_writes_utf8_m3u8(tmp_path):
    music = tmp_path / "Music" / "Björk"
    music.mkdir(parents=True)
    entries = [PlaylistEntry(music / "Jóga.flac", 305.4, "Björk", "Jóga"), PlaylistEntry(music / "notes.txt")]
    target = tmp_path / "Music" / "mix.m3u8"
    export_playlist(entries, target, relative=True)
    assert target.read_bytes().decode("utf-8") == "#EXTM3U\r\n#EXTINF:305,Björk - Jóga\r\nBjörk\\Jóga.flac\r\n"
    export_playlist(entries, target, relative=False)  # replaces the file
    assert target.read_text(encoding="utf-8").splitlines()[2] == str(music / "Jóga.flac")
    assert not target.with_name("mix.m3u8.tmp").exists()
    assert render(entries) == render(entries, None)
