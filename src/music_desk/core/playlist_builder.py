"""The playlist builder's model (SPEC §15): one playlist in progress, as an ordered list of tracks. Plain Python,
no Qt.

Each item is the track's index entry id and its path. The id keeps it valid through in-app renames; the path
finds it again if the index is rebuilt, and lets a file that left the library stay listed (dimmed) instead of
vanishing. The playlist is saved in the app's data and restored at start, until it is cleared.
"""

import json
import logging
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

FORMAT_VERSION = 1


@dataclass(frozen=True)
class Item:
    entry_id: int | None  # None: the file isn't in the index (any more)
    path: str

    @property
    def key(self) -> object:
        """What makes two items the same track (a song isn't added twice)."""
        return self.entry_id if self.entry_id is not None else os.path.normcase(self.path)


class Builder:
    def __init__(self, items: Iterable[Item] = ()) -> None:
        self.items: list[Item] = list(items)

    def __len__(self) -> int:
        return len(self.items)

    @property
    def ids(self) -> list[int | None]:
        return [item.entry_id for item in self.items]

    def add(self, items: Iterable[Item], position: int | None = None) -> tuple[int, int]:
        """Insert at `position` (the end when None), in the order given. Tracks already in the playlist, or
        repeated in `items`, are skipped. Returns (added, skipped)."""
        items = list(items)
        present = {item.key for item in self.items}
        new = []
        for item in items:
            if item.key not in present:
                present.add(item.key)
                new.append(item)
        at = len(self.items) if position is None else max(0, min(position, len(self.items)))
        self.items[at:at] = new
        return len(new), len(items) - len(new)

    def remove(self, rows: Iterable[int]) -> None:
        for row in sorted(set(rows), reverse=True):
            if 0 <= row < len(self.items):
                del self.items[row]

    def clear(self) -> None:
        self.items.clear()

    def move_rows(self, rows: Iterable[int], delta: int) -> list[int]:
        """Ctrl+Up / Ctrl+Down: every selected row moves one step (`delta` is -1 or 1), keeping their order; when
        the selection already touches that edge, nothing moves. Returns the rows' new positions."""
        rows = sorted({r for r in rows if 0 <= r < len(self.items)})
        if not rows or delta not in (-1, 1):
            return rows
        if (delta < 0 and rows[0] == 0) or (delta > 0 and rows[-1] == len(self.items) - 1):
            return rows
        for r in (rows if delta < 0 else reversed(rows)):
            self.items[r], self.items[r + delta] = self.items[r + delta], self.items[r]
        return [r + delta for r in rows]

    def move_to(self, rows: Iterable[int], position: int) -> list[int]:
        """A drag inside the list: the rows go before the row at `position` (the end when past it), in order.
        Returns their new positions."""
        rows = sorted({r for r in rows if 0 <= r < len(self.items)})
        if not rows:
            return []
        chosen = [self.items[r] for r in rows]
        position = max(0, min(position, len(self.items)))
        target = position - sum(1 for r in rows if r < position)  # rows above the drop point leave a gap
        rest = [item for r, item in enumerate(self.items) if r not in set(rows)]
        self.items = rest[:target] + chosen + rest[target:]
        return list(range(target, target + len(chosen)))

    def sort(self, key: Callable[[Item], Any], reverse: bool = False) -> None:
        """A header click: the playlist's order itself changes (it is what gets exported). Stable."""
        self.items.sort(key=key, reverse=reverse)

    def resolve(self, lookup: Callable[[Item], Item]) -> None:
        """Bring every item up to date with the index (a rename moved it, a rebuild changed its id)."""
        self.items = [lookup(item) for item in self.items]

    # -- persistence --

    def to_json(self) -> dict:
        return {"version": FORMAT_VERSION, "items": [{"id": i.entry_id, "path": i.path} for i in self.items]}

    @classmethod
    def from_json(cls, data) -> "Builder":
        """A saved playlist; anything malformed is dropped, never an error (an old or hand-edited file)."""
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            return cls()
        items = []
        for raw in data["items"]:
            if not isinstance(raw, dict) or not isinstance(raw.get("path"), str):
                continue
            entry_id = raw.get("id")
            items.append(Item(entry_id if isinstance(entry_id, int) and not isinstance(entry_id, bool) else None,
                              raw["path"]))
        return cls(items)

    def save(self, path: Path) -> None:
        """Atomic: a temporary file replaces the old one, so a crash can't leave half a playlist."""
        temp = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp.write_text(json.dumps(self.to_json(), ensure_ascii=False), encoding="utf-8")
            os.replace(temp, path)
        except OSError as e:
            log.warning("could not save the playlist to %s: %s", path, e)

    @classmethod
    def load(cls, path: Path) -> "Builder":
        try:
            return cls.from_json(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as e:
            log.warning("could not read the playlist %s (%s): starting empty", path, e)
            return cls()
