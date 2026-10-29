"""core/index/roots.py: adding, merging, removing roots and the online check (SPEC §3)."""

import shutil
from datetime import datetime

import pytest

from refrain.core.index.roots import (
    AddStatus,
    add_root,
    check_online,
    is_inside,
    list_roots,
    normalize,
    remove_root,
)
from refrain.core.index.store import DbWriter, connect_read, path_of

NOW = datetime(2026, 10, 15, 12, 0, 0)


@pytest.fixture
def library(tmp_path):
    """A small folder tree standing in for a library (never a real one)."""
    for relative in ("Music/Artist A/Album 1", "Music/Artist B/Album 2", "Rips/Single", "Other"):
        (tmp_path / "lib" / relative).mkdir(parents=True)
    return tmp_path / "lib"


@pytest.fixture
def writer(tmp_path):
    with DbWriter(tmp_path / "db" / "library.db") as writer:
        yield writer


@pytest.fixture
def read(writer):
    conn = connect_read(writer.path)
    yield conn
    conn.close()


def add_entry(writer, root_id, parent_id, name, is_dir=True):
    return writer.execute("INSERT INTO entries (root_id, parent_id, name, is_dir) VALUES (?, ?, ?, ?)",
                          (root_id, parent_id, name, int(is_dir))).result()


def test_is_inside():
    assert is_inside(r"C:\Music\A", r"C:\Music")
    assert is_inside(r"c:\music\a", r"C:\MUSIC")
    assert is_inside(r"C:\Music", "C:\\")
    assert not is_inside(r"C:\Music", r"C:\Music")
    assert not is_inside(r"C:\Musical", r"C:\Music")


def test_normalize():
    assert normalize("C:/Music/A/../B/") == r"C:\Music\B"
    assert normalize("C:\\") == "C:\\"


def test_add_and_list(writer, read, library):
    result = add_root(writer, library / "Music", now=NOW)
    assert result.status is AddStatus.ADDED and result.path == str(library / "Music")
    [root] = list_roots(read)
    assert (root.id, root.path, root.online, root.added_at, root.last_scan) == (
        result.root_id, str(library / "Music"), True, "2026-10-15T12:00:00", None)
    assert path_of(read, root.top_entry_id) == library / "Music"


def test_roots_are_listed_in_path_order(writer, read, library):
    for name in ("Rips", "Other", "Music"):
        add_root(writer, library / name)
    assert [r.path for r in list_roots(read)] == [str(library / n) for n in ("Music", "Other", "Rips")]


def test_not_a_folder(writer, read, library):
    (library / "file.flac").write_bytes(b"")
    assert add_root(writer, library / "file.flac").status is AddStatus.IS_A_FILE
    assert add_root(writer, library / "missing").status is AddStatus.NOT_A_FOLDER
    assert list_roots(read) == []


def test_already_a_root_ignoring_case(writer, library):
    first = add_root(writer, library / "Music")
    again = add_root(writer, str(library / "MUSIC") + "\\")
    assert again.status is AddStatus.ALREADY_ROOT and again.root_id == first.root_id


def test_folder_inside_a_root_navigates(writer, read, library):
    root = add_root(writer, library / "Music")
    inside = add_root(writer, library / "Music" / "Artist A" / "Album 1")
    assert inside.status is AddStatus.INSIDE_ROOT and inside.root_id == root.root_id
    assert inside.path == str(library / "Music" / "Artist A" / "Album 1")
    assert len(list_roots(read)) == 1


def test_parent_merges_existing_roots_and_keeps_their_entries(writer, read, library):
    album1 = add_root(writer, library / "Music" / "Artist A" / "Album 1")
    album2 = add_root(writer, library / "Music" / "Artist B" / "Album 2")
    rips = add_root(writer, library / "Rips")
    top1 = list_roots(read)[0].top_entry_id
    track = add_entry(writer, album1.root_id, top1, "01 One.flac", is_dir=False)
    writer.execute("INSERT INTO tracks (entry_id, title) VALUES (?, 'One')", (track,)).result()

    result = add_root(writer, library / "Music")
    assert result.status is AddStatus.MERGED
    assert result.merged == (str(library / "Music" / "Artist A" / "Album 1"),
                             str(library / "Music" / "Artist B" / "Album 2"))
    assert [r.path for r in list_roots(read)] == [str(library / "Music"), str(library / "Rips")]
    # the former roots' entries are kept, now under the new root
    assert path_of(read, track) == library / "Music" / "Artist A" / "Album 1" / "01 One.flac"
    assert read.execute("SELECT title FROM tracks WHERE entry_id = ?", (track,)).fetchone()[0] == "One"
    assert read.execute("SELECT count(*) FROM entries WHERE root_id IN (?, ?)",
                        (album1.root_id, album2.root_id)).fetchone()[0] == 0
    assert read.execute("SELECT count(*) FROM entries WHERE root_id = ?", (rips.root_id,)).fetchone()[0] == 1
    names = {row[0] for row in read.execute("SELECT name FROM entries WHERE root_id = ?", (result.root_id,))}
    assert names == {str(library / "Music"), "Artist A", "Album 1", "Artist B", "Album 2", "01 One.flac"}


