"""LRCLIB lyrics lookups: track reading, the LRCLIB client, matching, cache rules and embedding (SPEC §8, §9;
port of seek_lyrics.py's lookup logic, same requests, retries, statuses and cache entries).

Changes from the script (SPEC §17): every format in §1, tags through core/tags.py, an injectable HTTP opener and
clock, every track is looked up even with a lyrics tag, auto-embed only fills empty tags, and the circuit breaker
only counts here (the LyricsService pauses and retries around it). The cache is a protocol: a dict for now, SQLite
from M2/M4. No songs.html.
"""

import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from pathlib import Path
from typing import Protocol

import music_desk
from music_desk.core.tags import Fields, TagError, TaggedFile

FOUND = "found"
INSTRUMENTAL = "instrumental"
NOT_FOUND = "not_found"
MISSING_TAGS = "missing_tags"
NOT_LOOKED_UP = "not_looked_up"
ERROR = "error"

LRC_TIMESTAMP = re.compile(r"^\s*\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]", re.M)


@dataclass(frozen=True)
class LyricsOptions:
    """The script's constants, with its defaults (the User-Agent names the app instead)."""

    api: str = "https://lrclib.net/api"
    user_agent: str = f"MusicDesk/{music_desk.__version__} (personal music library app)"
    not_found_retry_days: int = 30
    duration_tolerance: int = 3  # seconds between a file and an LRCLIB search result
    request_timeout: int = 30  # seconds
    max_attempts: int = 4  # per request on network errors, with a growing pause between attempts
    max_overload_retries: int = 10  # LRCLIB often answers "server busy, retry in a moment" (503/429)
    max_consecutive_failures: int = 10  # the circuit breaker trips after this many failures in a row
    lookup_threads: int = 4


# ---------------------------------------------------------------------------
# Reading a track
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Track:
    """What a lookup needs from a file. Only artist, title, album and duration are ever sent to LRCLIB."""

    artist: str | None
    title: str | None
    album: str | None
    duration: int
    embedded: str | None = None  # the file's lyrics tag: shown for comparison, never a reason to skip a lookup


def _text(*values) -> str | None:
    """First non-blank value, stripped (the script's tag_text)."""
    for value in values:
        value = str(value).strip() if value is not None else ""
        if value:
            return value
    return None


def parse_from_filename(name):
    """Best-effort 'Artist - Title' fallback when tags are missing."""
    stem = re.sub(r"^\d{1,3}[\s\-._]+", "", Path(name).stem)
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return artist.strip() or None, title.strip()
    return None, stem.strip()


def read_track(path: str | Path) -> Track:
    tagged = TaggedFile(path)
    return track_from_fields(tagged.fields(), tagged.path.name, tagged.stream_info().duration)


def track_from_fields(fields: Fields, file_name: str, duration: float | None) -> Track:
    """The lookup data from already-read tags (the indexer reads each file once)."""
    artist = _text(fields.artist, fields.albumartist)
    title = _text(fields.title)
    if not artist or not title:
        file_artist, file_title = parse_from_filename(file_name)
        artist, title = artist or file_artist, title or file_title
    return Track(
        artist=artist,
        title=title,
        album=_text(fields.album),
        duration=round(duration or 0),
        embedded=_text(fields.lyrics),
    )


# ---------------------------------------------------------------------------
# LRCLIB
# ---------------------------------------------------------------------------

def retry_after(error) -> int | None:
    """Seconds asked by the server's Retry-After header (capped at 60), or None."""
    try:
        return min(60, int(error.headers.get("Retry-After")))
    except (TypeError, ValueError, AttributeError):
        return None


