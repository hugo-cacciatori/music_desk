"""Library search (SPEC §6): seek_lyrics' songs.html rule (any substring of the title, artist, album or lyrics,
ignoring case) over the whole index, plus field filters, the status chips and a folder scope. Plain Python, no Qt.

Free text is matched through the trigram FTS5 `search` table (schema v3), so any substring of 3 or more
characters uses the index; shorter text falls back to LIKE over the same columns.
"""

import functools
import json
import re
import sqlite3
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from music_desk.core.fsops import natural_key
from music_desk.core.index.queries import LISTING_SELECT, FileRow, listing_fields
from music_desk.core.index.store import consistent_read
from music_desk.core.lyrics import FOUND, INSTRUMENTAL, NOT_FOUND, strip_timestamps

# Every column free text searches: the file name, the tags, the tags' lyrics and the LRCLIB plain text.
FREE_COLUMNS = ("name", "title", "artist", "albumartist", "album", "genre", "lyrics", "lrclib")
META_COLUMNS = ("name", "title", "artist", "albumartist", "album", "genre")  # a match here shows no snippet
TEXT_FIELDS = {"artist": ("artist", "albumartist"), "album": ("album",), "genre": ("genre",)}
VALUE_FIELDS = {
    "lyricstag": ("yes", "no", "lrc"),
    "lrclib": ("synced", "plain", "instrumental", "notfound", "pending", "error", "notags"),
    "rg": ("none", "partial", "full"),
}
FIELDS = (*TEXT_FIELDS, "year", "ext", *VALUE_FIELDS)

ALL, WITH_LYRICS, MISSING = "all", "with lyrics", "missing"  # the script's status chips
LIMIT = 5000
FTS_MIN = 3  # trigram needs 3 characters; shorter text is matched with LIKE

_TOKEN = re.compile(r'(?P<field>\b[A-Za-z]+):(?:"(?P<quoted>[^"]*)"?|(?P<bare>[^\s"]*))|"(?P<phrase>[^"]*)"?')


@dataclass(frozen=True)
class SearchQuery:
    """A parsed query. Free text parts and every filter field are ANDed; a field's values are ORed."""

    text: tuple[str, ...] = ()  # substrings: the unquoted remainder as one, plus each "quoted phrase"
    filters: dict[str, tuple[str, ...]] = field(default_factory=dict)
    errors: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.text and not self.filters

    def needles(self, *fields: str) -> list[str]:
        """The folded text to highlight: the free text, plus the values of the named text fields."""
        found = [fold_text(t) for t in self.text]
        for name in fields:
            found += [fold_text(v) for v in self.filters.get(name, ())]
        return [n for n in found if n]


def parse_query(text: str) -> SearchQuery:
    """`artist:x album:"a b" "quoted phrase" free words`. A word before a colon that isn't a field name is plain
    text ("Re: Stacks"). The unquoted remainder, trimmed, is one substring, as the script's search took the whole
    box; a known field with an unknown value is reported in `errors` and ignored."""
    free, phrases, filters, errors = [], [], {}, []
    position = 0
    for match in _TOKEN.finditer(text):
        name = (match["field"] or "").lower()
        if match["field"] and name not in FIELDS:
            continue  # "Re:" stays in the free text
        free.append(text[position:match.start()])
        position = match.end()
        if not match["field"]:
            if match["phrase"].strip():
                phrases.append(match["phrase"])
            continue
        value = match["quoted"] if match["quoted"] is not None else match["bare"]
        if not value.strip():
            continue  # "artist:" while it is being typed
        if name in VALUE_FIELDS:
            value = value.lower()
            if value not in VALUE_FIELDS[name]:
                errors.append(f"{name}:{value} isn't a known filter. Use {', '.join(VALUE_FIELDS[name])}.")
                continue
        elif name == "ext":
            value = "." + value.lower().lstrip(".")
        filters.setdefault(name, []).append(value)
    free.append(text[position:])
    remainder = " ".join(piece.strip() for piece in free if piece.strip())
    parts = ([remainder] if remainder else []) + phrases
    return SearchQuery(tuple(parts), {k: tuple(v) for k, v in filters.items()}, tuple(errors))


