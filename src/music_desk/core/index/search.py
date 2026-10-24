"""Library search (SPEC §6): seek_lyrics' songs.html rule (any substring of the title, artist, album or lyrics,
ignoring case) over the whole index, plus field filters. Folders and audio files are found by name as soon as the
folder walk lists them, before their tags are read. Plain Python, no Qt.

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

from music_desk.core.formats import AUDIO_EXTENSIONS
from music_desk.core.fsops import natural_key
from music_desk.core.index.queries import LISTING_SELECT, FileRow, listing_fields
from music_desk.core.index.store import consistent_read
from music_desk.core.lyrics import FOUND, INSTRUMENTAL, NOT_FOUND, strip_timestamps

# The two searches (SPEC §6, §33): File search's free text matches the file and folder names, the tags and the
# date; Lyrics search's only the LRCLIB plain text. The files' own lyrics tags aren't searched (M12, SPEC §26).
FILES, LYRICS = "files", "lyrics"
MODES = (FILES, LYRICS)
DATE = "date"  # tracks.date, not a column of the search table: matched with LIKE, and only by text with a digit
FILE_COLUMNS = ("name", "title", "artist", "albumartist", "album", DATE)
LYRICS_COLUMNS = ("lrclib",)
MODE_COLUMNS = {FILES: FILE_COLUMNS, LYRICS: LYRICS_COLUMNS}
TEXT_FIELDS = {"artist": ("artist", "albumartist"), "album": ("album",), "genre": ("genre",)}
VALUE_FIELDS = {
    "lrclib": ("synced", "plain", "instrumental", "notfound", "pending", "error", "notags"),
    "rg": ("none", "partial", "full"),
}
FIELDS = (*TEXT_FIELDS, "year", "ext", *VALUE_FIELDS)

TAG_FILTERS = ("artist", "album", "genre", "year", *VALUE_FIELDS)  # only audio files whose tags are read
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
# The results: folders (not a root's top entry, whose name is the root path) and audio files, read or not.
_RESULTS = (f"((e.is_dir = 1 AND e.parent_id IS NOT NULL) OR e.ext IN "
            f"({', '.join(repr(ext) for ext in sorted(AUDIO_EXTENSIONS))}))")


def _md_contains(text, needle) -> int:
    return int(text is not None and needle in fold_text(text))


def _value_condition(name: str, value: str, params: list) -> str:
    if name == "year":
        params.append(_like(value)[1:])  # a prefix: "1997" matches "1997-05-21"
        return "t.date LIKE ? ESCAPE '\\'"
    if name == "ext":
        params.append(value)
        return "e.ext = ?"
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


def text_search(conn: sqlite3.Connection, query: SearchQuery, *,
                error_keys: set[str] | frozenset[str] = frozenset(), mode: str = FILES,
                columns: tuple[str, ...] | None = None, limit: int = LIMIT) -> SearchResult:
    """The folders and audio files matching a query. Their names are searchable from the folder walk on, their
    tags and lyrics once read: `tracks` is a LEFT JOIN, and a filter on tags limits the results to read files.
    `error_keys` are the lyrics keys whose last lookup failed this session (`lrclib:error`). `mode` picks the
    free text's columns (File or Lyrics search); `columns` narrows them instead (the parity test)."""
    conn.create_function("md_contains", 2, _md_contains, deterministic=True)
    columns = columns if columns is not None else MODE_COLUMNS[mode]
    # Atoms are (columns, text); a group ORs its atoms; groups are ANDed.
    groups: list[list[tuple[tuple[str, ...], str]]] = [[(columns, part)] for part in query.text]
    for name, cols in TEXT_FIELDS.items():
        if query.filters.get(name):
            groups.append([(cols, value) for value in query.filters[name]])
    match, where, params = [], [], []
    for group in groups:
        # The date can't be in the MATCH: a group that may match it ("1997") ORs a MATCH subquery with a LIKE.
        dated = [(cols, text) for cols, text in group if DATE in cols and any(c.isdigit() for c in text)]
        group = [(tuple(c for c in cols if c != DATE), text) for cols, text in group]
        if not dated and all(len(text) >= FTS_MIN for _cols, text in group):
            match.append("(" + " OR ".join(f"{{{' '.join(cols)}}} : {_phrase(text)}" for cols, text in group) + ")")
            continue
        alternatives = []
        for cols, text in group:
            if not cols:
                continue
            if len(text) >= FTS_MIN:
                alternatives.append("search.rowid IN (SELECT rowid FROM search WHERE search MATCH ?)")
                params.append(f"{{{' '.join(cols)}}} : {_phrase(text)}")
            elif text.isascii():
                alternatives += [f"search.{c} LIKE ? ESCAPE '\\'" for c in cols]
                params += [_like(text)] * len(cols)
            else:
                alternatives += [f"md_contains(search.{c}, ?)" for c in cols]
                params += [fold_text(text)] * len(cols)
        for _cols, text in dated:
            alternatives.append("t.date LIKE ? ESCAPE '\\'")
            params.append(_like(text))
        where.append("(" + " OR ".join(alternatives or ["0"]) + ")")
    for name in ("year", "ext", *VALUE_FIELDS):
        values = query.filters.get(name)
        if values:
            where.append("(" + " OR ".join(_value_condition(name, v, params) for v in values) + ")")
    if any(query.filters.get(name) for name in TAG_FILTERS):
        where.append("t.entry_id IS NOT NULL")  # rg:none must not take a folder's missing tags for "none"
    where.append(_RESULTS)
    if match:
        where.insert(0, "search MATCH :match")
    sql = ("SELECT e.id, e.parent_id, e.name FROM search JOIN entries e ON e.id = search.rowid "
           "LEFT JOIN tracks t ON t.entry_id = e.id LEFT JOIN lyrics l ON l.key = t.lyrics_key"
           " WHERE " + " AND ".join(where))
    # Positional and named parameters can't be mixed: number the positional ones.
    named = {"match": " AND ".join(match), "errors": json.dumps(sorted(error_keys))}
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
    """A search result (a folder or an audio file): a file list row, plus its parent folder and what to
    highlight."""

    folder_id: int = 0
    folder: str = ""
    albumartist: str | None = None
    snippet: str = ""  # the first matching lyric line, when only the lyrics match
    title_spans: tuple[tuple[int, int], ...] = ()
    artist_spans: tuple[tuple[int, int], ...] = ()
    snippet_spans: tuple[tuple[int, int], ...] = ()


