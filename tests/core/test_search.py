"""core/index/search.py: free text as seek_lyrics' search, filters, chips, scope, order and rows (SPEC §6)."""

import shutil
import sqlite3

import pytest

from music_desk.core.index.lyrics_index import record_lyrics
from music_desk.core.index.roots import add_root
from music_desk.core.index.scanner import Scanner
from music_desk.core.index.schema import configure, migrate
from music_desk.core.index.search import (
    ALL,
    MISSING,
    WITH_LYRICS,
    fold,
    fold_text,
    parse_query,
    result_rows,
    snippet,
    spans,
    text_search,
)
from music_desk.core.index.store import DbWriter, connect_read, path_of
from music_desk.core.lyrics import FOUND, INSTRUMENTAL, NOT_FOUND


class Library:
    """A hand-built index: one root, folders, and tracks with their search rows."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.root = conn.execute("INSERT INTO roots (path, added_at) VALUES ('C:\\Music', 'x')").lastrowid
        self.top = self.folder(None, "C:\\Music")

    def folder(self, parent, name) -> int:
        return self.conn.execute("INSERT INTO entries (root_id, parent_id, name, is_dir) VALUES (?, ?, ?, 1)",
                                 (self.root, parent, name)).lastrowid

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
    assert find(lib, "BJORK") == {"01 Hunter.flac", "02 Jóga.flac"}
    assert find(lib, "jóga") == find(lib, "joga") == {"02 Jóga.flac"}
    assert find(lib, "Bjôrk") == {"01 Hunter.flac", "02 Jóga.flac"}


def test_lrclib_text_and_tag_lyrics_both_match(lib, songs):
    assert find(lib, "travel is") == {"01 Hunter.flac"}  # LRCLIB plain
    assert find(lib, "accidents") == {"02 Jóga.flac"}  # the file's lyrics tag


def test_the_whole_remainder_is_one_substring(lib, songs):
    assert find(lib, "  like a glove ") == {"Glove.mp3"}
    assert find(lib, "glove like") == set()  # not a word search: the script took the box as one substring


def test_one_and_two_characters(lib, songs):
    assert find(lib, "Lo") == {"Glove.mp3", "lovely.opus"}
    assert find(lib, "un") == {"01 Hunter.flac", "untitled.flac"}  # "Hunter", "found", "untitled"
    # Short non-ASCII text is folded on both sides: "ö" finds Björk, Jóga and every other "o".
    with_o = {"01 Hunter.flac", "02 Jóga.flac", "Glove.mp3", "lovely.opus"}
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
    assert find(lib, "searching") == {"01 Hunter.flac"}
    lib.lyrics("k1", NOT_FOUND)
    assert find(lib, "searching") == set()
    lib.lyrics("k1", FOUND, plain="searching again")
    assert find(lib, "searching") == {"01 Hunter.flac"}


def test_tracks_only(lib, songs):
    lib.conn.execute("INSERT INTO entries (root_id, parent_id, name, is_dir, ext) VALUES (?, ?, 'glove.txt', 0, "
                     "'.txt')", (lib.root, songs[1]))
    lib.conn.execute("INSERT INTO search (rowid, name) VALUES (last_insert_rowid(), 'glove.txt')")
    assert find(lib, "glove") == {"Glove.mp3"}


def test_columns_narrow_the_free_text(lib, songs):
    assert find(lib, "Various") == {"Glove.mp3"}
    assert find(lib, "Various", columns=("title", "artist", "album", "lrclib")) == set()
    assert find(lib, "Va", columns=("title", "artist", "album", "lrclib")) == set()


# -- filters, chips, scope --


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
    assert find(lib, "lyricstag:yes") == {"02 Jóga.flac"}
    assert find(lib, "lyricstag:lrc") == {"lovely.opus"}
    assert find(lib, "lyricstag:no") == {"01 Hunter.flac", "Glove.mp3", "untitled.flac"}
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


def test_chips(lib, songs):
    assert len(find(lib, "", chip=ALL)) == 5  # no text and no filter: every track (the window shows a prompt)
    assert find(lib, "o", chip=WITH_LYRICS) == {"01 Hunter.flac", "02 Jóga.flac", "Glove.mp3"}
    assert find(lib, "o", chip=MISSING) == {"lovely.opus"}
    assert find(lib, "", chip=MISSING) == {"lovely.opus", "untitled.flac"}
    lib.track(songs[0], "03 Pending.flac", title="Pending", key="k5")  # no lyrics row yet
    assert find(lib, "pending", chip=MISSING) == {"03 Pending.flac"}
    assert find(lib, "pending", chip=WITH_LYRICS) == set()


def test_scope(lib, songs):
    album, other = songs
    sub = lib.folder(album, "Bonus")
    lib.track(sub, "Hunter (live).flac", title="Hunter live", key="k8")
    assert find(lib, "hunter", scope=album) == {"01 Hunter.flac", "Hunter (live).flac"}
    assert find(lib, "hunter", scope=sub) == {"Hunter (live).flac"}
    assert find(lib, "hunter", scope=other) == set()
    assert find(lib, "hunter", scope=lib.top) == {"01 Hunter.flac", "Hunter (live).flac"}


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
    ids = text_search(lib.conn, query).ids
    [row] = result_rows(lib.conn, ids, query)
    assert (row.name, row.folder, row.folder_id) == ("01 Hunter.flac", "C:\\Music\\Björk - Homogenic", album)
    assert row.snippet == "If travel is searching"
    assert row.snippet_spans == ((10, 19),)
    assert row.lrclib == "synced" and row.rg == "full"

    query = parse_query("BJÖ")
    rows = result_rows(lib.conn, text_search(lib.conn, query).ids, query)
    assert [r.artist_spans for r in rows] == [((0, 3),), ((0, 3),)]
    assert all(r.snippet == "" for r in rows)  # the artist matches: no snippet


def test_snippet_only_for_lyric_only_parts(lib, songs):
    query = parse_query('glove "fits"')
    [row] = result_rows(lib.conn, text_search(lib.conn, query).ids, query)
    assert row.title_spans == ((0, 5),)
    assert row.snippet == "Fits like a glove" and row.snippet_spans == ((0, 4),)


def test_artist_filter_values_are_highlighted(lib, songs):
    query = parse_query("artist:cure")
    [row] = result_rows(lib.conn, text_search(lib.conn, query).ids, query)
    assert row.artist_spans == ((4, 8),) and row.title_spans == ()


def test_rows_gone_meanwhile_are_dropped(lib, songs):
    query = parse_query("bjork")
    ids = text_search(lib.conn, query).ids
    lib.conn.execute("DELETE FROM entries WHERE id = ?", (ids[0],))
    assert [r.id for r in result_rows(lib.conn, ids, query)] == ids[1:]


def test_snippet_strips_timestamps():
    assert snippet([None, "[00:01.00] La la\n[00:02.00] Glove"], ["glove"]) == "Glove"


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
            before = text_search(read, parse_query("zebra")).total
            assert before >= 1
            (lib / "Copy").mkdir()
            shutil.copy2(lib / ALBUM / "01 First.flac", lib / "Copy" / "01 First.flac")
            Scanner(writer).scan_root(root)
            ids = text_search(read, parse_query("zebra")).ids
            assert len(ids) == before + 1
            assert any(path_of(read, i) == lib / "Copy" / "01 First.flac" for i in ids)
        finally:
            read.close()
