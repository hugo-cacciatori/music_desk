"""core/index/search.py: free text as seek_lyrics' search, File and Lyrics search, names before tags, filters,
order and rows (SPEC §6)."""

import shutil
import sqlite3
import threading

import pytest

from refrain.core.index.lyrics_index import record_lyrics
from refrain.core.index.queries import playlist_for_ids
from refrain.core.index.roots import add_root
from refrain.core.index.scanner import Scanner
from refrain.core.index.schema import configure, migrate
from refrain.core.index.search import (
    LYRICS,
    extract,
    fold,
    fold_text,
    parse_query,
    result_rows,
    snippet,
    spans,
    text_search,
)
from refrain.core.index.store import DbWriter, connect_read, path_of
from refrain.core.lyrics import FOUND, INSTRUMENTAL, NOT_FOUND


class Library:
    """A hand-built index: one root, folders, and tracks with their search rows."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.root = conn.execute("INSERT INTO roots (path, added_at) VALUES ('C:\\Music', 'x')").lastrowid
        self.top = self.folder(None, "C:\\Music")

    def folder(self, parent, name) -> int:
        entry = self.conn.execute("INSERT INTO entries (root_id, parent_id, name, is_dir) VALUES (?, ?, ?, 1)",
                                  (self.root, parent, name)).lastrowid
        self.conn.execute("INSERT INTO search (rowid, name) VALUES (?, ?)", (entry, name))  # as the walk does
        return entry

    def track(self, parent, name, *, title=None, artist=None, album=None, albumartist=None, genre=None,
              lyrics=None, key=None, date=None, rg=0, has_lyrics_tag=0, has_lrc=0, read_error=None) -> int:
        ext = name[name.rfind("."):].lower()
        entry = self.conn.execute("INSERT INTO entries (root_id, parent_id, name, is_dir, ext) VALUES (?, ?, ?, 0, ?)",
                                  (self.root, parent, name, ext)).lastrowid
        gains = ["-1.0 dB"] * rg + [None] * (4 - rg)
        self.conn.execute(
            "INSERT INTO tracks (entry_id, title, artist, album, albumartist, genre, date, lyrics_key, "
            "has_lyrics_tag, has_lrc_sidecar, read_error, rg_track_gain, rg_track_peak, rg_album_gain, rg_album_peak) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entry, title, artist, album, albumartist, genre, date, key, has_lyrics_tag, has_lrc, read_error, *gains))
        self.conn.execute("INSERT INTO search (rowid, name, title, artist, albumartist, album, genre, lyrics) "
                          "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (entry, name, title, artist, albumartist, album, genre,
                                                              lyrics))
        return entry

    def unread(self, parent, name) -> int:
        """A file the folder walk listed and whose tags aren't read yet: an entry and its name only."""
        ext = name[name.rfind("."):].lower() if "." in name else None
        entry = self.conn.execute("INSERT INTO entries (root_id, parent_id, name, is_dir, ext) VALUES (?, ?, ?, 0, ?)",
                                  (self.root, parent, name, ext)).lastrowid
        self.conn.execute("INSERT INTO search (rowid, name) VALUES (?, ?)", (entry, name))
        return entry

    def lyrics(self, key, status, plain=None, synced=None):
        record_lyrics(self.conn, key, {"status": status, "plain": plain, "synced": synced, "checked": "2026-10-15"})


@pytest.fixture
def lib(tmp_path):
    conn = sqlite3.connect(tmp_path / "library.db", autocommit=True)
    configure(conn, write=True)
    migrate(conn)
    yield Library(conn)
    conn.close()


def find(lib, text, **kwargs) -> set[str]:
    result = text_search(lib.conn, parse_query(text), **kwargs)
    names = dict(lib.conn.execute("SELECT id, name FROM entries"))
    return {names[i] for i in result.ids}


