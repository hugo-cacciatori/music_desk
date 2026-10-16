"""The editable cue sheet of the Split page (SPEC §22, "Saving the sheet"). No Qt.

The document keeps every line of the file in order, with its own text and line ending. An edit rewrites, adds or
removes only the lines it concerns; every other line is written back exactly as it was read, so a sheet saved
unedited is byte-identical to the original. Bytes the encoding can't decode survive too (surrogateescape).

Times, track numbers and values are parsed from the lines each time they are asked for: the lines are the only
state, as the .cue is the split's only source of truth.
"""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from music_desk.core import fsops
from music_desk.core.cue import (
    ENCODINGS,
    FRAMES_PER_SECOND,
    CueError,
    CueSheet,
    SourceInfo,
    _file_name,
    _value,
    frames_from_time,
    parse_cue,
    samples_from_frames,
)

_NEWLINE = re.compile(r"\r\n|\r|\n")
_UNDECODED = re.compile("[\udc80-\udcff]")
_BOMS = ((b"\xef\xbb\xbf", "utf-8", "UTF-8 (BOM)"), (b"\xff\xfe", "utf-16-le", "UTF-16 LE"),
         (b"\xfe\xff", "utf-16-be", "UTF-16 BE"))
UTF8_BOM = b"\xef\xbb\xbf"
PARTIAL = ".partial"

# field -> (tag, quoted). A tag is a line's keyword, with REM's second word and INDEX's number.
ALBUM_FIELDS: dict[str, tuple[str, bool]] = {
    "performer": ("PERFORMER", True), "title": ("TITLE", True), "genre": ("REM GENRE", True),
    "date": ("REM DATE", False), "disc": ("REM DISCNUMBER", False), "disc_total": ("REM TOTALDISCS", False),
    "catalog": ("CATALOG", False), "songwriter": ("SONGWRITER", True),
}
TRACK_FIELDS: dict[str, tuple[str, bool]] = {
    "title": ("TITLE", True), "performer": ("PERFORMER", True), "songwriter": ("SONGWRITER", True),
    "isrc": ("ISRC", False), "flags": ("FLAGS", False), "gap": ("INDEX 00", False), "start": ("INDEX 01", False),
}
_TRACK_ORDER = [tag for tag, _quoted in TRACK_FIELDS.values()]  # as EAC writes them
_ALBUM_TAGS = {tag for tag, _quoted in ALBUM_FIELDS.values()}
_TRACK_TAGS = set(_TRACK_ORDER)
TIME_FIELDS = ("gap", "start")


class CueEncodingError(ValueError):
    """Characters the sheet's encoding can't hold. Save asks to write UTF-8 with a BOM instead (SPEC §22)."""

    def __init__(self, encoding: str, characters: list[str]) -> None:
        self.encoding, self.characters = encoding, characters
        super().__init__(f"{encoding} can't hold {' '.join(characters)}")


@dataclass
class Line:
    text: str  # without its line ending
    end: str = ""  # "\r\n", "\n", "\r", or "" for a last line without one

    @property
    def tag(self) -> str:
        return _tag(self.text)

    @property
    def indent(self) -> str:
        return self.text[:len(self.text) - len(self.text.lstrip())]


def _tag(text: str) -> str:
    words = text.split(None, 2)
    if not words:
        return ""
    keyword = words[0].upper()
    if keyword == "REM" and len(words) > 1:
        return "REM " + words[1].upper()
    if keyword == "INDEX" and len(words) > 1 and words[1].isdigit():
        return f"INDEX {int(words[1]):02d}"
    return keyword


def _shown(text: str) -> str:
    """Bytes the encoding couldn't decode (kept as surrogates, to be written back as they were) show as �."""
    return _UNDECODED.sub("\ufffd", text)


def _line_value(text: str, tag: str) -> str:
    """The value after a line's tag: between its first and last quotes (a quote inside is then visible to
    validate()), or the rest of the line."""
    words = text.split(None, len(tag.split()))
    if len(words) <= len(tag.split()):
        return ""
    rest = words[-1].strip()
    value = rest[1:-1] if len(rest) > 1 and rest[0] == rest[-1] == '"' else _value(rest)
    return _shown(value)


