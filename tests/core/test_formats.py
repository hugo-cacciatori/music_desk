"""SPEC §1: every format in the table opens with its mutagen class and round-trips title, lyrics and cover.

The writes here use each family's raw keys; core/tags.py adapters get their own tests on top of these fixtures.
"""

import base64

import pytest
from mutagen.apev2 import BINARY, APEValue
from mutagen.asf import ASFByteArrayAttribute
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TIT2, USLT
from mutagen.mp4 import MP4Cover

from music_desk.core.formats import AUDIO_EXTENSIONS, FORMATS, REPLAYGAIN_EXTENSIONS, TagFamily, format_for, open_audio
from music_desk.core.tags import Cover, pack_asf_picture, unpack_asf_picture

TITLE = "Round Trip – ünïcødé"
LYRICS = "Line one\nLine two"

# Fixture per extension in tests/fixtures/generated/formats/
FIXTURES_BY_EXTENSION = {
    ".flac": ["tone.flac"], ".ogg": ["tone.ogg"], ".oga": ["tone.oga"], ".opus": ["tone.opus"],
    ".mp3": ["tone.mp3"], ".mp2": ["tone.mp2"], ".wav": ["tone.wav"], ".aiff": ["tone.aiff"],
    ".m4a": ["tone_aac.m4a", "tone_alac.m4a"], ".m4b": ["tone.m4b"], ".wv": ["tone.wv"], ".tta": ["tone.tta"],
    ".wma": ["tone.wma"], ".aac": ["tone.aac"],
}
# Same container as a fixture under another extension, so the fixture covers them.
COVERED_BY_ALIAS = {".aif": ".aiff", ".aifc": ".aiff", ".mp4": ".m4a"}
# ffmpeg can't encode these (or this build lacks the encoder), so they have no fixture. They stay supported through
# mutagen, untested with a real file: getting sample files was dropped (Hugo, 2026-10-15).
NO_FIXTURE = {".spx", ".ape", ".mpc", ".tak", ".ofr", ".ofs", ".dsf", ".dff"}

TAGGED_FIXTURES = [name for ext, names in FIXTURES_BY_EXTENSION.items() if ext != ".aac" for name in names]


@pytest.fixture
def cover_bytes(fixtures_dir) -> bytes:
    return (fixtures_dir / "cover.jpg").read_bytes()


def test_every_extension_is_accounted_for():
    assert AUDIO_EXTENSIONS == set(FIXTURES_BY_EXTENSION) | set(COVERED_BY_ALIAS) | NO_FIXTURE


def test_extensions_are_unique_and_lowercase():
    extensions = [ext for info in FORMATS for ext in info.extensions]
    assert len(extensions) == len(set(extensions))
    assert all(ext.startswith(".") and ext == ext.lower() for ext in extensions)


def test_format_for_ignores_case_and_rejects_non_audio():
    assert format_for("x/Track.FLAC").family is TagFamily.VORBIS
    assert format_for("folder.jpg") is None
    assert format_for("album.cue") is None
    assert ".m4b" not in REPLAYGAIN_EXTENSIONS and ".flac" in REPLAYGAIN_EXTENSIONS


@pytest.mark.parametrize("name", [n for names in FIXTURES_BY_EXTENSION.values() for n in names])
def test_fixture_opens_with_its_class(fixtures_dir, name):
    path = fixtures_dir / "formats" / name
    info = format_for(path)
    audio = open_audio(path)
    assert isinstance(audio, info.mutagen_classes)
    assert 0.9 < audio.info.length < 1.2


# ---------------------------------------------------------------------------
# Raw per-family writes and reads
# ---------------------------------------------------------------------------

def flac_picture(data: bytes) -> Picture:
    picture = Picture()
    picture.type = 3
    picture.mime = "image/jpeg"
    picture.data = data
    return picture


def asf_picture(data: bytes) -> bytes:
    return pack_asf_picture(Cover(data, "image/jpeg"))


def asf_picture_data(raw: bytes) -> bytes:
    return unpack_asf_picture(raw)[1].data


