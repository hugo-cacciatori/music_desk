"""APEv2 (WavPack, Musepack, TAK, OptimFROG)."""


from mutagen.apev2 import BINARY, APEValue

from refrain.core.formats import TagFamily
from refrain.core.tags.adapter import Adapter
from refrain.core.tags.fields import Cover, format_pair, guess_mime, parse_pair


class APEv2Adapter(Adapter):
    family = TagFamily.APEV2
    KEYS = {"title": "Title", "artist": "Artist", "albumartist": "Album Artist", "album": "Album", "year": "Year",
            "genre": "Genre", "composer": "Composer", "comment": "Comment", "lyrics": "Lyrics"}
    PAIRS = {"track": "Track", "disc": "Disc"}
    COVER_KEY = "Cover Art (Front)"

    def read(self, audio) -> dict:
        tags = audio.tags  # APEv2 keys match ignoring case
        if tags is None:
            return {}
        values: dict[str, str | int | None] = {name: (str(tags[key]) if key in tags else None)
                                               for name, key in self.KEYS.items()}
        for name, key in self.PAIRS.items():
            values[name], values[f"{name}_total"] = parse_pair(str(tags[key]) if key in tags else None)
        return values

    def put(self, audio, key, value) -> None:
        self.ensure_tags(audio)
        if key in audio.tags:
            del audio.tags[key]
        if value is not None:
            audio.tags[key] = value

    def write_text(self, audio, name, value) -> None:
        self.put(audio, self.KEYS[name], value)

    def write_pair(self, audio, name, number, total) -> None:
        self.put(audio, self.PAIRS[name], format_pair(number, total))

    def get_replaygain(self, audio, key) -> str | None:
        return str(audio.tags[key]) if audio.tags is not None and key in audio.tags else None

    def set_replaygain(self, audio, key, value) -> None:
        self.put(audio, key.upper(), value)

    def cover(self, audio) -> Cover | None:
        if audio.tags is None or self.COVER_KEY not in audio.tags:
            return None
        _, _, data = audio.tags[self.COVER_KEY].value.partition(b"\0")
        return Cover(data, guess_mime(data))

    def set_cover(self, audio, cover) -> None:
        if cover is None:
            self.put(audio, self.COVER_KEY, None)
            return
        name = b"cover.png" if cover.mime == "image/png" else b"cover.jpg"
        self.put(audio, self.COVER_KEY, APEValue(name + b"\0" + cover.data, BINARY))
