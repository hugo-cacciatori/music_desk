"""Lyrics test helpers: an in-memory cache with the script's entry shape (what `LyricsStore` must behave like), and
a file's lookup data as the indexer gets it."""

from refrain.core.lyrics import Track, track_from_fields
from refrain.core.tags import TaggedFile


def read_track(path) -> Track:
    """The file's tags read once, then the lookup data (`track_from_fields`)."""
    tagged = TaggedFile(path)
    return track_from_fields(tagged.fields(), tagged.path.name, tagged.stream_info().duration)


class DictCache:
    def __init__(self, entries: dict[str, dict] | None = None) -> None:
        self.entries = dict(entries or {})

    def get(self, key: str) -> dict | None:
        return self.entries.get(key)

    def put(self, key: str, entry: dict) -> None:
        self.entries[key] = entry