def snippet(texts: list[str | None], needles: list[str]) -> str:
    """The first line of these texts (the LRCLIB lyrics) that contains a needle (folded)."""
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
                cancelled: Callable[[], bool] | None = None, *, mode: str = FILES) -> list[SearchRow]:
    """The shown results in `ids` order, with their folder, highlights and snippet. Ids gone since are dropped.
    `cancelled` is checked now and then (a newer query): the rows built so far are returned. File search
    highlights the free text in the title and artist; Lyrics search, in the snippet only."""
    if not ids:
        return []
    with consistent_read(conn):
        rows = {r[0]: r for r in conn.execute(
            f"{LISTING_SELECT} WHERE e.id IN (SELECT value FROM json_each(?))", (json.dumps(ids),))}
        extra = {r[0]: r[1:] for r in conn.execute(
            "SELECT e.id, e.parent_id, s.albumartist, s.lrclib FROM entries e "
            "JOIN search s ON s.rowid = e.id WHERE e.id IN (SELECT value FROM json_each(?))", (json.dumps(ids),))}
        folders = _Folders(conn)
        lyrics = mode == LYRICS
        free = query.needles()
        title_needles = [] if lyrics else free
        artist_values = [fold_text(v) for v in query.filters.get("artist", ())]
        artist_needles = [n for n in artist_values if n] if lyrics else query.needles("artist")
        results = []
        for n, entry_id in enumerate(ids):
            if cancelled is not None and n % 200 == 0 and cancelled():
                break
            if entry_id not in rows or entry_id not in extra:
                continue
            fields = listing_fields(rows[entry_id])
            if fields["is_dir"]:
                fields.update(n_tracks=None, mtime=None)  # a folder row: its name and parent, nothing else
            parent_id, albumartist, lrclib = extra[entry_id]
            line = snippet([lrclib], free) if lyrics and free else ""
            results.append(SearchRow(
                **fields, folder_id=parent_id, folder=folders.path(parent_id), albumartist=albumartist,
                snippet=line, title_spans=spans(fields["title"], title_needles),
                artist_spans=spans(fields["artist"], artist_needles), snippet_spans=spans(line, free)))
    return results
