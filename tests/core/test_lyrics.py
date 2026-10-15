"""core/lyrics.py on mocked HTTP (SPEC §8, §9, §18). No test reaches the real LRCLIB."""

import urllib.error
import urllib.request
from datetime import date

import pytest
from lrclib_fake import FakeUrlopen, SleepRecorder, http_error, no_jitter
from mutagen.id3 import ID3

from music_desk.core.lyrics import (
    ERROR,
    FOUND,
    INSTRUMENTAL,
    MISSING_TAGS,
    NOT_FOUND,
    NOT_LOOKED_UP,
    CircuitBreaker,
    DictCache,
    EmbedResult,
    LrclibClient,
    LyricsOptions,
    Track,
    cache_key,
    embed_lyrics,
    fetch_lyrics,
    is_fresh,
    lookup,
    read_track,
    search_urls,
    strip_timestamps,
)
from music_desk.core.tags import TaggedFile

TODAY = date(2026, 10, 15)
TRACK = Track(artist="Fixture Artist", title="First", album="Fixture Album", duration=200)
SYNCED = "[00:01.00]Line one\n[00:02.50] Line two"
RECORD = {"id": 7, "artistName": "Fixture Artist", "trackName": "First", "duration": 200,
          "instrumental": False, "plainLyrics": "Line one\nLine two", "syncedLyrics": SYNCED}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """The default opener must never be used by a test."""

    def refuse(*_args, **_kwargs):
        raise AssertionError("a test tried to reach the network")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)


def client(fake: FakeUrlopen, sleep: SleepRecorder | None = None) -> LrclibClient:
    return LrclibClient(urlopen=fake, sleep=sleep or SleepRecorder(), jitter=no_jitter)


def test_default_client_never_reaches_the_network():
    with pytest.raises(AssertionError, match="network"):
        LrclibClient(urlopen=urllib.request.urlopen).get("get", track_name="x")


# -- requests and retries --

def test_request_shape():
    fake = FakeUrlopen({"id": 1})
    assert client(fake).get("get", artist_name="A B", track_name="T&U", duration=3) == {"id": 1}
    assert fake.urls == ["https://lrclib.net/api/get?artist_name=A+B&track_name=T%26U&duration=3"]
    assert fake.user_agents[0].startswith("MusicDesk/") and fake.user_agents[0].endswith("(personal music library app)")
    assert fake.timeouts == [30]


@pytest.mark.parametrize("code", [400, 404])
def test_unknown_song_is_none(code):
    assert client(FakeUrlopen(http_error(code))).get("get") is None


def test_other_client_errors_raise():
    with pytest.raises(urllib.error.HTTPError):
        client(FakeUrlopen(http_error(403))).get("get")


def test_overload_honours_retry_after_capped_at_60():
    sleep = SleepRecorder()
    fake = FakeUrlopen(http_error(503, "5"), http_error(429, "120"), http_error(503, "soon"), {"id": 1})
    assert client(fake, sleep).get("get") == {"id": 1}
    assert sleep.delays == [5, 60, 3]  # the third has no usable header: min(10, attempt)


def test_overload_gives_up_after_ten_retries():
    sleep = SleepRecorder()
    fake = FakeUrlopen(*[http_error(503)] * 11)
    with pytest.raises(urllib.error.HTTPError):
        client(fake, sleep).get("get")
    assert sleep.delays == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]


@pytest.mark.parametrize("error", [http_error(502), urllib.error.URLError("down"), TimeoutError(), ConnectionError()])
def test_server_and_network_errors_get_four_attempts(error):
    sleep = SleepRecorder()
    fake = FakeUrlopen(error, error, error, error)
    with pytest.raises(type(error)):
        client(fake, sleep).get("get")
    assert len(fake.urls) == 4
    assert sleep.delays == [2, 4, 8]


def test_jitter_is_added():
    sleep = SleepRecorder()
    LrclibClient(urlopen=FakeUrlopen(http_error(500), {}), sleep=sleep, jitter=lambda a, b: b).get("get")
    assert sleep.delays == [2.5]


# -- fetch --

def test_exact_match_first():
    fake = FakeUrlopen(RECORD)
    entry = fetch_lyrics(TRACK, client(fake), TODAY)
    assert entry == {"status": FOUND, "source": "lrclib", "lrclib_id": 7, "plain": "Line one\nLine two",
                     "synced": SYNCED, "checked": "2026-10-15"}
    assert len(fake.urls) == 1 and "/get?" in fake.urls[0] and "album_name=Fixture+Album" in fake.urls[0]


def test_search_without_album_or_duration():
    for track in (Track("Fixture Artist", "First", None, 200), Track("Fixture Artist", "First", "Album", 0)):
        fake = FakeUrlopen([RECORD])
        assert fetch_lyrics(track, client(fake), TODAY)["status"] == FOUND
        assert ["/search?" in url for url in fake.urls] == [True]


