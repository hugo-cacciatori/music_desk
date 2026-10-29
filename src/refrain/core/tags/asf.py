"""ASF attributes (WMA), with WM/Picture packing."""

import struct

from mutagen.asf import ASFByteArrayAttribute  # pyright: ignore[reportPrivateImportUsage]

from refrain.core.formats import TagFamily
from refrain.core.tags.adapter import Adapter
from refrain.core.tags.fields import FRONT_COVER, Cover, TagError, format_pair, guess_mime, parse_int, parse_pair


def pack_asf_picture(cover: Cover) -> bytes:
    """WM/Picture: type, size, MIME and description as null-terminated UTF-16LE, then the data."""
    return (struct.pack("<bI", FRONT_COVER, len(cover.data)) + (cover.mime + "\0").encode("utf-16-le")
            + "\0".encode("utf-16-le") + cover.data)


def unpack_asf_picture(raw: bytes) -> tuple[int, Cover]:
    picture_type, size = struct.unpack_from("<bI", raw)
    position, strings = 5, []
    for _ in range(2):  # MIME, description
        end = position
        while raw[end:end + 2] != b"\0\0":
            end += 2
        strings.append(raw[position:end].decode("utf-16-le"))
        position = end + 2
    data = raw[position:position + size]
    return picture_type, Cover(data, strings[0] or guess_mime(data))


class ASFAdapter(Adapter):
    family = TagFamily.ASF
    KEYS = {"title": "Title", "artist": "Author", "albumartist": "WM/AlbumArtist", "album": "WM/AlbumTitle",
            "year": "WM/Year", "genre": "WM/Genre", "composer": "WM/Composer", "comment": "Description",
            "lyrics": "WM/Lyrics"}

    def read(self, audio) -> dict:
        tags = audio.tags
        values: dict[str, str | int | None] = {name: (str(tags[key][0]) if tags.get(key) else None)
                                               for name, key in self.KEYS.items()}
        track = tags.get("WM/TrackNumber")
        values["track"], values["track_total"] = (parse_int(track[0].value) if track else None), None
        disc = tags.get("WM/PartOfSet")
        values["disc"], values["disc_total"] = parse_pair(str(disc[0]) if disc else None)
        return values

    def put(self, audio, key, value) -> None:
        if key in audio.tags:
            del audio.tags[key]
        if value is not None:
            audio.tags[key] = [value]

    def write_text(self, audio, name, value) -> None:
        self.put(audio, self.KEYS[name], value)

    def write_pair(self, audio, name, number, total) -> None:
        if name == "track":
            if total is not None:
                raise TagError("WMA has no standard track total field")
            self.put(audio, "WM/TrackNumber", None if number is None else str(number))
        else:
            self.put(audio, "WM/PartOfSet", format_pair(number, total))

    def matching_keys(self, audio, key) -> list[str]:
        return [k for k in audio.tags.keys() if k.lower() == key.lower()]

    def get_replaygain(self, audio, key) -> str | None:
        keys = self.matching_keys(audio, key)
        return str(audio.tags[keys[0]][0]) if keys else None

    def set_replaygain(self, audio, key, value) -> None:
        for existing in self.matching_keys(audio, key):
            del audio.tags[existing]
        audio.tags[key.upper()] = [value]

    def pictures(self, audio) -> list[tuple[int, Cover]]:
        return [unpack_asf_picture(attr.value) for attr in audio.tags.get("WM/Picture", [])]

    def cover(self, audio) -> Cover | None:
        pictures = self.pictures(audio)
        chosen = next((c for t, c in pictures if t == FRONT_COVER), pictures[0][1] if pictures else None)
        return chosen

    def set_cover(self, audio, cover) -> None:
        attributes = list(audio.tags.get("WM/Picture", []))
        types = [t for t, _ in self.pictures(audio)]
        has_front = FRONT_COVER in types
        if has_front or cover is not None:
            kept = [a for a, t in zip(attributes, types, strict=True) if t != FRONT_COVER]
        else:
            kept = attributes[1:]
        if cover is not None:
            kept.insert(0, ASFByteArrayAttribute(pack_asf_picture(cover)))
        if "WM/Picture" in audio.tags:
            del audio.tags["WM/Picture"]
        if kept:
            audio.tags["WM/Picture"] = kept
