"""The base of the per-family tag adapters: raw reads, writes, ReplayGain keys, cover."""



from refrain.core.formats import TagFamily
from refrain.core.tags.fields import REPLAYGAIN_PREFIX, Cover


class Adapter:
    """Base adapter. `read` returns raw values: ints for numbers (0 kept), strings as stored."""

    family: TagFamily

    def read(self, audio) -> dict:
        raise NotImplementedError

    def write_text(self, audio, name: str, value: str | None) -> None:
        raise NotImplementedError

    def write_pair(self, audio, name: str, number: int | None, total: int | None) -> None:
        """name is 'track' or 'disc'."""
        raise NotImplementedError

    def get_replaygain(self, audio, key: str) -> str | None:
        raise NotImplementedError

    def set_replaygain(self, audio, key: str, value: str) -> None:
        raise NotImplementedError

    def set_comment(self, audio, text: str) -> None:
        self.write_text(audio, "comment", text)

    def replaygain_name(self, key: str) -> str:
        """The ReplayGain name a tag key carries (MP4 wraps it in a freeform key)."""
        return key

    def remove_replaygain(self, audio) -> int:
        """Delete every REPLAYGAIN_* tag (any case: rsgain's four values, and any other a tool wrote, such as a
        reference loudness). Returns how many keys went."""
        if audio.tags is None:
            return 0
        keys = [k for k in list(audio.tags.keys()) if self.replaygain_name(k).lower().startswith(REPLAYGAIN_PREFIX)]
        for key in keys:
            del audio.tags[key]
        return len(keys)

    def cover(self, audio) -> Cover | None:
        raise NotImplementedError

    def set_cover(self, audio, cover: Cover | None) -> None:
        """Replace the front cover, or remove it with None. Other pictures stay."""
        raise NotImplementedError

    def save(self, audio) -> None:
        audio.save()

    @staticmethod
    def ensure_tags(audio) -> None:
        if audio.tags is None:
            audio.add_tags()