def fold(text: str) -> tuple[str, list[int]]:
    """Casefolded with accents removed (as the trigram index sees it), and each folded character's offset in
    `text`, so a match found in the folded text can be highlighted in the original."""
    out, offsets = [], []
    for i, ch in enumerate(text):
        for part in unicodedata.normalize("NFD", ch):
            if unicodedata.combining(part):
                continue
            for c in part.casefold():
                out.append(c)
                offsets.append(i)
    return "".join(out), offsets


@functools.cache
def _no_combining() -> dict[int, None]:
    """A str.translate table removing every combining mark (built once)."""
    return dict.fromkeys(c for c in range(0x110000) if unicodedata.combining(chr(c)))


def fold_text(text: str) -> str:
    """fold() without the offsets, much faster on long texts (lyrics). Line breaks are kept where they were."""
    if text.isascii():
        return text.casefold()
    return unicodedata.normalize("NFD", text).translate(_no_combining()).casefold()


def spans(text: str | None, needles: list[str]) -> tuple[tuple[int, int], ...]:
    """Where the folded needles occur in `text`, as (start, end) offsets into the original, merged and sorted."""
    if not text or not needles:
        return ()
    folded, offsets = fold(text)
    found = []
    for needle in needles:
        start = folded.find(needle)
        while start >= 0:
            found.append((offsets[start], offsets[start + len(needle) - 1] + 1))
            start = folded.find(needle, start + len(needle))
    merged: list[tuple[int, int]] = []
    for start, end in sorted(found):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return tuple(merged)


def _contains(needle: str, text: str | None) -> bool:
    return bool(text) and needle in fold_text(text)


@dataclass(frozen=True)
class SearchResult:
    ids: list[int]  # the first `limit` matches, in natural folder and name order
    total: int  # every match


def _phrase(text: str) -> str:
    return '"' + text.replace('"', '""') + '"'


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# The LRCLIB column's states (queries._lrclib_state), in SQL.
_LRCLIB_STATE = f"""CASE
    WHEN t.read_error IS NOT NULL THEN ''
    WHEN t.lyrics_key IS NULL THEN 'notags'
    WHEN l.status = '{FOUND}' THEN CASE WHEN coalesce(l.synced, '') != '' THEN 'synced' ELSE 'plain' END
    WHEN l.status = '{INSTRUMENTAL}' THEN 'instrumental'
    WHEN l.status = '{NOT_FOUND}' THEN 'notfound'
    ELSE 'pending' END"""
_RG_COUNT = ("((t.rg_track_gain IS NOT NULL) + (t.rg_track_peak IS NOT NULL) + (t.rg_album_gain IS NOT NULL)"
             " + (t.rg_album_peak IS NOT NULL))")
_WITH_LYRICS = (f"(t.read_error IS NULL AND t.lyrics_key IS NOT NULL"
                f" AND coalesce(l.status, '') IN ('{FOUND}', '{INSTRUMENTAL}'))")  # never NULL: NOT must work


def _md_contains(text, needle) -> int:
    return int(text is not None and needle in fold_text(text))


def _value_condition(name: str, value: str, params: list) -> str:
    if name == "year":
        params.append(_like(value)[1:])  # a prefix: "1997" matches "1997-05-21"
        return "t.date LIKE ? ESCAPE '\\'"
    if name == "ext":
        params.append(value)
        return "e.ext = ?"
    if name == "lyricstag":
        return {"yes": "t.has_lyrics_tag = 1", "lrc": "(t.has_lyrics_tag = 0 AND t.has_lrc_sidecar = 1)",
                "no": "(t.has_lyrics_tag = 0 AND t.has_lrc_sidecar = 0)"}[value]
    if name == "rg":
        return {"none": f"{_RG_COUNT} = 0", "full": f"{_RG_COUNT} = 4",
                "partial": f"{_RG_COUNT} BETWEEN 1 AND 3"}[value]
    if name == "lrclib":
        errors = "t.lyrics_key IN (SELECT value FROM json_each(:errors))"
        if value == "error":
            return f"({_LRCLIB_STATE} = 'pending' AND {errors})"
        if value == "pending":
            return f"({_LRCLIB_STATE} = 'pending' AND NOT {errors})"
        params.append(value)
        return f"{_LRCLIB_STATE} = ?"
    raise ValueError(name)