class LrclibClient:
    """GETs LRCLIB endpoints with the script's retry rules. The opener, sleep and jitter are injectable so tests
    never touch the network or wait."""

    def __init__(self, options: LyricsOptions | None = None, *, urlopen: Callable | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 jitter: Callable[[float, float], float] = random.uniform) -> None:
        self.options = options or LyricsOptions()
        self._urlopen = urlopen  # None: urllib's, looked up at call time (so the tests' network guard applies)
        self._sleep = sleep
        self._jitter = jitter

    def get(self, endpoint: str, **params):
        """The decoded JSON, or None for an unknown song.

        "Server busy" answers (503/429) are retried up to max_overload_retries times after the pause the server
        asks for; network errors and other server errors up to max_attempts times with a growing pause. The error
        is raised only when the retries are used up.
        """
        options = self.options
        url = f"{options.api}/{endpoint}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers={"User-Agent": options.user_agent})
        overloads = failures = 0
        while True:
            try:
                urlopen = self._urlopen or urllib.request.urlopen
                with urlopen(request, timeout=options.request_timeout) as response:
                    return json.load(response)
            except urllib.error.HTTPError as e:
                if e.code in (400, 404):  # unknown song, or a query LRCLIB refuses
                    return None
                if e.code in (429, 503):
                    overloads += 1
                    if overloads > options.max_overload_retries:
                        raise
                    delay = retry_after(e) or min(10, overloads)
                elif e.code in (500, 502, 504):
                    failures += 1
                    if failures >= options.max_attempts:
                        raise
                    delay = 2 ** failures
                else:
                    raise
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                failures += 1
                if failures >= options.max_attempts:
                    raise
                delay = 2 ** failures
            # a little randomness so parallel lookups don't all retry at the same instant
            self._sleep(delay + self._jitter(0, 0.5))


def normalize(text):
    return re.sub(r"[\W_]+", "", (text or "").casefold())


def matches(track: Track, record: dict, tolerance: int = LyricsOptions.duration_tolerance) -> bool:
    """Whether an LRCLIB search result is the same recording as the file."""
    if track.duration and record.get("duration") and abs(track.duration - record["duration"]) > tolerance:
        return False
    wanted, found = normalize(track.artist), normalize(record.get("artistName"))
    return bool(wanted and found) and (wanted in found or found in wanted)


def strip_timestamps(lrc):
    return "\n".join(LRC_TIMESTAMP.sub("", line).strip() for line in lrc.splitlines()).strip()


def fetch_lyrics(track: Track, client: LrclibClient, today: date | None = None) -> dict:
    """Look a track up on LRCLIB: exact match first, then a search. Returns a cache entry."""
    record = None
    if track.album and track.duration:
        record = client.get("get", artist_name=track.artist, track_name=track.title,
                            album_name=track.album, duration=track.duration)
    if not record:
        results = client.get("search", track_name=track.title, artist_name=track.artist) or []
        candidates = [r for r in results if matches(track, r, client.options.duration_tolerance)]
        # prefer results that actually carry lyrics, synced ones first
        candidates.sort(key=lambda r: (not r.get("syncedLyrics"), not r.get("plainLyrics")))
        record = candidates[0] if candidates else None

    checked = (today or date.today()).isoformat()
    if not record:
        return {"status": NOT_FOUND, "checked": checked}
    if record.get("instrumental"):
        return {"status": INSTRUMENTAL, "lrclib_id": record.get("id"), "checked": checked}
    if not (record.get("plainLyrics") or record.get("syncedLyrics")):
        return {"status": NOT_FOUND, "checked": checked}
    return {
        "status": FOUND,
        "source": "lrclib",
        "lrclib_id": record.get("id"),
        "plain": record.get("plainLyrics") or strip_timestamps(record["syncedLyrics"]),
        "synced": record.get("syncedLyrics"),
        "checked": checked,
    }


# ---------------------------------------------------------------------------
# Cache and per-track lookup
# ---------------------------------------------------------------------------

def cache_key(artist: str | None, title: str | None) -> str:
    return f"{artist}\x1f{title}".casefold()


class LyricsCache(Protocol):
    def get(self, key: str) -> dict | None: ...
    def put(self, key: str, entry: dict) -> None: ...


class DictCache:
    """In-memory cache with the script's entry shape (the SQLite store replaces it in M4)."""

    def __init__(self, entries: dict[str, dict] | None = None) -> None:
        self.entries = dict(entries or {})

    def get(self, key: str) -> dict | None:
        return self.entries.get(key)

    def put(self, key: str, entry: dict) -> None:
        self.entries[key] = entry


