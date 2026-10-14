"""Parity of core/tags.py with the frozen music_tools.py (CLAUDE.md Ground rule 3, SPEC §17).

- The script's get_tag/set_tag/set_comment and TaggedFile do the same thing to twin copies of FLAC/MP3 files.
  Deliberate differences: Vorbis ReplayGain keys are written in uppercase (the script writes them as given,
  lowercase), and ID3v2.3 files stay v2.3 (the script's save upgrades them to v2.4).
- rename_fields() gives what the script's read_fields(TinyTag.get(...)) gives, before sanitizing, on every fixture
  TinyTag reads. Deliberate difference: WMA disc numbers are read (TinyTag 2.0.0 ignores WM/PartOfSet).
"""

import shutil

import pytest
from tagdump import tag_dump
from tinytag import TinyTag

from music_desk.core.formats import AUDIO_EXTENSIONS
from music_desk.core.tags import TaggedFile

ALBUM = "album/Fixture Artist - Fixture Album"
FLAC_MP3_FIXTURES = [f"{ALBUM}/01 First.flac", f"{ALBUM}/lowercase_rg.mp3", f"{ALBUM}/no_track.mp3",
                     "edge/edge.flac", "edge/edge.mp3"]
# TinyTag 2.0.0 has no WavPack reader.
TINYTAG_UNREADABLE = {".wv"}


@pytest.fixture
def twins(fixtures_dir, tmp_path):
    def make(relative):
        source = fixtures_dir / relative
        paths = []
        for side in ("script", "app"):
            (tmp_path / side).mkdir(exist_ok=True)
            paths.append(shutil.copy2(source, tmp_path / side / source.name))
        return paths

    return make


@pytest.mark.parametrize("relative", FLAC_MP3_FIXTURES)
def test_get_tag_parity(reference_music_tools, fixtures_dir, relative):
    ref = reference_music_tools
    path = fixtures_dir / relative
    script_audio, tagged = ref.load_audio(str(path)), TaggedFile(path)
    for key in (ref.TRACK_GAIN, ref.TRACK_PEAK, ref.ALBUM_GAIN, ref.ALBUM_PEAK):
        assert tagged.get_replaygain(key) == ref.get_tag(script_audio, key)


@pytest.mark.parametrize("relative", FLAC_MP3_FIXTURES)
def test_set_tag_and_comment_parity(reference_music_tools, twins, relative):
    """The writes of the ReplayGain step: track and album values, then the LUFS comment."""
    ref = reference_music_tools
    script_path, app_path = twins(relative)
    writes = [(ref.TRACK_GAIN, "-3.21 dB"), (ref.TRACK_PEAK, "0.987654"), (ref.ALBUM_GAIN, "-4.50 dB"),
              (ref.ALBUM_PEAK, "0.999990")]

    script_audio = ref.load_audio(str(script_path))
    for key, value in writes:
        ref.set_tag(script_audio, key, value)
    ref.set_comment(script_audio, ref.LUFS_COMMENT)
    script_audio.save()

    tagged = TaggedFile(app_path)
    for key, value in writes:
        tagged.set_replaygain(key, value)
    tagged.set_comment(ref.LUFS_COMMENT)
    tagged.save()

    script_after, app_after = ref.load_audio(str(script_path)), TaggedFile(app_path).audio
    assert tag_dump(app_after) == tag_dump(script_after)
    for key, _ in writes:
        assert TaggedFile(app_path).get_replaygain(key) == ref.get_tag(script_after, key)

    if hasattr(app_after.tags, "version"):
        original = TaggedFile(app_path).audio.tags.version  # the app keeps the file's version
        assert script_after.tags.version == (2, 4, 0)
        assert original == ((2, 3, 0) if relative.endswith("no_track.mp3") else (2, 4, 0))
    else:  # Vorbis: the app writes ReplayGain keys in uppercase, the script as given
        app_keys = {key for key, _ in app_after.tags if key.lower().startswith("replaygain_")}
        assert app_keys == {key.upper() for key, _ in writes}


def audio_fixtures(fixtures_dir):
    return sorted(p for p in fixtures_dir.rglob("*") if p.suffix.lower() in AUDIO_EXTENSIONS)


def test_rename_fields_match_tinytag(reference_music_tools, fixtures_dir):
    ref = reference_music_tools
    checked = 0
    for path in audio_fixtures(fixtures_dir):
        if path.suffix.lower() in TINYTAG_UNREADABLE:
            continue
        expected = ref.read_fields(TinyTag.get(path))
        ours = TaggedFile(path).rename_fields()
        if path.suffix.lower() == ".wma":
            ours = {k: v for k, v in ours.items() if k not in ("disc", "disc_total")}
        actual = {k: ref.sanitize(v) if isinstance(v, str) else v for k, v in ours.items()}
        assert actual == expected, path.relative_to(fixtures_dir).as_posix()
        checked += 1
    assert checked >= 30
