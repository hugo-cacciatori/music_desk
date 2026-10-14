"""core/tags.py: the family adapters on every fixture format (SPEC §1, §7)."""

import pytest
from mutagen.id3 import ID3
from mutagen.wave import WAVE

from music_desk.core.tags import FIELD_NAMES, Fields, TagError, TaggedFile

FORMAT_FIXTURES = ["tone.flac", "tone.ogg", "tone.oga", "tone.opus", "tone.mp3", "tone.mp2", "tone.wav", "tone.aiff",
                   "tone_aac.m4a", "tone_alac.m4a", "tone.m4b", "tone.wv", "tone.tta", "tone.wma"]
ALBUM = "album/Fixture Artist - Fixture Album"

NEW_VALUES = {"title": "New Title", "artist": "New Artist", "albumartist": "New Album Artist", "album": "New Album",
              "track": 7, "track_total": 9, "disc": 2, "disc_total": 3, "year": "1999", "genre": "Jazz",
              "composer": "New Composer", "comment": "New comment", "lyrics": "[00:01.00]Line one\nLine two"}


def reopen(tagged: TaggedFile) -> TaggedFile:
    tagged.save()
    return TaggedFile(tagged.path)


@pytest.fixture
def cover(fixtures_dir) -> bytes:
    return (fixtures_dir / "cover.jpg").read_bytes()


@pytest.mark.parametrize("name", FORMAT_FIXTURES)
def test_reads_the_generator_values(fixtures_dir, name):
    fields = TaggedFile(fixtures_dir / "formats" / name).fields()
    assert fields == Fields(title=f"Tone {name}", artist="Fixture Artist", albumartist="Fixture Artist",
                            album="Fixture Formats", track=1, track_total=None if name.endswith(".wma") else 1,
                            disc=1, disc_total=1, year="2024", genre="Test")


@pytest.mark.parametrize("name", FORMAT_FIXTURES)
def test_set_and_clear_every_field(fixture_copy, name):
    path = fixture_copy(f"formats/{name}")
    values = dict(NEW_VALUES)
    if name.endswith(".wma"):
        values["track_total"] = None  # WMA has no track total

    tagged = TaggedFile(path)
    tagged.set_fields(values)
    tagged = reopen(tagged)
    assert tagged.fields() == Fields(**values)

    tagged.set_fields(dict.fromkeys(FIELD_NAMES))
    assert reopen(tagged).fields() == Fields()


@pytest.mark.parametrize("name", FORMAT_FIXTURES)
def test_replaygain_round_trip(fixture_copy, name):
    tagged = TaggedFile(fixture_copy(f"formats/{name}"))
    tagged.set_replaygain("replaygain_track_gain", "-3.21 dB")
    tagged.set_replaygain("replaygain_track_peak", "0.987654")
    tagged = reopen(tagged)
    assert tagged.get_replaygain("replaygain_track_gain") == "-3.21 dB"
    assert tagged.get_replaygain("REPLAYGAIN_TRACK_PEAK") == "0.987654"
    assert tagged.get_replaygain("replaygain_album_gain") is None


@pytest.mark.parametrize("name", FORMAT_FIXTURES)
def test_cover_set_and_remove(fixture_copy, cover, name):
    tagged = TaggedFile(fixture_copy(f"formats/{name}"))
    assert tagged.cover() is None
    tagged.set_cover(cover)
    tagged = reopen(tagged)
    assert tagged.cover().data == cover
    assert tagged.cover().mime == "image/jpeg"
    tagged.remove_cover()
    assert reopen(tagged).cover() is None


def test_lowercase_txxx_is_matched_and_replaced_in_uppercase(fixture_copy):
    tagged = TaggedFile(fixture_copy(f"{ALBUM}/lowercase_rg.mp3"))
    assert tagged.get_replaygain("REPLAYGAIN_TRACK_GAIN") == "-1.00 dB"
    tagged.set_replaygain("replaygain_track_gain", "-2.00 dB")
    tagged = reopen(tagged)
    frames = [f for f in tagged.audio.tags.getall("TXXX") if f.desc.lower() == "replaygain_track_gain"]
    assert [(f.desc, f.text[0]) for f in frames] == [("REPLAYGAIN_TRACK_GAIN", "-2.00 dB")]


def test_field_edit_leaves_other_tags_alone(fixture_copy):
    tagged = TaggedFile(fixture_copy(f"{ALBUM}/lowercase_rg.mp3"))
    tagged.set_fields({"title": "Changed"})
    tagged = reopen(tagged)
    assert tagged.get_replaygain("replaygain_track_peak") == "0.500000"
    assert tagged.fields().artist == "Fixture Artist"
    assert tagged.fields().track == 4 and tagged.fields().track_total == 6


def test_track_total_alone_keeps_the_track_number(fixture_copy):
    tagged = TaggedFile(fixture_copy("edge/edge.flac"))  # TRACKNUMBER "03/12"
    tagged.set_fields({"track_total": 10})
    tagged = reopen(tagged)
    assert (tagged.fields().track, tagged.fields().track_total) == (3, 10)
    assert tagged.audio["TRACKNUMBER"] == ["3"]