def is_fresh(entry: dict | None, today: date, options: LyricsOptions | None = None) -> bool:
    """A cached entry is used unless it is a not_found older than the retry delay."""
    if not entry:
        return False
    retry_days = (options or LyricsOptions()).not_found_retry_days
    retry_before = (today - timedelta(days=retry_days)).isoformat()
    return entry["status"] != NOT_FOUND or entry["checked"] >= retry_before


def lookup(track: Track, cache: LyricsCache, client: LrclibClient | None, *, offline: bool = False,
           today: date | None = None) -> dict:
    """The lyrics entry for one track (SPEC §9). Errors are returned, not cached, so they are retried later.

    Unlike the script, a lyrics tag in the file doesn't skip the lookup.
    """
    today = today or date.today()
    if not (track.artist and track.title):
        return {"status": MISSING_TAGS}
    key = cache_key(track.artist, track.title)
    cached = cache.get(key)
    if cached is not None and is_fresh(cached, today, client.options if client else None):
        return cached
    if offline or client is None:
        return {"status": NOT_LOOKED_UP}
    try:
        entry = fetch_lyrics(track, client, today)
    except Exception as e:  # noqa: BLE001 - one failing track must not stop the others
        return {"status": ERROR, "error": f"{type(e).__name__}: {e}"}
    cache.put(key, entry)
    return entry


class CircuitBreaker:
    """Counts lookup failures in a row. The script stopped the run when it tripped; the LyricsService pauses and
    retries later instead (SPEC §9)."""

    def __init__(self, threshold: int = LyricsOptions.max_consecutive_failures) -> None:
        self.threshold = threshold
        self.consecutive = 0

    def record(self, entry: dict) -> None:
        if entry.get("status") == ERROR:
            self.consecutive += 1
        elif entry.get("status") != NOT_LOOKED_UP:
            self.consecutive = 0

    @property
    def tripped(self) -> bool:
        return self.consecutive >= self.threshold

    def reset(self) -> None:
        self.consecutive = 0


# ---------------------------------------------------------------------------
# Writing lyrics into the file
# ---------------------------------------------------------------------------

class EmbedResult(Enum):
    WRITTEN = "written"
    SAME_TEXT = "same_text"  # the tag already holds these lyrics
    HAS_OTHER_LYRICS = "has_other_lyrics"  # only_if_empty, and the tag holds something else
    NOTHING_TO_EMBED = "nothing_to_embed"  # the entry isn't a found LRCLIB result
    NOT_EMBEDDABLE = "not_embeddable"  # the format has no tags


def embed_text(entry: dict) -> str | None:
    """What the script's --embed writes: the synced lyrics when available, else the plain ones."""
    if entry.get("status") != FOUND:
        return None
    return entry.get("synced") or entry.get("plain")


def embed_lyrics(path: str | Path, entry: dict, *, only_if_empty: bool) -> EmbedResult:
    """Write an LRCLIB result into the Lyrics tag. Auto-embed passes only_if_empty=True and never replaces a tag;
    "Save to file tags" passes False once the user confirmed replacing different lyrics."""
    text = embed_text(entry)
    if not text:
        return EmbedResult.NOTHING_TO_EMBED
    tagged = TaggedFile(path)
    if not tagged.info.tags:
        return EmbedResult.NOT_EMBEDDABLE
    current = tagged.fields().lyrics
    if current is not None and current.strip() == text.strip():
        return EmbedResult.SAME_TEXT
    if only_if_empty and current is not None and current.strip():
        return EmbedResult.HAS_OTHER_LYRICS
    try:
        tagged.set_fields({"lyrics": text})
    except TagError:
        return EmbedResult.NOT_EMBEDDABLE
    tagged.save()
    return EmbedResult.WRITTEN


def search_urls(artist: str | None, title: str | None) -> tuple[str, str]:
    """The Genius and Google search links of the script's page (encoded like JS encodeURIComponent)."""
    query = urllib.parse.quote(" ".join(part for part in (artist, title) if part), safe="-_.!~*'()")
    return f"https://genius.com/search?q={query}", f"https://www.google.com/search?q={query}+lyrics"
