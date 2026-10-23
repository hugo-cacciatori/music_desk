"""Shared by the parity tests: a comparable dump of every tag in a FLAC or MP3."""


def tag_dump(audio) -> list:
    """Every tag of a FLAC or MP3, comparable across the two implementations (Vorbis keys ignoring case)."""
    if hasattr(audio.tags, "getall"):
        return sorted((key, frame.pprint()) for key, frame in audio.tags.items())
    return sorted((key.lower(), value) for key, value in audio.tags) + [p.data for p in audio.pictures]