@pytest.fixture
def songs(lib):
    album = lib.folder(lib.top, "Björk - Homogenic")
    lib.track(album, "01 Hunter.flac", title="Hunter", artist="Björk", album="Homogenic", key="k1", rg=4,
              date="1997-09-22", genre="Electronic")
    lib.track(album, "02 Jóga.flac", title="Jóga", artist="Björk", album="Homogenic", key="k2", rg=2,
              has_lyrics_tag=1, lyrics="All these accidents\nthat happen")
    other = lib.folder(lib.top, "Radiohead")
    lib.track(other, "Glove.mp3", title="Glove", artist="Radiohead", albumartist="Various", key="k3",
              date="2001")
    lib.track(other, "lovely.opus", title="Lovely", artist="The Cure", key="k4", has_lrc=1)
    lib.track(other, "untitled.flac", key=None)
    lib.lyrics("k1", FOUND, plain="If travel is searching\nAnd home what's been found", synced="[00:01.00]If")
    lib.lyrics("k3", FOUND, plain="Fits like a glove")
    lib.lyrics("k4", NOT_FOUND)
    lib.lyrics("k2", INSTRUMENTAL)
    return album, other


# -- free text --


def test_substring_mid_word(lib, songs):
    assert find(lib, "ove") == {"Glove.mp3", "lovely.opus"}
    assert find(lib, "unte") == {"01 Hunter.flac"}


def test_case_and_accents_are_ignored_both_ways(lib, songs):
    bjork = {"Björk - Homogenic", "01 Hunter.flac", "02 Jóga.flac"}  # the folder by its name
    assert find(lib, "BJORK") == bjork
    assert find(lib, "jóga") == find(lib, "joga") == {"02 Jóga.flac"}
    assert find(lib, "Bjôrk") == bjork


def test_lyrics_search_matches_lrclib_text_only(lib, songs):
    assert find(lib, "travel is", mode=LYRICS) == {"01 Hunter.flac"}  # LRCLIB plain
    assert find(lib, "accidents", mode=LYRICS) == set()  # only in the file's lyrics tag: not searched (M12)
    assert find(lib, "Hunter", mode=LYRICS) == set()  # names and tags are File search's
    assert find(lib, "glove", mode=LYRICS) == {"Glove.mp3"}  # in its lyrics too
    assert find(lib, "ve", mode=LYRICS) == {"01 Hunter.flac", "Glove.mp3"}  # short text: LIKE, lyrics only
    assert find(lib, "artist:cure ove", mode=LYRICS) == set()  # filters still narrow it
    assert find(lib, "artist:radiohead ove", mode=LYRICS) == {"Glove.mp3"}


def test_file_search_matches_names_tags_and_the_date_not_lyrics(lib, songs):
    assert find(lib, "travel is") == set()  # lyrics are Lyrics search's
    assert find(lib, "Various") == {"Glove.mp3"}  # the album artist
    assert find(lib, "Electronic") == set()  # not the genre as free text (Hugo, 2026-10-24)...
    assert find(lib, "genre:tron") == {"01 Hunter.flac"}  # ...which stays a filter
    assert find(lib, "1997") == {"01 Hunter.flac"}  # the date, any part of it
    assert find(lib, "09-2") == {"01 Hunter.flac"}
    assert find(lib, "01") == {"01 Hunter.flac", "Glove.mp3"}  # a name, and a date: "2001"
    assert find(lib, "Homogenic 1997") == set()  # still one substring
    assert find(lib, "Radiohead 2") == set()


def test_the_whole_remainder_is_one_substring(lib, songs):
    assert find(lib, "  like a glove ", mode=LYRICS) == {"Glove.mp3"}
    assert find(lib, "glove like", mode=LYRICS) == set()  # not a word search: the script took the box as one


def test_one_and_two_characters(lib, songs):
    assert find(lib, "Lo") == {"Glove.mp3", "lovely.opus"}
    assert find(lib, "un") == {"01 Hunter.flac", "untitled.flac"}  # "Hunter", "found", "untitled"
    # Short non-ASCII text is folded on both sides: "ö" finds Björk, Jóga and every other "o".
    with_o = {"01 Hunter.flac", "02 Jóga.flac", "Glove.mp3", "lovely.opus", "Björk - Homogenic", "Radiohead"}
    assert find(lib, "ö") == find(lib, "ó") == with_o
    assert find(lib, "jo") == set()  # short ASCII text uses LIKE, which keeps accents (a known limit)
    assert find(lib, "%") == set() and find(lib, "_") == set()  # LIKE wildcards are literal