def test_search_filters_duration_and_artist_and_prefers_synced():
    results = [
        dict(RECORD, id=1, duration=204),  # 4 s off
        dict(RECORD, id=2, artistName="Someone Else"),
        dict(RECORD, id=3, syncedLyrics=None),
        dict(RECORD, id=4, artistName="fixture-artist & friends", duration=197),  # normalized containment, 3 s
    ]
    fake = FakeUrlopen(routes={"get": [http_error(404)], "search": [results]})
    assert fetch_lyrics(TRACK, client(fake), TODAY)["lrclib_id"] == 4


def test_no_candidate_is_not_found():
    fake = FakeUrlopen(routes={"get": [None], "search": [[dict(RECORD, artistName="Other")]]})
    assert fetch_lyrics(TRACK, client(fake), TODAY) == {"status": NOT_FOUND, "checked": "2026-10-15"}


def test_instrumental_and_empty_records():
    entry = fetch_lyrics(TRACK, client(FakeUrlopen(dict(RECORD, instrumental=True))), TODAY)
    assert entry == {"status": INSTRUMENTAL, "lrclib_id": 7, "checked": "2026-10-15"}
    empty = dict(RECORD, plainLyrics=None, syncedLyrics=None)
    assert fetch_lyrics(TRACK, client(FakeUrlopen(empty)), TODAY)["status"] == NOT_FOUND


def test_synced_only_gets_stripped_plain():
    entry = fetch_lyrics(TRACK, client(FakeUrlopen(dict(RECORD, plainLyrics=""))), TODAY)
    assert entry["plain"] == "Line one\nLine two"
    assert entry["synced"] == SYNCED


def test_strip_timestamps():
    assert strip_timestamps("[01:02.345] a\n  [1:02] b \n[01:02:03]c\n[ar:Artist]\n") == "a\nb\nc\n[ar:Artist]"


# -- cache and lookup --

def test_cache_key():
    assert cache_key("ABBA", "Ça") == "abba\x1fça"


@pytest.mark.parametrize(("entry", "fresh"), [
    (None, False),
    ({"status": FOUND, "checked": "2000-01-01"}, True),
    ({"status": INSTRUMENTAL, "checked": "2000-01-01"}, True),
    ({"status": NOT_FOUND, "checked": "2026-09-15"}, True),  # exactly 30 days
    ({"status": NOT_FOUND, "checked": "2026-09-14"}, False),
])
def test_is_fresh(entry, fresh):
    assert is_fresh(entry, TODAY) is fresh


def test_missing_tags():
    assert lookup(Track(None, "Title", None, 1), DictCache(), None, today=TODAY) == {"status": MISSING_TAGS}
    assert lookup(Track("Artist", None, None, 1), DictCache(), None, today=TODAY) == {"status": MISSING_TAGS}


def test_fresh_cache_entry_is_used_offline_or_not():
    cached = {"status": FOUND, "plain": "x", "checked": "2026-01-01"}
    cache = DictCache({cache_key(TRACK.artist, TRACK.title): cached})
    assert lookup(TRACK, cache, client(FakeUrlopen()), today=TODAY) is cached
    assert lookup(TRACK, cache, None, offline=True, today=TODAY) is cached


def test_offline_without_cache():
    assert lookup(TRACK, DictCache(), client(FakeUrlopen()), offline=True, today=TODAY) == {"status": NOT_LOOKED_UP}


def test_stale_not_found_is_looked_up_again_and_cached():
    key = cache_key(TRACK.artist, TRACK.title)
    cache = DictCache({key: {"status": NOT_FOUND, "checked": "2026-01-01"}})
    entry = lookup(TRACK, cache, client(FakeUrlopen(RECORD)), today=TODAY)
    assert entry["status"] == FOUND
    assert cache.get(key) is entry


def test_errors_are_not_cached():
    cache = DictCache()
    entry = lookup(TRACK, cache, client(FakeUrlopen(http_error(403))), today=TODAY)
    assert entry == {"status": ERROR, "error": "HTTPError: HTTP Error 403: HTTP 403"}
    assert cache.entries == {}


def test_a_lyrics_tag_does_not_skip_the_lookup():
    """Deliberate change from the script (SPEC §17)."""
    track = Track("Fixture Artist", "First", "Fixture Album", 200, embedded="Old lyrics")
    fake = FakeUrlopen(RECORD)
    assert lookup(track, DictCache(), client(fake), today=TODAY)["status"] == FOUND
    assert len(fake.urls) == 1


