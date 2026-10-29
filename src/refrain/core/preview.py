"""File preview (SPEC §20): which files the side panel shows read-only, and reading text of unknown encoding.

Album folders hold rip logs (EAC writes UTF-16), cue sheets, notes in Windows-1252 and NFO files in the DOS code
page; each is decoded the way its writer meant, and a garbled byte becomes U+FFFD, never an error.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from refrain.core.fsops import open_shared

TEXT_TYPES = frozenset({".txt", ".log", ".cue", ".nfo", ".sfv", ".md5", ".ffp", ".accurip", ".m3u", ".m3u8", ".lrc"})
IMAGE_TYPES = frozenset({".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"})
PREVIEW_TYPES = TEXT_TYPES | IMAGE_TYPES
# Showing text must not stall the UI (SPEC §20, measured in M9): 1 MB of log lines took up to 52 ms, 512 KB up to
# 29 ms. Without word wrap Qt lays out every line to find the widest, so long lines cost more: up to 300
# characters stay under 33 ms, longer ones are word-wrapped (under 16 ms), and a line is cut into pieces of at
# most BREAK_AT characters, since even wrapped, one 200,000-character line took 5 to 8 s.
TEXT_LIMIT = 512 * 1024  # bytes shown; the rest is one Open away
WRAP_FROM = 300  # a longer line turns word wrap on
BREAK_AT = 2000  # longer lines are cut into pieces of this length
IMAGE_LIMIT = 50 * 1024 * 1024  # larger images aren't decoded

_BOMS = ((b"\xef\xbb\xbf", "utf-8", "UTF-8 (BOM)"), (b"\xff\xfe", "utf-16-le", "UTF-16 LE"),
         (b"\xfe\xff", "utf-16-be", "UTF-16 BE"))


# The five bytes Windows-1252 assigns no character to: text holding one was written in another code page (Shift-JIS
# and GBK use them often), so reading it as Windows-1252 would garble it. Proof, not a guess.
CP1252_UNASSIGNED = (b"\x81", b"\x8d", b"\x8f", b"\x90", b"\x9d")


def not_cp1252(data: bytes) -> bool:
    return any(byte in data for byte in CP1252_UNASSIGNED)


def kind(path: str | Path) -> str | None:
    """"text", "image", or None when the file isn't previewed."""
    suffix = Path(path).suffix.lower()
    return "text" if suffix in TEXT_TYPES else "image" if suffix in IMAGE_TYPES else None


def decode(data: bytes, legacy: str = "cp1252", cut: bool = False) -> tuple[str, str]:
    """(text, encoding name) for bytes of unknown encoding: a BOM first, then UTF-8 if the bytes are valid, else
    the `legacy` code page. When the bytes were `cut` from a longer file, a UTF-8 character split at the end still
    counts as UTF-8 (a whole cp1252 file can end with a byte that looks like such a start, so only then)."""
    for bom, codec, name in _BOMS:
        if data.startswith(bom):
            return data[len(bom):].decode(codec, errors="replace"), name
    try:
        return data.decode("utf-8"), "UTF-8"
    except UnicodeDecodeError as e:
        if cut and e.reason == "unexpected end of data" and e.end == len(data):  # valid until the cut
            return data.decode("utf-8", errors="replace"), "UTF-8"
    return data.decode(legacy, errors="replace"), "CP437 (DOS)" if legacy == "cp437" else "Windows-1252"


@dataclass(frozen=True)
class TextPreview:
    text: str
    encoding: str  # its name, shown with the file's details so a garbled preview explains itself
    size: int  # the whole file, in bytes
    truncated: bool  # only the first `limit` bytes are in `text`
    wrap: bool = False  # a line is longer than WRAP_FROM: show it word-wrapped


def read_text(path: str | Path, limit: int = TEXT_LIMIT) -> TextPreview:
    """The first `limit` bytes of a text file, decoded, with lines over BREAK_AT cut into pieces. Read with
    sharing, so Explorer can still rename or delete the file meanwhile. Raises OSError."""
    path = Path(path)
    with open_shared(path) as f:
        size = os.fstat(f.fileno()).st_size
        data = f.read(limit)
    truncated = size > len(data)
    text, encoding = decode(data, "cp437" if path.suffix.lower() == ".nfo" else "cp1252", cut=truncated)
    lines = text.replace("\r\n", "\n").split("\n")
    longest = max(map(len, lines))
    if longest > BREAK_AT:
        lines = [line[i:i + BREAK_AT] for line in lines for i in range(0, max(len(line), 1), BREAK_AT)]
    return TextPreview("\n".join(lines), encoding, size, truncated, wrap=longest > WRAP_FROM)