def write(audio, family: TagFamily, cover: bytes) -> None:
    if audio.tags is None:
        audio.add_tags()
    match family:
        case TagFamily.VORBIS:
            audio["TITLE"] = TITLE
            audio["LYRICS"] = LYRICS
            if isinstance(audio, FLAC):
                audio.clear_pictures()
                audio.add_picture(flac_picture(cover))
            else:
                audio["METADATA_BLOCK_PICTURE"] = base64.b64encode(flac_picture(cover).write()).decode("ascii")
            audio.save()
        case TagFamily.ID3:
            version = audio.tags.version[1] if audio.tags.version >= (2, 3, 0) else 4
            audio.tags.add(TIT2(encoding=3, text=TITLE))
            audio.tags.add(USLT(encoding=3, lang="eng", desc="", text=LYRICS))
            audio.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=cover))
            audio.save(v2_version=version)
        case TagFamily.MP4:
            audio.tags["\xa9nam"] = [TITLE]
            audio.tags["\xa9lyr"] = [LYRICS]
            audio.tags["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]
            audio.save()
        case TagFamily.APEV2:
            audio.tags["Title"] = TITLE
            audio.tags["Lyrics"] = LYRICS
            audio.tags["Cover Art (Front)"] = APEValue(b"cover.jpg\0" + cover, BINARY)
            audio.save()
        case TagFamily.ASF:
            audio.tags["Title"] = [TITLE]
            audio.tags["WM/Lyrics"] = [LYRICS]
            audio.tags["WM/Picture"] = [ASFByteArrayAttribute(asf_picture(cover))]
            audio.save()


def read(audio, family: TagFamily) -> tuple[str, str, bytes]:
    tags = audio.tags
    match family:
        case TagFamily.VORBIS:
            if isinstance(audio, FLAC):
                cover = audio.pictures[0].data
            else:
                cover = Picture(base64.b64decode(tags["METADATA_BLOCK_PICTURE"][0])).data
            return tags["TITLE"][0], tags["LYRICS"][0], cover
        case TagFamily.ID3:
            assert isinstance(tags, ID3)
            return str(tags["TIT2"]), str(tags["USLT::eng"]), tags.getall("APIC")[0].data
        case TagFamily.MP4:
            return tags["\xa9nam"][0], tags["\xa9lyr"][0], bytes(tags["covr"][0])
        case TagFamily.APEV2:
            return str(tags["Title"]), str(tags["Lyrics"]), tags["Cover Art (Front)"].value.split(b"\0", 1)[1]
        case TagFamily.ASF:
            return str(tags["Title"][0]), str(tags["WM/Lyrics"][0]), asf_picture_data(tags["WM/Picture"][0].value)
    raise AssertionError(family)


@pytest.mark.parametrize("name", TAGGED_FIXTURES)
def test_round_trip_title_lyrics_cover(fixture_copy, cover_bytes, name):
    path = fixture_copy(f"formats/{name}")
    info = format_for(path)
    assert info.tags
    original_length = open_audio(path).info.length

    write(open_audio(path), info.family, cover_bytes)
    reopened = open_audio(path)

    assert read(reopened, info.family) == (TITLE, LYRICS, cover_bytes)
    assert reopened.info.length == pytest.approx(original_length)
    assert reopened.tags["ARTIST" if info.family is TagFamily.VORBIS else {
        TagFamily.ID3: "TPE1", TagFamily.MP4: "\xa9ART", TagFamily.APEV2: "Artist", TagFamily.ASF: "Author",
    }[info.family]][0] == "Fixture Artist"  # existing tags survive


def test_raw_aac_has_no_tags(fixtures_dir):
    path = fixtures_dir / "formats" / "tone.aac"
    assert not format_for(path).tags
    assert open_audio(path).tags is None


def test_id3v23_is_preserved(fixture_copy, cover_bytes):
    path = fixture_copy("album/Fixture Artist - Fixture Album/no_track.mp3")
    assert open_audio(path).tags.version == (2, 3, 0)
    write(open_audio(path), TagFamily.ID3, cover_bytes)
    assert open_audio(path).tags.version == (2, 3, 0)


def test_open_audio_rejects_non_audio(fixtures_dir):
    with pytest.raises(ValueError, match="not an audio file"):
        open_audio(fixtures_dir / "cover.jpg")
