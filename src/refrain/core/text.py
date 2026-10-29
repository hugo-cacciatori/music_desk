"""Wording helpers for the messages and reports every layer writes."""


def plural(count: int, word: str, plural_word: str | None = None) -> str:
    """"1 track", "2 tracks"; `plural_word` when adding an s is wrong ("2 clipping fixes")."""
    return f"{count} {word if count == 1 else plural_word or word + 's'}"