def text_search(conn: sqlite3.Connection, query: SearchQuery, *, scope: int | None = None, chip: str = ALL,
                error_keys: set[str] | frozenset[str] = frozenset(), columns: tuple[str, ...] = FREE_COLUMNS,
                limit: int = LIMIT) -> SearchResult:
    """The tracks matching a query, under a folder when `scope` is given. `error_keys` are the lyrics keys whose
    last lookup failed this session (`lrclib:error`). `columns` narrows the free text (the parity test)."""
    conn.create_function("md_contains", 2, _md_contains, deterministic=True)
    # Atoms are (columns, text); a group ORs its atoms; groups are ANDed.
    groups: list[list[tuple[tuple[str, ...], str]]] = [[(columns, part)] for part in query.text]
    for name, cols in TEXT_FIELDS.items():
        if query.filters.get(name):
            groups.append([(cols, value) for value in query.filters[name]])
    match, where, params = [], [], []
    for group in groups:
        if all(len(text) >= FTS_MIN for _cols, text in group):
            match.append("(" + " OR ".join(f"{{{' '.join(cols)}}} : {_phrase(text)}" for cols, text in group) + ")")
            continue
        alternatives = []
        for cols, text in group:
            if text.isascii():
                alternatives += [f"search.{c} LIKE ? ESCAPE '\\'" for c in cols]
                params += [_like(text)] * len(cols)
            else:
                alternatives += [f"md_contains(search.{c}, ?)" for c in cols]
                params += [fold_text(text)] * len(cols)
        where.append("(" + " OR ".join(alternatives) + ")")
    for name in ("year", "ext", *VALUE_FIELDS):
        values = query.filters.get(name)
        if values:
            where.append("(" + " OR ".join(_value_condition(name, v, params) for v in values) + ")")
    if chip == WITH_LYRICS:
        where.append(_WITH_LYRICS)
    elif chip == MISSING:
        where.append(f"NOT {_WITH_LYRICS}")
    cte = ""
    if scope is not None:
        cte = ("WITH RECURSIVE sub(id) AS (SELECT :scope UNION ALL "
               "SELECT c.id FROM entries c JOIN sub ON c.parent_id = sub.id WHERE c.is_dir = 1) ")
        where.append("e.parent_id IN sub")
    if match:
        where.insert(0, "search MATCH :match")
    sql = (f"{cte}SELECT e.id, e.parent_id, e.name FROM search JOIN entries e ON e.id = search.rowid "
           f"JOIN tracks t ON t.entry_id = e.id LEFT JOIN lyrics l ON l.key = t.lyrics_key"
           + (" WHERE " + " AND ".join(where) if where else ""))
    # Positional and named parameters can't be mixed: number the positional ones.
    named = {"match": " AND ".join(match), "scope": scope, "errors": json.dumps(sorted(error_keys))}
    numbered = iter(range(len(params)))
    sql = re.sub(r"\?", lambda _m: f":p{next(numbered)}", sql)
    named.update({f"p{i}": value for i, value in enumerate(params)})
    with consistent_read(conn):
        rows = conn.execute(sql, named).fetchall()
        folders = _Folders(conn)
        rows.sort(key=lambda r: (folders.key(r[1]), natural_key(r[2])))
    return SearchResult([r[0] for r in rows[:limit]], len(rows))


