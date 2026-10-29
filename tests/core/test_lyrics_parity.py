"""core/lyrics.py against the frozen seek_lyrics.py (SPEC §17 "Behaviors to preserve exactly": Lyrics).

Both sides get the same scripted LRCLIB answers. Only the User-Agent and the `checked` date may differ.
"""

import urllib.request
from types import SimpleNamespace

import pytest
from lrclib_fake import FakeUrlopen, SleepRecorder, http_error, no_jitter
from lyrics_helpers import read_track

from refrain.core.lyrics import (
    LrclibClient,
    Track,
    cache_key,
    fetch_lyrics,
    matches,
    normalize,
    parse_from_filename,
    strip_timestamps,
)

RECORD = {"id": 7, "artistName": "Fixture Artist", "trackName": "First", "duration": 200, "instrumental": False,
          "plainLyrics": "Line one\nLine two", "syncedLyrics": "[00:01.00]Line one\n[00:02.50] Line two"}


def song(track: Track) -> dict:
    return {"artist": track.artist, "title": track.title, "album": track.album, "duration": track.duration}


@pytest.mark.parametrize("text", ["", None, "AC/DC", "Sigur Rós", "The_Beatles!", "  Ça va  ", "ß", "a-b c"])
def test_normalize(reference_seek_lyrics, text):
    assert normalize(text) == reference_seek_lyrics.normalize(text)


@pytest.mark.parametrize("lrc", ["[00:01.00]a\n[00:02]b", "  [1:02.3]  x  \n\n[ti:Title]\nplain", "", "[00:01:02]c"])
def test_strip_timestamps(reference_seek_lyrics, lrc):
    assert strip_timestamps(lrc) == reference_seek_lyrics.strip_timestamps(lrc)


@pytest.mark.parametrize("name", ["01 Artist - Title.flac", "03. Title.mp3", "Artist - Title - Live.wav",
                                  " - Title.flac", "123_Song.flac", "1234 Song.flac", "Song.flac"])
def test_parse_from_filename(reference_seek_lyrics, name):
    assert parse_from_filename(name) == reference_seek_lyrics.parse_from_filename(name)


@pytest.mark.parametrize(("artist", "title"), [("ABBA", "SOS"), ("Ça", "Été"), (None, "x")])
def test_cache_key(reference_seek_lyrics, artist, title):
    assert cache_key(artist, title) == reference_seek_lyrics.cache_key({"artist": artist, "title": title})


@pytest.mark.parametrize("record", [
    {"artistName": "Fixture Artist", "duration": 203},
    {"artistName": "Fixture Artist", "duration": 204},
    {"artistName": "fixture artist feat. x", "duration": None},
    {"artistName": "Fixture", "duration": 200},
    {"artistName": "", "duration": 200},
    {"duration": 200},
])
@pytest.mark.parametrize("duration", [200, 0])
def test_matches(reference_seek_lyrics, record, duration):
    track = Track("Fixture Artist", "First", None, duration)
    assert matches(track, record) == reference_seek_lyrics.matches(song(track), record)


SCENARIOS = {
    "exact": dict(get=[RECORD]),
    "get_404_then_search": dict(get=[http_error(404)], search=[[dict(RECORD, id=1, duration=210),
                                                                dict(RECORD, id=2, syncedLyrics=None),
                                                                dict(RECORD, id=3)]]),
    "busy_then_found": dict(get=[http_error(503, "2"), http_error(429), RECORD]),
    "flaky_search": dict(get=[None], search=[http_error(502), http_error(500), [RECORD]]),
    "instrumental": dict(get=[dict(RECORD, instrumental=True)]),
    "empty_record": dict(get=[dict(RECORD, plainLyrics="", syncedLyrics=None)]),
    "synced_only": dict(get=[dict(RECORD, plainLyrics=None)]),
    "nothing": dict(get=[None], search=[[]]),
    "search_null": dict(get=[http_error(400)], search=[None]),
    "gives_up": dict(get=[http_error(504)] * 4),
}


@pytest.mark.parametrize("track", [Track("Fixture Artist", "First", "Fixture Album", 200),
                                   Track("Fixture Artist", "First", None, 200)], ids=["album", "no_album"])
@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_fetch_lyrics(reference_seek_lyrics, monkeypatch, scenario, track):
    def run(fetch):
        fake = FakeUrlopen(routes={"get": [], "search": [], **SCENARIOS[scenario]})
        sleep = SleepRecorder()
        try:
            result = fetch(fake, sleep)
            result.pop("checked", None)
        except Exception as e:  # noqa: BLE001 - the failure itself is compared
            result = f"{type(e).__name__}: {e}"
        return result, fake.urls, sleep.delays

    def ours(fake, sleep):
        return fetch_lyrics(track, LrclibClient(urlopen=fake, sleep=sleep, jitter=no_jitter))

    def theirs(fake, sleep):
        monkeypatch.setattr(urllib.request, "urlopen", fake)
        monkeypatch.setattr(reference_seek_lyrics, "time", SimpleNamespace(sleep=sleep))
        monkeypatch.setattr(reference_seek_lyrics, "random", SimpleNamespace(uniform=no_jitter))
        return reference_seek_lyrics.fetch_lyrics(song(track))

    assert run(ours) == run(theirs)


@pytest.mark.parametrize("relative", [
    "formats/tone.flac", "formats/tone.mp3", "formats/tone.wav", "edge/edge.flac", "edge/edge.mp3",
    "cleanup/lrc/with_lyrics.flac", "cleanup/single_file_album/Fixture Album.flac",
    "album/Fixture Artist - Fixture Album/missing_title.flac",
    "replaygain/Fixture Artist/Album B/02 Quiet.mp3",
])
def test_read_song(reference_seek_lyrics, fixtures_dir, relative):
    path = fixtures_dir / relative
    track = read_track(path)
    expected = reference_seek_lyrics.read_song(path, fixtures_dir)
    assert (track.artist, track.title, track.album, track.duration, track.embedded) == \
        (expected["artist"], expected["title"], expected["album"], expected["duration"], expected["embedded"])