def format_time(frames: int) -> str:
    """CD frames -> 'mm:ss:ff', two digits each (minutes may have more)."""
    seconds, ff = divmod(frames, FRAMES_PER_SECOND)
    return f"{seconds // 60:02d}:{seconds % 60:02d}:{ff:02d}"


@dataclass
class TrackRegion:
    start: int  # the TRACK line
    end: int  # one past its last line (the next TRACK or FILE line)
    audio: bool


@dataclass(frozen=True)
class TrackView:
    """One audio track as the page's table shows it: text values, as typed or read."""

    row: int
    number: int | None
    values: dict[str, str]  # TRACK_FIELDS' keys
    other: str  # its lines with no column of their own


@dataclass
class CueDocument:
    lines: list[Line]
    codec: str  # Python's codec, to write it back
    encoding: str  # its name, as the page shows it
    bom: bytes = b""
    fell_back: bool = False  # Auto had to use Windows-1252: the titles may be garbled
    path: Path | None = None
    saved: bytes | None = None  # the bytes on disk, to tell unsaved edits

    # -- reading --

    @property
    def newline(self) -> str:
        """The file's own line ending, for new lines: its most common one (CRLF for a file without any)."""
        ends = [line.end for line in self.lines if line.end]
        return max(("\r\n", "\n", "\r"), key=ends.count) if ends else "\r\n"

    def text(self) -> str:
        return "".join(line.text + line.end for line in self.lines)

    def _structure(self) -> tuple[int, list[int], list[TrackRegion]]:
        """(the end of the album lines, the FILE lines, every TRACK's lines)."""
        header_end, files, tracks = len(self.lines), [], []
        for i, line in enumerate(self.lines):
            tag = line.tag
            if tag in ("FILE", "TRACK"):
                header_end = min(header_end, i)
                if tracks and tracks[-1].end == len(self.lines):
                    tracks[-1].end = i
                if tag == "FILE":
                    files.append(i)
                else:
                    words = line.text.split()
                    tracks.append(TrackRegion(i, len(self.lines), len(words) > 2 and words[2].upper() == "AUDIO"))
        return header_end, files, tracks

    def _audio(self) -> list[TrackRegion]:
        return [region for region in self._structure()[2] if region.audio]

    @property
    def file(self) -> str:
        """The first FILE line's name."""
        files = self._structure()[1]
        if not files:
            return ""
        return _shown(_file_name(self.lines[files[0]].text.strip()[4:]))

    @property
    def file_count(self) -> int:
        return len(self._structure()[1])

    def album(self) -> dict[str, str]:
        """The album fields as text, and "other": the album lines with no field of their own."""
        header_end = self._structure()[0]
        values = {name: self._value_in(0, header_end, tag) for name, (tag, _q) in ALBUM_FIELDS.items()}
        values["other"] = "\n".join(_shown(line.text) for line in self.lines[:header_end]
                                    if line.tag not in _ALBUM_TAGS)
        return values

    def tracks(self) -> list[TrackView]:
        views = []
        for row, region in enumerate(self._audio()):
            words = self.lines[region.start].text.split()
            number = int(words[1]) if len(words) > 1 and words[1].isdigit() else None
            values = {name: self._value_in(region.start + 1, region.end, tag)
                      for name, (tag, _q) in TRACK_FIELDS.items()}
            other = "\n".join(_shown(line.text) for line in self.lines[region.start + 1:region.end]
                              if line.tag not in _TRACK_TAGS)
            views.append(TrackView(row, number, values, other))
        return views

    def _value_in(self, start: int, end: int, tag: str) -> str:
        for line in self.lines[start:end]:
            if line.tag == tag:
                return _line_value(line.text, tag)
        return ""

    def sheet(self) -> CueSheet:
        """The parsed sheet, for planning. Raises CueError (validate() says where)."""
        sheet = parse_cue(_shown(self.text()), self.encoding, self.path)
        sheet.fell_back = self.fell_back
        return sheet

    @property
    def modified(self) -> bool:
        try:
            return format_cue(self) != self.saved
        except CueEncodingError:
            return True

    # -- edits --

    def set_file(self, name: str) -> None:
        """The first FILE line's name; its type word (WAVE, AIFF) stays."""
        files = self._structure()[1]
        if not files:
            self._insert(self._structure()[0], f'FILE "{name}" WAVE')
            return
        line = self.lines[files[0]]
        rest = line.text.strip()[4:].strip()
        kind = rest.rsplit(None, 1)[-1] if rest.endswith(("WAVE", "AIFF", "BINARY", "MP3", "MOTOROLA")) else "WAVE"
        text = f'{line.indent}FILE "{name}" {kind}'
        if text != line.text and name != self.file:
            line.text = text

    def set_album(self, name: str, value: str) -> None:
        tag, quoted = ALBUM_FIELDS[name]
        header_end = self._structure()[0]
        if tag.startswith("REM "):
            rems = [i for i in range(header_end) if self.lines[i].tag.startswith("REM ")]
            position = rems[-1] + 1 if rems else 0
        else:
            position = header_end
        self._set(0, header_end, tag, quoted, value.strip(), position, "")

    def set_track(self, row: int, name: str, value: str) -> None:
        tag, quoted = TRACK_FIELDS[name]
        region = self._audio()[row]
        value = value.strip()
        if name in TIME_FIELDS and value:
            try:
                value = format_time(frames_from_time(value))
            except CueError:
                pass  # kept as typed: validate() marks it
        order = _TRACK_ORDER.index(tag)
        position = region.start + 1  # after the last field EAC writes before this one
        for i in range(region.start + 1, region.end):
            other = self.lines[i].tag
            if other in _TRACK_TAGS and _TRACK_ORDER.index(other) < order:
                position = i + 1
        self._set(region.start + 1, region.end, tag, quoted, value, position, self._content_indent(region))

    def _set(self, start: int, end: int, tag: str, quoted: bool, value: str, position: int, indent: str) -> None:
        """Rewrite the first `tag` line of [start, end) (dropping repeats), add one, or remove them when empty."""
        found = [i for i in range(start, end) if self.lines[i].tag == tag]
        if found and _line_value(self.lines[found[0]].text, tag) == value and len(found) == 1:
            return  # unchanged: the line keeps its bytes
        for i in reversed(found[1:]):
            del self.lines[i]
        if not value:
            if found:
                del self.lines[found[0]]
            return
        text = f'{tag} "{value}"' if quoted else f"{tag} {value}"
        if found:
            self.lines[found[0]].text = self.lines[found[0]].indent + text
        else:
            self._insert(position, indent + text)

    def set_album_other(self, text: str) -> None:
        header_end = self._structure()[0]
        others = [i for i in range(header_end) if self.lines[i].tag not in _ALBUM_TAGS]
        self._replace_block(others, text, others[0] if others else 0, "")

    def set_track_other(self, row: int, text: str) -> None:
        region = self._audio()[row]
        others = [i for i in range(region.start + 1, region.end) if self.lines[i].tag not in _TRACK_TAGS]
        self._replace_block(others, text, others[0] if others else region.end, self._content_indent(region))

    def _replace_block(self, indexes: list[int], text: str, position: int, indent: str) -> None:
        new = text.split("\n") if text.strip() else []
        new = [line.rstrip("\r") for line in new]
        if new == [_shown(self.lines[i].text) for i in indexes]:
            return
        for i in reversed(indexes):
            del self.lines[i]
            if i < position:
                position -= 1
        for offset, line in enumerate(new):
            self._insert(position + offset, line if line[:1].isspace() or not indent else indent + line.lstrip())

    def add_track(self, after: int | None, audio_frames: int | None = None) -> int:
        """Insert a track after row `after`, starting halfway through it (to the next start, or `audio_frames`
        for the last track). Returns the new row. Raises CueError when that track has no room or no valid time."""
        regions = self._audio()
        if after is None or not regions:
            all_regions = self._structure()[2]
            position = all_regions[-1].end if all_regions else len(self.lines)
            start, indent = 0, "  "
            row = len(regions)
        else:
            views = self.tracks()
            try:
                begin = frames_from_time(views[after].values["start"])
                if after + 1 < len(views):
                    end = frames_from_time(views[after + 1].values["start"])
                elif audio_frames is not None:
                    end = audio_frames
                else:
                    raise CueError("the end of the audio isn't known")
            except CueError as e:
                raise CueError(f"A track can't be added after track {after + 1}: {e}") from None
            if end - begin < 2:
                raise CueError(f"Track {after + 1} is too short to add a track inside it.")
            start = (begin + end) // 2
            position = regions[after].end
            indent = self.lines[regions[after].start].indent
            row = after + 1
        content = indent + "  " if indent else "    "
        self._insert(position, f"{indent}TRACK 00 AUDIO")
        self._insert(position + 1, f"{content}INDEX 01 {format_time(start)}")
        self._renumber()
        return row

    def delete_tracks(self, rows: Iterable[int]) -> None:
        """Remove tracks. Their audio joins the track before (gaps go to the previous track), or, for track 1,
        the next one kept, which takes its start."""
        rows = sorted(set(rows))
        views = self.tracks()
        if rows and rows[0] == 0:
            keep = next((v.row for v in views if v.row not in rows), None)
            if keep is not None and views[0].values["start"]:
                self.set_track(keep, "start", views[0].values["start"])
                self.set_track(keep, "gap", "")  # it would come after the new start
        regions = self._audio()
        for row in reversed(rows):
            del self.lines[regions[row].start:regions[row].end]
        self._renumber()

    def _renumber(self) -> None:
        """TRACK lines numbered 1…N in order; only lines whose number changes are rewritten."""
        for number, region in enumerate(self._structure()[2], 1):
            line = self.lines[region.start]
            words = line.text.split()
            if len(words) > 1 and words[1].isdigit() and int(words[1]) == number:
                continue
            line.text = f"{line.indent}TRACK {number:02d} {' '.join(words[2:])}".rstrip()

    def _content_indent(self, region: TrackRegion) -> str:
        for line in self.lines[region.start + 1:region.end]:
            if line.text.strip():
                return line.indent
        return self.lines[region.start].indent + "  "

    def _insert(self, index: int, text: str) -> None:
        line = Line(text, self.newline)
        if index >= len(self.lines) and self.lines and not self.lines[-1].end:
            self.lines[-1].end, line.end = self.newline, ""  # the file had no final line ending: keep it so
        self.lines.insert(index, line)

    def use_utf8_bom(self) -> None:
        """Write UTF-8 with a BOM from now on (a character didn't fit the sheet's encoding)."""
        self.codec, self.bom, self.encoding, self.fell_back = "utf-8", UTF8_BOM, "UTF-8 (BOM)", False