def test_edge_values_are_read_as_stored(fixtures_dir):
    for name in ("edge.flac", "edge.mp3", "edge.m4a"):
        fields = TaggedFile(fixtures_dir / "edge" / name).fields()
        assert fields.title == "  Spaced Title  "
        assert fields.year == "2024-05-01"
        assert (fields.track, fields.track_total) == (3, 12)
        assert fields.artist == ("" if name != "edge.mp3" else None)  # mutagen never writes an empty ID3 frame
    assert TaggedFile(fixtures_dir / "edge" / "edge.flac").fields().disc_total == 2  # from TOTALDISCS
    assert TaggedFile(fixtures_dir / "edge" / "edge.m4a").fields().disc_total is None  # MP4 stores 0


def test_vorbis_legacy_lyrics_key(fixture_copy):
    tagged = TaggedFile(fixture_copy("edge/edge.flac"))
    assert tagged.fields().lyrics == "Legacy lyrics"
    tagged.set_fields({"lyrics": "New lyrics"})
    tagged = reopen(tagged)
    assert tagged.fields().lyrics == "New lyrics"
    assert "UNSYNCEDLYRICS" not in tagged.audio


def test_id3_comment_ignores_described_frames_and_drops_txxx_comment(fixture_copy):
    tagged = TaggedFile(fixture_copy("edge/edge.mp3"))
    assert tagged.fields().comment == "edge comment"
    tagged.set_comment("LUFS Target : -15")
    tagged = reopen(tagged)
    assert tagged.fields().comment == "LUFS Target : -15"
    assert not [f for f in tagged.audio.tags.getall("TXXX") if f.desc.lower() == "comment"]
    assert [f.text[0] for f in tagged.audio.tags.getall("COMM") if f.desc == "iTunNORM"] == [" 00000001 00000002"]


@pytest.mark.parametrize("name", ["edge.flac", "edge.mp3"])
def test_front_cover_preferred_and_back_cover_kept(fixture_copy, fixtures_dir, cover, name):
    back = (fixtures_dir / "edge" / "back.jpg").read_bytes()
    front = (fixtures_dir / "edge" / "front.jpg").read_bytes()
    tagged = TaggedFile(fixture_copy(f"edge/{name}"))
    assert tagged.cover().data == front

    tagged.set_cover(cover)
    tagged = reopen(tagged)
    assert tagged.cover().data == cover

    tagged.remove_cover()
    tagged = reopen(tagged)
    assert tagged.cover().data == back  # only the front cover went


@pytest.mark.parametrize("relative", [f"{ALBUM}/no_track.mp3", "formats/tone.wav", "formats/tone.aiff"])
def test_id3v23_is_preserved(fixture_copy, relative):
    path = fixture_copy(relative)
    if path.suffix != ".mp3":  # make the WAV/AIFF chunk v2.3 first
        audio = WAVE(path) if path.suffix == ".wav" else None
        if audio is None:
            from mutagen.aiff import AIFF

            audio = AIFF(path)
        audio.save(v2_version=3)
    tagged = TaggedFile(path)
    assert tagged.audio.tags.version == (2, 3, 0)
    tagged.set_fields({"title": "v2.3 stays"})
    tagged = reopen(tagged)
    assert tagged.audio.tags.version == (2, 3, 0)
    assert tagged.fields().title == "v2.3 stays"


def test_untagged_mp3_gets_id3v24(fixture_copy):
    path = fixture_copy("formats/tone.mp3")
    ID3(path).delete()
    tagged = TaggedFile(path)
    tagged.set_fields({"title": "Fresh"})
    assert reopen(tagged).audio.tags.version == (2, 4, 0)


def test_raw_aac_is_read_only(fixture_copy):
    tagged = TaggedFile(fixture_copy("formats/tone.aac"))
    assert tagged.fields() == Fields()
    assert tagged.cover() is None
    with pytest.raises(TagError, match="tone.aac: this format has no tags"):
        tagged.set_fields({"title": "x"})
    with pytest.raises(TagError):
        tagged.set_replaygain("replaygain_track_gain", "1.00 dB")


def test_wma_refuses_a_track_total(fixture_copy):
    tagged = TaggedFile(fixture_copy("formats/tone.wma"))
    with pytest.raises(TagError, match="no standard track total"):
        tagged.set_fields({"track_total": 12})


def test_unknown_field_is_refused(fixture_copy):
    with pytest.raises(ValueError, match="unknown field"):
        TaggedFile(fixture_copy("formats/tone.flac")).set_fields({"bpm": 120})


def test_rename_fields_drop_blanks_and_keep_spacing(fixtures_dir):
    fields = TaggedFile(fixtures_dir / "edge" / "edge.flac").rename_fields()
    assert "artist" not in fields
    assert fields["title"] == "  Spaced Title  "
    assert fields["track"] == 3 and fields["track_total"] == 12
    assert "comment" not in fields and "lyrics" not in fields  # not mask fields


def test_stream_info(fixtures_dir):
    info = TaggedFile(fixtures_dir / "formats" / "tone.flac").stream_info()
    assert info.sample_rate == 44100 and info.channels == 1 and info.bits_per_sample == 16
    assert 0.9 < info.duration < 1.2
    assert TaggedFile(fixtures_dir / "formats" / "tone_alac.m4a").stream_info().codec == "ALAC"
