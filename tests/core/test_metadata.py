"""core/metadata.py's write path on every fixture format (SPEC §7): a save writes only the changed field or the
cover and keeps every other tag. The Metadata tab's tests check the tab on two formats; the formats are here."""

import mutagen
import pytest
from test_tags import FORMAT_FIXTURES

from refrain.core import metadata
from refrain.core.tags import FIELD_NAMES, TaggedFile


def raw_keys(path) -> set[str]:
    """Every tag key in the file, as mutagen stores it (ignoring case)."""
    tags = mutagen.File(path).tags
    return {str(key).casefold() for key in tags.keys()} if tags is not None else set()


@pytest.mark.parametrize("name", FORMAT_FIXTURES)
def test_one_changed_field_keeps_everything_else(fixture_copy, name):
    path = fixture_copy(f"formats/{name}")
    before, keys = TaggedFile(path).fields(), raw_keys(path)
    snapshot = metadata.write_fields(path, {"title": "New Title"})
    after = TaggedFile(path).fields()
    assert after.title == "New Title" and snapshot.fields == after
    assert [f for f in FIELD_NAMES if f != "title" and getattr(after, f) != getattr(before, f)] == []
    assert raw_keys(path) >= keys


@pytest.mark.parametrize("name", FORMAT_FIXTURES)
def test_a_new_cover_is_written(fixture_copy, fixtures_dir, name):
    path = fixture_copy(f"formats/{name}")
    cover = (fixtures_dir / "cover.jpg").read_bytes()
    snapshot = metadata.write_fields(path, {metadata.COVER: cover})
    assert TaggedFile(path).cover().data == cover and snapshot.cover_source == "embedded"
