"""Removing ReplayGain (SPEC §10 B, Hugo 2026-10-16): the REPLAYGAIN_* values and the LUFS comment lines."""

import re
from pathlib import Path

from refrain.core.replaygain.options import DEFAULT_OPTIONS, ReplayGainOptions
from refrain.core.tags import TaggedFile

_NUMBER = r"[-+]?\d+(?:[.,]\d+)?"


def lufs_comment_pattern(template: str) -> re.Pattern:
    """A comment line the ReplayGain step writes, for any target: the template ("LUFS Target : {target}") with
    {target} as a number. A template without {target} matches only itself."""
    body = _NUMBER.join(re.escape(part) for part in template.strip().split("{target}"))
    return re.compile(r"\s*" + body + r"\s*")


def remove_replaygain(path: str | Path, options: ReplayGainOptions = DEFAULT_OPTIONS) -> bool:
    """Remove a file's ReplayGain tags: every REPLAYGAIN_* value, and the comment lines the ReplayGain step wrote
    (the card's comment template, any target). Other comment lines stay. Saves only when something changed;
    returns whether it did. Raises the file's errors (unreadable, no tags, locked...) to the caller."""
    tagged = TaggedFile(path)
    if not tagged.info.tags:
        return False  # raw AAC: nothing to remove, nothing can be written
    changed = tagged.remove_replaygain() > 0
    comment = tagged.fields().comment
    if comment:
        pattern = lufs_comment_pattern(options.comment)
        lines = comment.splitlines()
        kept = [line for line in lines if not pattern.fullmatch(line)]
        if len(kept) != len(lines):
            tagged.set_fields({"comment": "\n".join(kept).strip() or None})
            changed = True
    if changed:
        tagged.save()
    return changed
