"""Vorbis comments (FLAC and the Ogg formats)."""

import base64

from mutagen.flac import FLAC, Picture

from refrain.core.formats import TagFamily
from refrain.core.tags.adapter import Adapter
from refrain.core.tags.fields import (
    FRONT_COVER,
    Cover,
    guess_mime,
    parse_int,
    parse_pair,
)


class VorbisAdapter(Adapter):
    family = TagFamily.VORBIS
    KEYS = {"title": "TITLE", "artist": "ARTIST", "albumartist": "ALBUMARTIST", "album": "ALBUM", "year": "DATE",
            "genre": "GENRE", "composer": "COMPOSER", "comment": "COMMENT", "lyrics": "LYRICS"}
    PAIRS = {"track": ("TRACKNUMBER", "TRACKTOTAL", "TOTALTRACKS"), "disc": ("DISCNUMBER", "DISCTOTAL", "TOTALDISCS")}

    @staticmethod
    def first(tags, *keys) -> str | None:
        for key in keys:
            values = tags.get(key)  # VComment keys match ignoring case
            if values:
                return values[0]
        return None

    def read(self, audio) -> dict:
        tags = audio.tags
        if tags is None:
            return {}
        values: dict[str, str | int | None] = {name: self.first(tags, key) for name, key in self.KEYS.items()}
        if values["lyrics"] is None:
            values["lyrics"] = self.first(tags, "UNSYNCEDLYRICS")
        for name, (number_key, total_key, legacy_total_key) in self.PAIRS.items():
            number, pair_total = parse_pair(self.first(tags, number_key))
            total = parse_int(self.first(tags, total_key, legacy_total_key))
            values[name] = number
            values[f"{name}_total"] = total if total is not None else pair_total
        return values

    def delete(self, audio, *keys) -> None:
        for key in keys:
            if key in audio.tags:
                del audio.tags[key]

    def write_text(self, audio, name, value) -> None:
        self.ensure_tags(audio)
        key = self.KEYS[name]
        if name == "lyrics":
            self.delete(audio, "UNSYNCEDLYRICS")  # else it would show through as the fallback
        if value is None:
            self.delete(audio, key)
        else:
            audio.tags[key] = value

    def write_pair(self, audio, name, number, total) -> None:
        self.ensure_tags(audio)
        number_key, total_key, legacy_total_key = self.PAIRS[name]
        self.delete(audio, number_key, total_key, legacy_total_key)
        if number is not None:
            audio.tags[number_key] = str(number)
        if total is not None:
            audio.tags[total_key] = str(total)

    def get_replaygain(self, audio, key) -> str | None:
        return self.first(audio.tags, key) if audio.tags is not None else None

    def set_replaygain(self, audio, key, value) -> None:
        self.ensure_tags(audio)
        audio.tags[key.upper()] = value  # replaces every case variant

    def pictures(self, audio) -> list[Picture]:
        if isinstance(audio, FLAC):
            return list(audio.pictures)
        if audio.tags is None:
            return []
        pictures = []
        for encoded in audio.tags.get("METADATA_BLOCK_PICTURE", []):
            try:
                pictures.append(Picture(base64.b64decode(encoded)))
            except Exception:  # noqa: BLE001 - a corrupt block is ignored, as players do
                continue
        return pictures

    def set_pictures(self, audio, pictures: list[Picture]) -> None:
        if isinstance(audio, FLAC):
            audio.clear_pictures()
            for picture in pictures:
                audio.add_picture(picture)
            return
        self.ensure_tags(audio)
        self.delete(audio, "METADATA_BLOCK_PICTURE")
        if pictures:
            audio.tags["METADATA_BLOCK_PICTURE"] = [base64.b64encode(p.write()).decode("ascii") for p in pictures]

    def cover(self, audio) -> Cover | None:
        pictures = self.pictures(audio)
        chosen = next((p for p in pictures if p.type == FRONT_COVER), pictures[0] if pictures else None)
        return Cover(chosen.data, chosen.mime or guess_mime(chosen.data)) if chosen else None

    def set_cover(self, audio, cover) -> None:
        pictures = self.pictures(audio)
        fronts = [p for p in pictures if p.type == FRONT_COVER]
        # Removing takes away what cover() shows: the front pictures, else the first picture.
        removed = fronts if fronts or cover is not None else pictures[:1]
        kept = [p for p in pictures if p not in removed]
        if cover is not None:
            picture = Picture()
            picture.type = FRONT_COVER
            picture.mime = cover.mime
            picture.data = cover.data
            kept.insert(0, picture)
        self.set_pictures(audio, kept)
