"""Which fields a Metadata tab save writes (SPEC §7), from the texts in its fields: for one file, those that differ
from it; for several, the shared fields that were edited or cleared. None clears a field."""

from refrain.core.tags import Fields

NUMBER_FIELDS = ("track", "track_total", "disc", "disc_total")
PER_TRACK_FIELDS = ("title", "track")  # one file at a time (SPEC §7)
SHARED_FIELDS = ("artist", "albumartist", "album", "track_total", "disc", "disc_total", "year", "genre", "composer",
                 "comment")
MULTIPLE = object()  # the files of a multi-selection differ on this field


def parse_field(key: str, text: str):
    if not text.strip():
        return None
    return int(text) if key in NUMBER_FIELDS else text


def common_values(fields: list[Fields]) -> dict[str, object]:
    """Each shared field's value when every file has the same, else MULTIPLE."""
    common: dict[str, object] = {}
    for key in SHARED_FIELDS:
        values = {getattr(f, key) for f in fields}
        common[key] = values.pop() if len(values) == 1 else MULTIPLE
    return common


def single_changes(fields: Fields, texts: dict[str, str]) -> dict:
    """The fields whose text differs from the file's value."""
    changed = {}
    for key, text in texts.items():
        original = getattr(fields, key)
        if text == ("" if original is None else str(original)):
            continue  # untouched, even when the file holds blank text such as "  "
        value = parse_field(key, text)
        if value != original:
            changed[key] = value
    return changed


def multi_changes(texts: dict[str, str], touched: set[str], cleared: set[str], common: dict[str, object]) -> dict:
    """The shared fields edited or cleared; a "(multiple values)" field left empty stays untouched."""
    changed = {}
    for key in SHARED_FIELDS:
        if key not in touched:
            continue
        text = texts[key]
        if not text.strip() and key not in cleared and common[key] is MULTIPLE:
            continue  # emptied a "(multiple values)" field without the clear button: leave it alone
        value = parse_field(key, text)
        if common[key] is not MULTIPLE and value == common[key]:
            continue
        changed[key] = value
    return changed