# ---------------------------------------------------------------------------
# Reading and writing
# ---------------------------------------------------------------------------

def parse_document(data: bytes, encoding: str | None = None, path: Path | None = None) -> CueDocument:
    """`encoding` None is Auto: a BOM, then UTF-8, then Windows-1252 (as the file preview)."""
    bom, codec, label, fell_back = b"", None, None, False
    for mark, bom_codec, name in _BOMS:
        if data.startswith(mark) and (encoding is None or (encoding == "utf-8" and mark == UTF8_BOM)):
            bom, codec, label = mark, bom_codec, name
            break
    if codec is None and encoding is None:
        try:
            data.decode("utf-8")
            codec, label = "utf-8", "UTF-8"
        except UnicodeDecodeError:
            codec, label, fell_back = "cp1252", "Windows-1252", True
    elif codec is None:
        codec, label = encoding, dict(ENCODINGS).get(encoding, encoding)
    text = data[len(bom):].decode(codec, errors="surrogateescape")
    lines, position = [], 0
    for match in _NEWLINE.finditer(text):
        lines.append(Line(text[position:match.start()], match.group()))
        position = match.end()
    if position < len(text):
        lines.append(Line(text[position:]))
    return CueDocument(lines, codec, label, bom, fell_back, path, data)


def read_document(path: str | Path, encoding: str | None = None) -> CueDocument:
    """Read a .cue for editing. Raises OSError."""
    path = Path(path)
    return parse_document(path.read_bytes(), encoding, path)


