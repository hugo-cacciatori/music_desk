"""MP4 atoms (AAC and ALAC in .m4a, .mp4, .m4b)."""


from mutagen.mp4 import AtomDataType, MP4Cover, MP4FreeForm

from refrain.core.formats import TagFamily
from refrain.core.tags.adapter import Adapter
from refrain.core.tags.fields import Cover


class MP4Adapter(Adapter):
    family = TagFamily.MP4
    KEYS = {"title": "\xa9nam", "artist": "\xa9ART", "albumartist": "aART", "album": "\xa9alb", "year": "\xa9day",
            "genre": "\xa9gen", "composer": "\xa9wrt", "comment": "\xa9cmt", "lyrics": "\xa9lyr"}
    PAIRS = {"track": "trkn", "disc": "disk"}
    FREEFORM = "----:com.apple.iTunes:"

    def read(self, audio) -> dict:
        tags = audio.tags
        if tags is None:
            return {}
        values: dict[str, str | int | None] = {name: (str(tags[key][0]) if tags.get(key) else None)
                                               for name, key in self.KEYS.items()}
        for name, key in self.PAIRS.items():
            pair = tags.get(key)
            values[name], values[f"{name}_total"] = (pair[0][0], pair[0][1]) if pair else (None, None)
        return values

    def write_text(self, audio, name, value) -> None:
        self.ensure_tags(audio)
        key = self.KEYS[name]
        if value is None:
            audio.tags.pop(key, None)
        else:
            audio.tags[key] = [value]

    def write_pair(self, audio, name, number, total) -> None:
        self.ensure_tags(audio)
        key = self.PAIRS[name]
        if number is None and total is None:
            audio.tags.pop(key, None)
        else:
            audio.tags[key] = [(number or 0, total or 0)]

    def freeform_keys(self, audio, key) -> list[str]:
        wanted = (self.FREEFORM + key).lower()
        return [k for k in (audio.tags or {}) if k.lower() == wanted]

    def replaygain_name(self, key: str) -> str:
        return key[len(self.FREEFORM):] if key.startswith(self.FREEFORM) else ""

    def get_replaygain(self, audio, key) -> str | None:
        keys = self.freeform_keys(audio, key)
        return bytes(audio.tags[keys[0]][0]).decode("utf-8") if keys else None

    def set_replaygain(self, audio, key, value) -> None:
        self.ensure_tags(audio)
        for existing in self.freeform_keys(audio, key):
            del audio.tags[existing]
        audio.tags[self.FREEFORM + key.upper()] = [MP4FreeForm(value.encode("utf-8"), dataformat=AtomDataType.UTF8)]

    def cover(self, audio) -> Cover | None:
        covers = audio.tags.get("covr") if audio.tags is not None else None
        if not covers:
            return None
        mime = "image/png" if covers[0].imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
        return Cover(bytes(covers[0]), mime)

    def set_cover(self, audio, cover) -> None:
        # MP4 has no picture types: the first covr image is the front cover.
        self.ensure_tags(audio)
        covers = list(audio.tags.get("covr", []))[1:]
        if cover is not None:
            image_format = MP4Cover.FORMAT_PNG if cover.mime == "image/png" else MP4Cover.FORMAT_JPEG
            covers.insert(0, MP4Cover(cover.data, imageformat=image_format))
        if covers:
            audio.tags["covr"] = covers
        else:
            audio.tags.pop("covr", None)