@pytest.mark.parametrize("text", ['AND', 'glove AND x', 'glo*', 'a"b', 'artist : x', '(glove', 'NEAR(a b)',
                                  'x OR y', '-glove', '^glove'])
def test_fts_syntax_is_literal(lib, songs, text):
    lib.track(songs[1], "odd.flac", title=f"weird {text} title", key="k9")
    assert "odd.flac" in find(lib, text)


def test_quoted_phrases_and_with_the_rest(lib, songs):
    assert find(lib, '"the cure"') == {"lovely.opus"}
    assert find(lib, 'lovely "cure"') == {"lovely.opus"}
    assert find(lib, '"glove" "cure"') == set()
    assert parse_query('"  a  b "').text == ("  a  b ",)  # spaces kept as typed


def test_a_key_losing_found_drops_its_text(lib, songs):
    assert find(lib, "searching", mode=LYRICS) == {"01 Hunter.flac"}
    lib.lyrics("k1", NOT_FOUND)
    assert find(lib, "searching", mode=LYRICS) == set()
    lib.lyrics("k1", FOUND, plain="searching again")
    assert find(lib, "searching", mode=LYRICS) == {"01 Hunter.flac"}


def test_folders_and_audio_files_only(lib, songs):
    lib.unread(songs[1], "glove.txt")
    lib.unread(songs[1], "glove.cue")
    lib.unread(songs[1], "glove.jpg")
    lib.folder(songs[1], "Gloves")
    assert find(lib, "glove") == {"Glove.mp3", "Gloves"}
    assert find(lib, "Music") == set()  # not the root's top entry, whose name is the root path


def test_names_are_found_before_the_tags_are_read(lib, songs):
    """The walk lists a folder and its files at once; the tag read comes later (SPEC §6 "What is searched")."""
    new = lib.folder(lib.top, "Sigur Rós - Takk")
    lib.unread(new, "01 Takk.flac")
    lib.unread(new, "02 Glósóli.FLAC")
    assert find(lib, "takk") == {"Sigur Rós - Takk", "01 Takk.flac"}
    assert find(lib, "glosoli") == {"02 Glósóli.FLAC"}
    assert find(lib, "ós") == {"Sigur Rós - Takk", "02 Glósóli.FLAC"}  # short and folded
    assert find(lib, "ext:flac takk") == {"01 Takk.flac"}  # ext: limits to files, read or not
    # A filter on tags limits the results to read files: never a folder, nor a file not read yet.
    for text in ("rg:none", "rg:partial", "lrclib:notags", "lrclib:pending", "year:2", "artist:s", "genre:e"):
        assert not find(lib, text) & {"Sigur Rós - Takk", "01 Takk.flac", "02 Glósóli.FLAC"}, text
    assert find(lib, "rg:none") == {"Glove.mp3", "lovely.opus", "untitled.flac"}


def test_a_folder_row(lib, songs):
    album, _other = songs
    query = parse_query("homogenic")
    rows = result_rows(lib.conn, text_search(lib.conn, query).ids, query)
    assert [r.name for r in rows] == ["Björk - Homogenic", "01 Hunter.flac", "02 Jóga.flac"]
    folder = rows[0]
    assert folder.id == album and folder.is_dir and folder.folder == "C:\\Music" and folder.folder_id == lib.top
    assert (folder.title, folder.artist, folder.n_tracks, folder.mtime, folder.snippet) == (None, None, None, None, "")


