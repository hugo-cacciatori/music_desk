"""core/masks.py: file names from tags, shared by Rename, Tidy, the cue split and the rename hint (SPEC §21)."""

from music_desk.core import masks, rename
from music_desk.core.metadata import suggested_name
from music_desk.core.tags import TaggedFile


def test_name_for():
    fields = {"track": 3, "title": "Who: Me?", "artist": "A/B"}
    assert masks.name_for(fields, masks.DEFAULT_MASKS, "-", ".FLAC") == "03 Who- Me-.flac"
    assert masks.name_for(fields, ("{artist} - {title}",), "_", ".mp3") == "A_B - Who_ Me_.mp3"
    assert masks.name_for({"genre": "Rock"}, masks.DEFAULT_MASKS, "-", ".mp3") is None


def test_rename_uses_the_same_code():
    """Rename keeps importing the mask functions (the parity tests use them from there), so every caller names
    the same fields the same way."""
    for name in ("choose_name", "mask_values", "mask_fields", "missing_from", "sanitize", "FORBIDDEN_CHARACTERS"):
        assert getattr(rename, name) is getattr(masks, name)
    options = rename.RenameOptions(masks=("{disc}-{track:02} {title}",), replacement="_")
    assert rename.validate(options) == masks.validate_masks(options.masks, options.replacement) == []


def test_validate_masks():
    assert masks.validate_masks(masks.DEFAULT_MASKS, "-") == []
    assert masks.validate_masks((), "-") == ["No rename mask"]
    assert masks.validate_masks(("{title}",), ":") == [
        "The replacement contains a character not allowed in file names"]


def test_the_hint_takes_the_masks_it_is_given(fixture_copy):
    path = fixture_copy("album/Fixture Artist - Fixture Album/track2.flac")
    tagged = TaggedFile(path)
    assert suggested_name(tagged) == "02 Second.flac"  # the defaults
    assert suggested_name(tagged, ("{album} - {title}",), "-") == "Fixture Album - Second.flac"
    assert suggested_name(tagged, ("{bogus}",), "-") is None  # invalid masks: no hint
