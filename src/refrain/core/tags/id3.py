"""ID3v2 (MPEG audio, and the ID3 of WAV, AIFF, DSF, DSDIFF and True Audio)."""


from mutagen.id3 import APIC, COMM, TALB, TCOM, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, TXXX, USLT

from refrain.core.formats import TagFamily
from refrain.core.tags.adapter import Adapter
from refrain.core.tags.fields import FRONT_COVER, REPLAYGAIN_PREFIX, Cover, format_pair, guess_mime, parse_pair
from refrain.core.tags.legacy_text import id3_text


class ID3Adapter(Adapter):
    family = TagFamily.ID3
    FRAMES = {"title": TIT2, "artist": TPE1, "albumartist": TPE2, "album": TALB, "year": TDRC, "genre": TCON,
              "composer": TCOM}
    PAIRS = {"track": TRCK, "disc": TPOS}

    def read(self, audio) -> dict:
        tags = audio.tags
        if tags is None:
            return {}
        values = {}
        for name, frame_class in self.FRAMES.items():
            frame = tags.get(frame_class.__name__)
            if frame is None or not frame.text:
                values[name] = None
            elif frame_class is TCON:
                values[name] = id3_text(frame, frame.genres[0]) if frame.genres else None
            else:
                values[name] = id3_text(frame, frame.text[0])
        comments = [f for f in tags.getall("COMM") if f.desc == ""]
        comments.sort(key=lambda f: f.lang != "eng")
        values["comment"] = id3_text(comments[0], comments[0].text[0]) if comments and comments[0].text else None
        lyrics = [id3_text(f, f.text) for f in tags.getall("USLT") if str(f.text).strip()]
        values["lyrics"] = lyrics[0] if lyrics else None
        for name, frame_class in self.PAIRS.items():
            frame = tags.get(frame_class.__name__)
            values[name], values[f"{name}_total"] = parse_pair(frame.text[0] if frame and frame.text else None)
        return values

    def write_text(self, audio, name, value) -> None:
        self.ensure_tags(audio)
        tags = audio.tags
        if name == "comment":
            for frame in [f for f in tags.getall("COMM") if f.desc == ""]:
                tags.delall(frame.HashKey)
            if value is not None:
                tags.add(COMM(encoding=3, lang="eng", desc="", text=[value]))
        elif name == "lyrics":
            for frame in [f for f in tags.getall("USLT") if f.desc == ""]:
                tags.delall(frame.HashKey)
            if value is not None:
                tags.add(USLT(encoding=3, lang="eng", desc="", text=value))
        else:
            frame_class = self.FRAMES[name]
            tags.delall(frame_class.__name__)
            if value is not None:
                tags.add(frame_class(encoding=3, text=[value]))

    def write_pair(self, audio, name, number, total) -> None:
        self.ensure_tags(audio)
        frame_class = self.PAIRS[name]
        audio.tags.delall(frame_class.__name__)
        text = format_pair(number, total)
        if text is not None:
            audio.tags.add(frame_class(encoding=3, text=[text]))

    @staticmethod
    def find_txxx(audio, name) -> list:  # of TXXX frames (their `text` is set at run time)
        if audio.tags is None:
            return []
        return [frame for frame in audio.tags.getall("TXXX") if frame.desc.lower() == name.lower()]

    def remove_replaygain(self, audio) -> int:
        if audio.tags is None:
            return 0
        frames = [f for f in audio.tags.getall("TXXX") if f.desc.lower().startswith(REPLAYGAIN_PREFIX)]
        for frame in frames:
            audio.tags.delall(frame.HashKey)
        return len(frames)

    def remove_txxx(self, audio, name) -> None:
        for frame in self.find_txxx(audio, name):
            audio.tags.delall(frame.HashKey)

    def get_replaygain(self, audio, key) -> str | None:
        frames = self.find_txxx(audio, key)
        return frames[0].text[0] if frames else None

    def set_replaygain(self, audio, key, value) -> None:
        self.ensure_tags(audio)
        self.remove_txxx(audio, key)
        audio.tags.add(TXXX(encoding=3, desc=key.upper(), text=[value]))

    def set_comment(self, audio, text) -> None:
        self.ensure_tags(audio)
        self.remove_txxx(audio, "comment")  # written by older versions of the script
        self.write_text(audio, "comment", text)

    def cover(self, audio) -> Cover | None:
        pictures = audio.tags.getall("APIC") if audio.tags is not None else []
        chosen = next((p for p in pictures if p.type == FRONT_COVER), pictures[0] if pictures else None)
        return Cover(chosen.data, chosen.mime or guess_mime(chosen.data)) if chosen else None

    def set_cover(self, audio, cover) -> None:
        self.ensure_tags(audio)
        pictures = audio.tags.getall("APIC")
        fronts = [p for p in pictures if p.type == FRONT_COVER]
        for frame in fronts if fronts or cover is not None else pictures[:1]:
            audio.tags.delall(frame.HashKey)
        if cover is not None:
            # APIC frames are keyed by description: don't collide with a picture that stays.
            desc = "" if "APIC:" not in audio.tags else "Front Cover"
            audio.tags.add(APIC(encoding=3, mime=cover.mime, type=FRONT_COVER, desc=desc, data=cover.data))

    def save(self, audio) -> None:
        """Keep the file's ID3 version (v2.3 stays v2.3); new or v2.2 tags become v2.4."""
        version = audio.tags.version[1] if audio.tags is not None and audio.tags.version[1] in (3, 4) else 4
        audio.save(v2_version=version)
