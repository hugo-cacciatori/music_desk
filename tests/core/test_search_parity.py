"""Library search against seek_lyrics' songs.html search box (SPEC §6 "Preserved from seek_lyrics", M6 Done when).

The page script's rule, re-implemented: `[title, artist, album, plain].join('\\n').toLowerCase().includes(q)`
with `q = box.value.trim().toLowerCase()`. The songs are the frozen script's own scan of the fixture library, and
their lyrics follow its tags-first rule; LRCLIB texts are scripted. The app's free text, narrowed to the same
fields, must select the same tracks, apart from the accent difference (SPEC §17).
"""

import importlib.util
import shutil
from pathlib import Path

import pytest

from music_desk.core.index.lyrics_index import record_lyrics
from music_desk.core.index.roots import add_root
from music_desk.core.index.scanner import Scanner
from music_desk.core.index.search import fold, parse_query, text_search
from music_desk.core.index.store import DbWriter, connect_read, path_of

LRCLIB_TEXTS = [
    "Love is a glove\nThat fits the hand",
    "Second line of the night\n(oh, oh) it's all right!",
    "Words without end\nlovely day, lovely day",
    "Fixture lyrics: one, two\nthree",
]
QUERIES = ["ove", "lovely day", "fir", "e", "xt", "Fi", "fixture", "Fixture Artist", "ist", "  first  ", "line",
           "o w", "!", "(", "'", ",", "tone", "night\n(", "oh, oh", "lyrics:", "TRACK", "a", "z",
           "the hand", "d,", "s w"]
TESTS = Path(__file__).parents[1]
FIXTURES, REFERENCE = TESTS / "fixtures" / "generated", TESTS / "reference"
FIELDS = ("title", "artist", "album", "lyrics", "lrclib")  # the tags' lyrics stand for the script's tags-first plain


def page_matches(song: dict, query: str) -> bool:
    haystack = "\n".join(s or "" for s in (song["title"], song["artist"], song["album"], song["plain"]))
    return query.strip().lower() in haystack.lower()


@pytest.fixture(scope="module")
def script(request):
    """The frozen script, for a module-scoped corpus (conftest's fixture is function-scoped)."""
    spec = importlib.util.spec_from_file_location("reference_seek_lyrics_search", REFERENCE / "seek_lyrics.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def corpus(tmp_path_factory, script):
    tmp = tmp_path_factory.mktemp("parity")
    lib = tmp / "lib"
    shutil.copytree(FIXTURES, lib)
    songs, _errors = script.scan(lib)
    with DbWriter(tmp / "db" / "library.db") as writer:
        Scanner(writer).scan_root(add_root(writer, lib).root_id)
        read = connect_read(writer.path)
        indexed = {}
        for entry_id, title, artist, album, key in read.execute(
                "SELECT entry_id, title, artist, album, lyrics_key FROM tracks"):
            indexed[path_of(read, entry_id).relative_to(lib).as_posix()] = (entry_id, (title, artist, album), key)
        # The app looks every track up, the script only those without a lyrics tag (a deliberate difference), so a
        # key gets LRCLIB text only when none of its songs has a lyrics tag. One text per key, as the cache gives.
        tagged = {script.cache_key(song) for song in songs if song["embedded"]}
        texts: dict[str, str | None] = {}
        compared = []
        for song in songs:
            found = indexed.get(song["path"])
            # Only songs whose fields are the same on both sides: the page shows file-name fallbacks and stripped
            # text, which the app keeps in other columns.
            if found is None or found[1] != (song["title"], song["artist"], song["album"]):
                continue
            entry_id, _fields, key = found
            if song["embedded"]:
                song["plain"] = script.from_tags(song["embedded"])["plain"]  # the script's tags-first rule
            else:
                if key not in texts:
                    n = len(texts)
                    texts[key] = LRCLIB_TEXTS[n % len(LRCLIB_TEXTS)] if n % 3 and key not in tagged else None
                    if texts[key]:
                        assert key == script.cache_key(song)
                        entry = {"status": "found", "source": "lrclib", "plain": texts[key], "checked": "2026-10-15"}
                        writer.submit(lambda conn, k=key, e=entry: record_lyrics(conn, k, e)).result()
                song["plain"] = texts[key]
            compared.append((entry_id, song))
        yield read, compared
        read.close()


def test_enough_songs_are_compared(corpus):
    _read, compared = corpus
    assert len(compared) >= 20
    assert any(song["embedded"] for _id, song in compared)
    assert sum(bool(song["plain"]) and not song["embedded"] for _id, song in compared) >= 5


@pytest.mark.parametrize("query", QUERIES)
def test_free_text_selects_what_the_page_selected(corpus, query):
    assert query.isascii()
    read, compared = corpus
    ids = {entry_id for entry_id, _song in compared}
    app = set(text_search(read, parse_query(query), columns=FIELDS, limit=100_000).ids) & ids
    page = {entry_id for entry_id, song in compared if page_matches(song, query)}
    assert page <= app, f"the page found more for {query!r}"
    for entry_id, song in compared:
        if entry_id in app - page:  # only an accent the app ignores may explain an extra match
            haystack = "\n".join(s or "" for s in (song["title"], song["artist"], song["album"], song["plain"]))
            assert fold(query.strip())[0] in fold(haystack)[0], f"{song['path']} matched {query!r}"