def format_cue(doc: CueDocument) -> bytes:
    """The sheet's bytes. Raises CueEncodingError naming the characters its encoding can't hold."""
    text = doc.text()
    try:
        return doc.bom + text.encode(doc.codec, errors="surrogateescape")
    except UnicodeEncodeError:
        def fits(c: str) -> bool:
            try:
                c.encode(doc.codec, errors="surrogateescape")
                return True
            except UnicodeEncodeError:
                return False
        bad = sorted({c for c in text if not fits(c)})
        raise CueEncodingError(doc.encoding, bad) from None


def save_cue(doc: CueDocument, path: str | Path, recycle: Callable[[Path], None] | None = None,
             check: Callable[[Path], str | None] | None = None) -> None:
    """Save (over the sheet's own file) and Save as (a new file, or one the user agreed to replace): write
    "<name>.partial", send the file it replaces to the Recycle Bin, then rename the new one into place. Refused,
    with nothing written, where Windows can't recycle (fsops.CannotRecycle). A Ground rule 1 exception (SPEC §22).
    Raises CueEncodingError, OSError."""
    path = Path(path)
    data = format_cue(doc)
    check = check or fsops.can_recycle
    recycle = recycle or (lambda p: fsops.send_to_recycle_bin(p, check))
    partial = path.with_name(path.name + PARTIAL)
    if partial.exists():
        raise FileExistsError(f"{partial.name} already exists, maybe left by an earlier save. Delete it in File "
                              "Explorer, then save again.")
    replaces = path.exists()
    if replaces:
        reason = check(path)
        if reason is not None:
            raise fsops.CannotRecycle(reason)
    partial.write_bytes(data)
    try:
        if replaces:
            recycle(path)
        partial.rename(path)
    except OSError as e:
        # Ground rule 1 deletes nothing for good but the split's own .partial files: this one stays, named.
        raise OSError(e.errno, f"{e.strerror or e}. The new version stays in {partial.name}, and the old one "
                               f"wasn't replaced.", str(path)) from e
    doc.path, doc.saved = path, data