def test_merging_reuses_shared_intermediate_folders(writer, read, library):
    (library / "Music" / "Artist A" / "Album 3").mkdir()
    add_root(writer, library / "Music" / "Artist A" / "Album 1")
    add_root(writer, library / "Music" / "Artist A" / "Album 3")
    result = add_root(writer, str(library / "music"))  # different case than on disk
    assert result.status is AddStatus.MERGED
    assert read.execute("SELECT count(*) FROM entries WHERE name = 'Artist A'").fetchone()[0] == 1


def test_remove_root_forgets_its_index(writer, read, library):
    result = add_root(writer, library / "Music")
    add_entry(writer, result.root_id, list_roots(read)[0].top_entry_id, "Artist A")
    assert remove_root(writer, result.root_id) is True
    assert remove_root(writer, result.root_id) is False
    assert list_roots(read) == []
    assert read.execute("SELECT count(*) FROM entries").fetchone()[0] == 0
    assert (library / "Music" / "Artist A").is_dir()  # nothing on disk changed


def test_offline_and_back(writer, read, library, tmp_path):
    add_root(writer, library / "Music")
    add_root(writer, library / "Rips")
    assert check_online(writer, list_roots(read)) == []

    shutil.move(library / "Rips", tmp_path / "unplugged")  # the drive goes away
    [changed] = check_online(writer, list_roots(read))
    assert changed.path == str(library / "Rips") and not changed.online
    assert [r.online for r in list_roots(read)] == [True, False]
    assert check_online(writer, list_roots(read)) == []

    shutil.move(tmp_path / "unplugged", library / "Rips")  # and comes back
    [changed] = check_online(writer, list_roots(read))
    assert changed.online and all(r.online for r in list_roots(read))


@pytest.fixture
def junction(library, tmp_path):
    """A junction elsewhere pointing at the library's Music folder: the same folder under another spelling."""
    import _winapi

    link = tmp_path / "elsewhere" / "Music link"
    link.parent.mkdir()
    _winapi.CreateJunction(str(library / "Music"), str(link))
    yield link
    link.rmdir()  # removes the junction only, never its target


def test_same_folder_through_a_junction_is_already_a_root(writer, read, library, junction):
    first = add_root(writer, library / "Music")
    again = add_root(writer, junction)
    assert again.status is AddStatus.ALREADY_ROOT and again.root_id == first.root_id
    assert again.path == str(library / "Music")
    assert len(list_roots(read)) == 1


def test_folder_inside_a_root_through_a_junction_navigates_to_the_library_path(writer, read, library, junction):
    root = add_root(writer, library / "Music")
    inside = add_root(writer, junction / "Artist A" / "Album 1")
    assert inside.status is AddStatus.INSIDE_ROOT and inside.root_id == root.root_id
    assert inside.path == str(library / "Music" / "Artist A" / "Album 1")
    assert len(list_roots(read)) == 1


def test_root_given_through_a_junction_keeps_its_spelling_and_blocks_the_real_path(writer, read, junction, library):
    first = add_root(writer, junction)
    assert first.status is AddStatus.ADDED and first.path == str(junction)
    assert add_root(writer, library / "Music" / "Artist B").status is AddStatus.INSIDE_ROOT
    assert [r.path for r in list_roots(read)] == [str(junction)]


def test_parent_through_a_junction_merges_roots_inside_it(writer, read, library, junction):
    album1 = add_root(writer, library / "Music" / "Artist A" / "Album 1")
    top1 = list_roots(read)[0].top_entry_id
    track = add_entry(writer, album1.root_id, top1, "01 One.flac", is_dir=False)
    result = add_root(writer, junction)
    assert result.status is AddStatus.MERGED
    assert result.merged == (str(library / "Music" / "Artist A" / "Album 1"),)
    assert path_of(read, track) == junction / "Artist A" / "Album 1" / "01 One.flac"


def test_offline_root_is_compared_as_written(writer, read, library):
    first = add_root(writer, library / "Music")
    writer.execute("UPDATE roots SET online = 0 WHERE id = ?", (first.root_id,)).result()
    assert add_root(writer, library / "Music").status is AddStatus.ALREADY_ROOT
