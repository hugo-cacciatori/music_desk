"""Legacy text in ID3 frames marked Latin-1 (SPEC §27): UTF-8, Shift-JIS, GBK and Big5 bytes read as the right text,
Western Latin-1 text unchanged."""

import pytest
from mutagen.id3 import ID3, TALB, TIT2, TPE1, Encoding

from refrain.core.tags import TaggedFile, decode_latin1_text


def as_mutagen_reads(text: str, codec: str) -> str:
    """What mutagen gives for `text` written in `codec` into a frame marked Latin-1."""
    return text.encode(codec).decode("latin-1")


@pytest.mark.parametrize(("text", "codec"), [
    ("こんにちは", "cp932"), ("椎名林檎 - 丸の内サディスティック", "cp932"), ("Mr.Children - 終わりなき旅", "cp932"),
    ("アイウエオ", "cp932"), ("東京事変", "cp932"),
    ("周杰伦 - 七里香", "gbk"), ("你好", "gbk"), ("邓丽君 月亮代表我的心", "gbk"),
    ("周杰倫", "big5"), ("張學友 - 吻別", "big5"), ("五月天", "big5"),
    ("こんにちは", "utf-8"), ("Björk", "utf-8"), ("안녕하세요", "utf-8"),
])
def test_legacy_bytes_read_as_their_text(text, codec):
    assert decode_latin1_text(as_mutagen_reads(text, codec)) == text


@pytest.mark.parametrize("text", [
    "Beyoncé", "Sigur Rós", "Mötley Crüe", "Àgua de Beber", "Ärzte", "Ångström", "Café del Mar", "Señor", "Æon Flux",
    "Hüsker Dü", "Les Misérables", "Édith Piaf", "Ñandú", "Sinéad O'Connor", "Ólafur Arnalds", "Øystein",
    "Für Elise", "Déjà vu", "ÁÉÍÓÚ", "Ünïcödé", "°C", "plain ASCII",
])
def test_western_text_is_unchanged(text):
    latin1 = as_mutagen_reads(text, "cp1252")
    assert decode_latin1_text(latin1) == latin1


def test_an_id3v1_value_cut_in_half_keeps_the_rest():
    raw = "椎名林檎 - 丸の内サディスティック".encode("cp932")[:30]  # the field's 30 bytes end mid-character
    assert raw[-1] >= 0x80
    assert decode_latin1_text(raw.decode("latin-1")) == "椎名林檎 - 丸の内サディスティ"


def test_a_file_with_latin1_frames(fixture_copy):
    path = fixture_copy("formats/tone.mp3")
    tags = ID3(path)
    tags.add(TIT2(encoding=Encoding.LATIN1, text=[as_mutagen_reads("丸の内サディスティック", "cp932")]))
    tags.add(TPE1(encoding=Encoding.LATIN1, text=[as_mutagen_reads("周杰伦", "gbk")]))
    tags.add(TALB(encoding=Encoding.UTF16, text=["Ünïcödé"]))  # a Unicode frame is never second-guessed
    tags.save(path)
    fields = TaggedFile(path).fields()
    assert (fields.title, fields.artist, fields.album) == ("丸の内サディスティック", "周杰伦", "Ünïcödé")


def test_an_id3v1_tag(fixture_copy):
    path = fixture_copy("formats/tone.mp3")
    ID3(path).delete(path, delete_v1=True, delete_v2=True)
    title = "東京事変".encode("cp932").ljust(30, b"\0")
    artist = "周杰倫".encode("big5").ljust(30, b"\0")
    with open(path, "ab") as file:
        file.write(b"TAG" + title + artist + b"\0" * 30 + b"2004" + b"\0" * 30 + b"\xff")
    fields = TaggedFile(path).fields()
    assert (fields.title, fields.artist, fields.year) == ("東京事変", "周杰倫", "2004")
