"""File name masks (SPEC §21): how the app names a music file from its tags, wherever it does (the Rename tool,
Tidy, the rename hint after a tag save, the cue split). Moved out of core/rename.py unchanged, so the rename
parity tests still hold.

A mask is a Python format string over the rename fields; the first mask whose fields are all present names the
file, with the characters Windows refuses in file names replaced.
"""

import re
import string
from collections.abc import Iterable

from refrain.core.tags import NUMBER_FIELDS, RENAME_TEXT_FIELDS

DEFAULT_MASKS: tuple[str, ...] = ("{track:02} {title}", "{title}")
DEFAULT_REPLACEMENT = "-"
FIELDS: tuple[str, ...] = (*NUMBER_FIELDS, *RENAME_TEXT_FIELDS)
FORBIDDEN_CHARACTERS = re.compile(r"[/\\?%*:|\"<>\x7F\x00-\x1F]")


def mask_fields(mask: str) -> list[str]:
    """List the field names used by a mask."""
    return [name for _, name, _, _ in string.Formatter().parse(mask) if name is not None]


def validate_masks(masks: Iterable[str], replacement: str) -> list[str]:
    """The problems in the masks and the replacement (empty if they are fine): the mask half of the script's
    validate_config."""
    masks = list(masks)
    problems = []
    if not masks:
        problems.append("No rename mask")

    sample = {name: 1 for name in NUMBER_FIELDS} | {name: "x" for name in RENAME_TEXT_FIELDS}
    for mask in masks:
        try:
            literal_text = "".join(text for text, _, _, _ in string.Formatter().parse(mask))
            unknown = [name for name in mask_fields(mask) if name not in sample]
        except ValueError as e:
            problems.append(f"mask {mask!r}: {e}")
            continue
        if unknown:
            problems.append(f"mask {mask!r}: unknown field(s) {', '.join(unknown)}")
            continue
        if not mask_fields(mask):
            problems.append(f"mask {mask!r}: uses no field")
        if FORBIDDEN_CHARACTERS.search(literal_text):
            problems.append(f"mask {mask!r}: contains a character not allowed in file names")
        try:
            mask.format(**sample)
        except (ValueError, TypeError, IndexError, KeyError) as e:
            problems.append(f"mask {mask!r}: invalid format ({e})")

    if FORBIDDEN_CHARACTERS.search(replacement):
        problems.append("The replacement contains a character not allowed in file names")
    return problems


def sanitize(text: str, replacement: str = DEFAULT_REPLACEMENT) -> str:
    """Replace the characters that are not allowed in file names."""
    return FORBIDDEN_CHARACTERS.sub(replacement, text)


def mask_values(rename_fields: dict, replacement: str) -> dict:
    """The script's read_fields: numbers as they are, text sanitized."""
    return {name: sanitize(value, replacement) if isinstance(value, str) else value
            for name, value in rename_fields.items()}


def choose_name(fields: dict, masks: Iterable[str]) -> tuple[str, int] | None:
    """(new_stem, mask_index) for the first mask whose fields are all present, or None."""
    for index, mask in enumerate(masks):
        if all(name in fields for name in mask_fields(mask)):
            return mask.format(**fields), index
    return None


def missing_from(mask: str, fields: dict) -> str:
    return ", ".join(name for name in mask_fields(mask) if name not in fields)


def name_for(rename_fields: dict, masks: Iterable[str], replacement: str, extension: str) -> str | None:
    """The full file name these fields get (the extension lowercased), or None when no mask fits."""
    choice = choose_name(mask_values(rename_fields, replacement), masks)
    return None if choice is None else f"{choice[0]}{extension.lower()}"