class _Folders:
    """Folder paths and natural sort keys, built once per folder from its parent's."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.keys: dict[int, tuple] = {}
        self.paths: dict[int, str] = {}

    def _load(self, folder_id: int) -> None:
        chain = []
        current = folder_id
        while current is not None and current not in self.keys:
            row = self.conn.execute("SELECT parent_id, name FROM entries WHERE id = ?", (current,)).fetchone()
            if row is None:
                break
            chain.append((current, *row))
            current = row[0]
        for entry_id, parent_id, name in reversed(chain):
            if parent_id is None or parent_id not in self.keys:
                self.keys[entry_id], self.paths[entry_id] = (natural_key(name),), name
            else:
                self.keys[entry_id] = (*self.keys[parent_id], natural_key(name))
                self.paths[entry_id] = str(Path(self.paths[parent_id], name))
        self.keys.setdefault(folder_id, ())
        self.paths.setdefault(folder_id, "")

    def key(self, folder_id: int) -> tuple:
        if folder_id not in self.keys:
            self._load(folder_id)
        return self.keys[folder_id]

    def path(self, folder_id: int) -> str:
        if folder_id not in self.paths:
            self._load(folder_id)
        return self.paths[folder_id]


@dataclass(frozen=True)
class SearchRow(FileRow):
    """A search result: a file list row, plus its folder and what to highlight."""

    folder_id: int = 0
    folder: str = ""
    albumartist: str | None = None
    snippet: str = ""  # the first matching lyric line, when only the lyrics match
    title_spans: tuple[tuple[int, int], ...] = ()
    artist_spans: tuple[tuple[int, int], ...] = ()
    snippet_spans: tuple[tuple[int, int], ...] = ()


def snippet(texts: list[str | None], needles: list[str]) -> str:
    """The first line, in the LRCLIB text and then the tags' lyrics, that contains a needle (folded)."""
    for text in texts:
        if not text:
            continue
        text = strip_timestamps(text)
        folded = fold_text(text)
        if not any(n in folded for n in needles):
            continue
        for line, folded_line in zip(text.splitlines(), folded.splitlines(), strict=False):
            if line.strip() and any(n in folded_line for n in needles):
                return line.strip()
    return ""


def result_rows(conn: sqlite3.Connection, ids: list[int], query: SearchQuery,
                cancelled: Callable[[], bool] | None = None) -> list[SearchRow]:
    """The shown results in `ids` order, with their folder, highlights and snippet. Ids gone since are dropped.
    `cancelled` is checked now and then (a newer query): the rows built so far are returned."""
    if not ids:
        return []
    with consistent_read(conn):
        rows = {r[0]: r for r in conn.execute(
            f"{LISTING_SELECT} WHERE e.id IN (SELECT value FROM json_each(?))", (json.dumps(ids),))}
        extra = {r[0]: r[1:] for r in conn.execute(
            "SELECT e.id, e.parent_id, s.albumartist, s.name, s.genre, s.lyrics, s.lrclib FROM entries e "
            "JOIN search s ON s.rowid = e.id WHERE e.id IN (SELECT value FROM json_each(?))", (json.dumps(ids),))}
        folders = _Folders(conn)
        free = query.needles()
        title_needles, artist_needles = query.needles(), query.needles("artist")
        results = []
        for n, entry_id in enumerate(ids):
            if cancelled is not None and n % 200 == 0 and cancelled():
                break
            if entry_id not in rows or entry_id not in extra:
                continue
            fields = listing_fields(rows[entry_id])
            parent_id, albumartist, name, genre, tag_lyrics, lrclib = extra[entry_id]
            meta = (name, fields["title"], fields["artist"], albumartist, fields["album"], genre)
            lyric_only = [n for n in free if not any(_contains(n, text) for text in meta)]
            line = snippet([lrclib, tag_lyrics], lyric_only) if lyric_only else ""
            results.append(SearchRow(
                **fields, folder_id=parent_id, folder=folders.path(parent_id), albumartist=albumartist,
                snippet=line, title_spans=spans(fields["title"], title_needles),
                artist_spans=spans(fields["artist"], artist_needles), snippet_spans=spans(line, lyric_only)))
    return results