def test_an_unread_file_row(lib):
    folder = lib.folder(lib.top, "New")
    entry = lib.unread(folder, "Song.opus")
    query = parse_query("song")
    [row] = result_rows(lib.conn, text_search(lib.conn, query).ids, query)
    assert (row.id, row.name, row.folder, row.title, row.lrclib) == (entry, "Song.opus", "C:\\Music\\New", None, None)
    # Play results plays it already, and skips the folder and a file that isn't audio.
    cover = lib.unread(folder, "cover.jpg")
    played = playlist_for_ids(lib.conn, [folder, entry, cover])
    assert [(str(p.path), p.duration, p.title) for p in played] == [("C:\\Music\\New\\Song.opus", None, None)]


def test_columns_narrow_the_free_text(lib, songs):
    assert find(lib, "Various") == {"Glove.mp3"}
    assert find(lib, "Various", columns=("title", "artist", "album", "lrclib")) == set()
    assert find(lib, "Va", columns=("title", "artist", "album", "lrclib")) == set()


# -- filters --


def test_parse_query():
    query = parse_query('artist:"Sigur Rós" album:Takk year:2005 EXT:Opus lrclib:Synced rg:none Re: stuff "x y"')
    assert query.text == ("Re: stuff", "x y")
    assert query.filters == {"artist": ("Sigur Rós",), "album": ("Takk",), "year": ("2005",), "ext": (".opus",),
                             "lrclib": ("synced",), "rg": ("none",)}
    assert not query.errors
    bad = parse_query("lrclib:maybe artist: hello")
    assert bad.errors and "lrclib:maybe" in bad.errors[0]
    assert bad.text == ("hello",) and not bad.filters
    assert parse_query("   ").empty


def test_text_fields(lib, songs):
    assert find(lib, "artist:bjork") == {"01 Hunter.flac", "02 Jóga.flac"}
    assert find(lib, "artist:various") == {"Glove.mp3"}  # album artist too
    assert find(lib, "artist:bjork artist:cure") == {"01 Hunter.flac", "02 Jóga.flac", "lovely.opus"}  # ORed
    assert find(lib, "artist:bjork hunt") == {"01 Hunter.flac"}  # ANDed with the free text
    assert find(lib, "album:homo") == {"01 Hunter.flac", "02 Jóga.flac"}
    assert find(lib, "genre:tron") == {"01 Hunter.flac"}
    assert find(lib, "artist:ra") == {"Glove.mp3"}  # short values use LIKE


def test_value_fields(lib, songs):
    assert find(lib, "year:1997") == {"01 Hunter.flac"}
    assert find(lib, "year:200") == {"Glove.mp3"}
    assert find(lib, "ext:opus") == {"lovely.opus"}
    assert find(lib, "ext:.MP3") == {"Glove.mp3"}
    assert find(lib, "rg:full") == {"01 Hunter.flac"}
    assert find(lib, "rg:partial") == {"02 Jóga.flac"}
    assert find(lib, "rg:none") == {"Glove.mp3", "lovely.opus", "untitled.flac"}


def test_lrclib_states(lib, songs):
    album = songs[0]
    lib.track(album, "03 Pending.flac", title="Pending", artist="Björk", key="k5")
    lib.track(album, "04 Failed.flac", title="Failed", artist="Björk", key="k6")
    lib.track(album, "05 Broken.flac", key="k7", read_error="TagError: no")
    assert find(lib, "lrclib:synced") == {"01 Hunter.flac"}
    assert find(lib, "lrclib:plain") == {"Glove.mp3"}
    assert find(lib, "lrclib:instrumental") == {"02 Jóga.flac"}
    assert find(lib, "lrclib:notfound") == {"lovely.opus"}
    assert find(lib, "lrclib:pending") == {"03 Pending.flac", "04 Failed.flac"}
    assert find(lib, "lrclib:pending", error_keys={"k6"}) == {"03 Pending.flac"}
    assert find(lib, "lrclib:error", error_keys={"k6"}) == {"04 Failed.flac"}
    assert find(lib, "lrclib:synced lrclib:plain") == {"01 Hunter.flac", "Glove.mp3"}
    assert find(lib, "lrclib:notags") == {"untitled.flac"}  # not the unreadable file