def test_circuit_breaker():
    breaker = CircuitBreaker(threshold=3)
    for _ in range(2):
        breaker.record({"status": ERROR})
    breaker.record({"status": NOT_LOOKED_UP})  # neither a success nor a failure
    assert not breaker.tripped
    breaker.record({"status": ERROR})
    assert breaker.tripped
    breaker.record({"status": NOT_FOUND})
    assert not breaker.tripped and breaker.consecutive == 0
    assert CircuitBreaker().threshold == LyricsOptions().max_consecutive_failures == 10


# -- reading tracks --

def test_read_track_from_tags(fixtures_dir):
    track = read_track(fixtures_dir / "cleanup/lrc/with_lyrics.flac")
    assert (track.artist, track.title, track.album, track.duration) == ("Fixture Artist", "With Lyrics",
                                                                        "Fixture Album", 1)
    assert track.embedded.startswith("[00:00.00]Fixture ly")


def test_read_track_strips_and_falls_back_to_album_artist(fixture_copy):
    path = fixture_copy("formats/tone_aac.m4a")
    tagged = TaggedFile(path)
    tagged.set_fields({"artist": "   ", "albumartist": "  Album Artist ", "title": " Padded ", "album": " "})
    tagged.save()
    track = read_track(path)
    assert (track.artist, track.title, track.album) == ("Album Artist", "Padded", None)


def test_read_track_file_name_fallback(tmp_path, fixtures_dir):
    path = tmp_path / "03 - Some Artist - Some Title.mp3"
    path.write_bytes((fixtures_dir / "formats/tone.mp3").read_bytes())
    tagged = TaggedFile(path)
    tagged.set_fields({"artist": None, "albumartist": None, "title": None})
    tagged.save()
    track = read_track(path)
    assert (track.artist, track.title) == ("Some Artist", "Some Title")


def test_read_track_on_a_file_without_tags(fixtures_dir):
    track = read_track(fixtures_dir / "formats/tone.aac")
    assert (track.artist, track.title, track.album, track.embedded) == (None, "tone", None, None)


# -- embedding --

FOUND_ENTRY = {"status": FOUND, "source": "lrclib", "lrclib_id": 7, "plain": "Line one\nLine two", "synced": SYNCED,
               "checked": "2026-10-15"}


@pytest.mark.parametrize("name", ["tone.flac", "tone.mp3", "tone_aac.m4a", "tone.ogg", "tone.wma"])
def test_embed_writes_synced_lyrics(fixture_copy, name):
    path = fixture_copy(f"formats/{name}")
    assert embed_lyrics(path, FOUND_ENTRY, only_if_empty=True) is EmbedResult.WRITTEN
    assert TaggedFile(path).fields().lyrics == SYNCED
    assert embed_lyrics(path, FOUND_ENTRY, only_if_empty=False) is EmbedResult.SAME_TEXT


def test_embed_plain_when_not_synced(fixture_copy):
    path = fixture_copy("formats/tone.flac")
    embed_lyrics(path, dict(FOUND_ENTRY, synced=None), only_if_empty=True)
    assert TaggedFile(path).fields().lyrics == "Line one\nLine two"


def test_auto_embed_never_replaces_lyrics(fixture_copy):
    path = fixture_copy("cleanup/lrc/with_lyrics.flac")
    before = TaggedFile(path).fields().lyrics
    assert embed_lyrics(path, FOUND_ENTRY, only_if_empty=True) is EmbedResult.HAS_OTHER_LYRICS
    assert TaggedFile(path).fields().lyrics == before
    assert embed_lyrics(path, FOUND_ENTRY, only_if_empty=False) is EmbedResult.WRITTEN
    assert TaggedFile(path).fields().lyrics == SYNCED


def test_embed_keeps_id3v23(fixture_copy):
    path = fixture_copy("replaygain/Fixture Artist/Album B/02 Quiet.mp3")
    assert ID3(path).version[1] == 3
    embed_lyrics(path, FOUND_ENTRY, only_if_empty=True)
    assert ID3(path).version[1] == 3


def test_embed_refusals(fixture_copy):
    assert embed_lyrics(fixture_copy("formats/tone.aac"), FOUND_ENTRY, only_if_empty=True) \
        is EmbedResult.NOT_EMBEDDABLE
    path = fixture_copy("formats/tone.flac")
    for entry in ({"status": NOT_FOUND, "checked": "x"}, {"status": INSTRUMENTAL}, {"status": ERROR, "error": "x"}):
        assert embed_lyrics(path, entry, only_if_empty=False) is EmbedResult.NOTHING_TO_EMBED
    assert TaggedFile(path).fields().lyrics is None


def test_search_urls():
    genius, google = search_urls("AC/DC", "It's a Long Way (Live)")
    assert genius == "https://genius.com/search?q=AC%2FDC%20It's%20a%20Long%20Way%20(Live)"
    assert google == "https://www.google.com/search?q=AC%2FDC%20It's%20a%20Long%20Way%20(Live)+lyrics"