# ---------------------------------------------------------------------------
# Live validation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Problem:
    """A bad value, for the page to mark: the track's row (None for the album or the sheet) and its column."""

    row: int | None
    column: str  # a TRACK_FIELDS or ALBUM_FIELDS key, "file", "name", or "" for the sheet
    message: str
    blocks_save: bool = True  # False for what isn't cue data (a file name): only Split is disabled


def validate(doc: CueDocument, info: SourceInfo | None = None) -> list[Problem]:
    """The sheet's bad values: malformed times, starts that don't increase, a start past the end of the audio
    (with `info`), a gap after its track's start, fewer than two tracks, double quotes in a value, a disc
    number that isn't a number. Naming problems come from the plan (cue.plan_sheet)."""
    problems: list[Problem] = []
    album = doc.album()
    for name, (_tag, quoted) in ALBUM_FIELDS.items():
        if quoted and '"' in album[name]:
            problems.append(Problem(None, name, 'A cue value can\'t contain a double quote (").'))
    for name in ("disc", "disc_total"):
        if album[name] and not album[name].split("/")[0].strip().isdigit():
            problems.append(Problem(None, name, f"{album[name]!r} isn't a number."))
    views = doc.tracks()
    if len(views) < 2:
        problems.append(Problem(None, "", "A sheet needs at least two tracks to be split."))
    starts: list[int | None] = []
    for view in views:
        n = view.row + 1
        for name, (_tag, quoted) in TRACK_FIELDS.items():
            if quoted and '"' in view.values[name]:
                problems.append(Problem(view.row, name, 'A cue value can\'t contain a double quote (").'))
        start = gap = None
        if not view.values["start"]:
            problems.append(Problem(view.row, "start", f"Track {n} has no start time (INDEX 01)."))
        else:
            try:
                start = frames_from_time(view.values["start"])
            except CueError as e:
                problems.append(Problem(view.row, "start", f"{e}."))
        if view.values["gap"]:
            try:
                gap = frames_from_time(view.values["gap"])
            except CueError as e:
                problems.append(Problem(view.row, "gap", f"{e}."))
        if gap is not None and start is not None and gap > start:
            problems.append(Problem(view.row, "gap", f"Track {n}'s gap (INDEX 00) must come before its start."))
        starts.append(start)
    for i in range(1, len(starts)):
        if starts[i] is not None and starts[i - 1] is not None and starts[i] <= starts[i - 1]:
            problems.append(Problem(i, "start", f"Track {i + 1} must start after track {i}."))
    if info is not None:
        for i, start in enumerate(starts):
            if start is not None and samples_from_frames(start, info.rate) >= info.samples:
                problems.append(Problem(i, "start", f"Track {i + 1} starts after the end of the audio "
                                                    f"({format_time(start)}, the audio lasts "
                                                    f"{format_time(info.samples * FRAMES_PER_SECOND // info.rate)})."))
    return problems