def test_natural_order_cap_and_total(lib):
    folders = [lib.folder(lib.top, name) for name in ("B10", "B2", "a")]
    for folder in folders:
        for name in ("t10.flac", "t9.flac", "T1.flac"):
            lib.track(folder, name, title="song")
    sub = lib.folder(folders[1], "inner")
    lib.track(sub, "t1.flac", title="song")
    result = text_search(lib.conn, parse_query("song"))
    assert result.total == 10
    paths = [str(path_of(lib.conn, i)).removeprefix("C:\\Music\\") for i in result.ids]
    assert paths == ["a\\T1.flac", "a\\t9.flac", "a\\t10.flac", "B2\\T1.flac", "B2\\t9.flac", "B2\\t10.flac",
                     "B2\\inner\\t1.flac", "B10\\T1.flac", "B10\\t9.flac", "B10\\t10.flac"]
    capped = text_search(lib.conn, parse_query("song"), limit=4)
    assert capped.ids == result.ids[:4] and capped.total == 10


# -- rows, highlights and snippets --


def test_fold_offsets():
    assert fold("Jóga") == ("joga", [0, 1, 2, 3])
    assert fold("Straße") == ("strasse", [0, 1, 2, 3, 4, 4, 5])
    decomposed = "Jo\u0301ga"
    assert fold(decomposed) == ("joga", [0, 1, 3, 4])
    assert spans("Straße Jóga", ["sse", "jog"]) == ((4, 6), (7, 10))
    assert spans("abab", ["ab"]) == ((0, 4),)  # adjacent matches paint as one
    assert spans("aaa", ["aa", "a"]) == ((0, 3),)
    for text in ("Jóga", "Straße", decomposed, "Sigur Rós\nÇa ira", "İstanbul", "ǰ", "ﬁne", "Ångström"):
        assert fold_text(text) == fold(text)[0]


def test_result_rows(lib, songs):
    album, _other = songs
    query = parse_query("is search")
    ids = text_search(lib.conn, query, mode=LYRICS).ids
    [row] = result_rows(lib.conn, ids, query, mode=LYRICS)
    assert (row.name, row.folder, row.folder_id) == ("01 Hunter.flac", "C:\\Music\\Björk - Homogenic", album)
    assert row.snippet == "If travel is searching"
    assert row.snippet_spans == ((10, 19),)
    assert row.extract == "If travel is searching\nAnd home what's been found"  # the card's: the line, the next
    assert row.extract_spans == tuple((a + row.extract.index(row.snippet), b + row.extract.index(row.snippet))
                                      for a, b in row.snippet_spans)
    assert row.lrclib == "synced" and row.rg == "full"

    query = parse_query("BJÖ")
    rows = result_rows(lib.conn, text_search(lib.conn, query).ids, query)
    assert [r.artist_spans for r in rows] == [(), ((0, 3),), ((0, 3),)]  # the folder, then its tracks
    assert all(r.snippet == "" for r in rows)  # the artist matches: no snippet


def test_snippets_in_lyrics_search_only(lib, songs):
    query = parse_query('glove "fits"')
    [row] = result_rows(lib.conn, text_search(lib.conn, query, mode=LYRICS).ids, query, mode=LYRICS)
    assert row.title_spans == ()  # the match is in the lyrics: only the snippet is highlighted
    assert row.snippet == "Fits like a glove" and row.snippet_spans == ((0, 4), (12, 17))
    query = parse_query("glove")
    [row] = result_rows(lib.conn, text_search(lib.conn, query).ids, query)
    assert row.title_spans == ((0, 5),) and row.snippet == ""  # File search: no snippet, though the lyrics match
    assert row.extract == "" and row.extract_spans == ()


def test_artist_filter_values_are_highlighted(lib, songs):
    query = parse_query("artist:cure")
    [row] = result_rows(lib.conn, text_search(lib.conn, query).ids, query)
    assert row.artist_spans == ((4, 8),) and row.title_spans == ()


def test_rows_gone_meanwhile_are_dropped(lib, songs):
    query = parse_query("artist:bjork")  # tracks only: deleting the folder would take them all
    ids = text_search(lib.conn, query).ids
    lib.conn.execute("DELETE FROM entries WHERE id = ?", (ids[0],))
    assert [r.id for r in result_rows(lib.conn, ids, query)] == ids[1:]


def test_snippet_strips_timestamps():
    assert snippet([None, "[00:01.00] La la\n[00:02.00] Glove"], ["glove"]) == "Glove"


def test_extract_takes_a_line_on_each_side():
    """A Lyrics search card (SPEC §6 "Direction task 6"): the first matching line, between its neighbours; blank
    lines and timestamps don't count."""
    lyrics = "[00:01.00] One\n\nTwo glove\nThree\nFour glove"
    assert extract([lyrics], ["glove"]) == "One\nTwo glove\nThree"
    assert extract(["Glove first\nthen"], ["glove"]) == "Glove first\nthen"
    assert extract(["only the end\nglove"], ["glove"]) == "only the end\nglove"
    assert extract(["nothing"], ["glove"]) == "" and extract([None], ["glove"]) == ""


# -- through the scanner --


ALBUM = "Fixture Artist - Fixture Album"


def test_a_new_copy_of_a_found_song_is_searchable_at_once(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "album" / ALBUM, lib / ALBUM)
    with DbWriter(tmp_path / "db" / "library.db") as writer:
        root = add_root(writer, lib).root_id
        Scanner(writer).scan_root(root)
        read = connect_read(writer.path)
        try:
            key = read.execute("SELECT lyrics_key FROM tracks t JOIN entries e ON e.id = t.entry_id "
                               "WHERE e.name = '01 First.flac'").fetchone()[0]
            writer.submit(lambda conn: record_lyrics(conn, key, {"status": FOUND, "plain": "zebra crossing",
                                                                 "checked": "2026-10-15"})).result()
            before = text_search(read, parse_query("zebra"), mode=LYRICS).total
            assert before >= 1
            (lib / "Copy").mkdir()
            shutil.copy2(lib / ALBUM / "01 First.flac", lib / "Copy" / "01 First.flac")
            Scanner(writer).scan_root(root)
            ids = text_search(read, parse_query("zebra"), mode=LYRICS).ids
            assert len(ids) == before + 1
            assert any(path_of(read, i) == lib / "Copy" / "01 First.flac" for i in ids)
        finally:
            read.close()


def test_names_are_searchable_while_the_tag_read_is_held_back(tmp_path, fixtures_dir, monkeypatch):
    """SPEC §6: the walk's names are found while no tag has been read (a stub reader holds the tag read)."""
    from refrain.core.index import scanner as scanner_module

    lib = tmp_path / "lib"
    shutil.copytree(fixtures_dir / "album" / ALBUM, lib / ALBUM)
    held, release = threading.Event(), threading.Event()
    real = scanner_module.read_file

    def stub(path, size, mtime):
        held.set()
        release.wait(30)
        return real(path, size, mtime)

    monkeypatch.setattr(scanner_module, "read_file", stub)
    with DbWriter(tmp_path / "db" / "library.db") as writer:
        root = add_root(writer, lib).root_id
        scan = threading.Thread(target=Scanner(writer).scan_root, args=(root,))
        scan.start()
        read = connect_read(writer.path)
        try:
            assert held.wait(30)
            assert read.execute("SELECT count(*) FROM tracks").fetchone()[0] == 0  # no tag read yet
            assert find_names(read, "fixture album") == {ALBUM}
            assert find_names(read, "first") == {"01 First.flac"}
            assert find_names(read, "lowercase_rg") == {"lowercase_rg.mp3"}
        finally:
            release.set()
            scan.join(60)
            read.close()


def find_names(conn, text) -> set[str]:
    ids = text_search(conn, parse_query(text)).ids
    return {conn.execute("SELECT name FROM entries WHERE id = ?", (i,)).fetchone()[0] for i in ids}
