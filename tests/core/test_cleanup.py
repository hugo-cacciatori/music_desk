"""core/cleanup.py: the clutter Tidy offers (SPEC §28 "Clutter", §32): pictures and videos beside music, and the
outermost folders holding only pictures, videos or nothing."""

from pathlib import Path

import pytest

from music_desk.core.cleanup import (
    DEFAULT_LISTS,
    CleanupOptions,
    find_clutter,
    find_items,
    is_clutter,
    options_from_json,
    options_to_json,
    recheck,
    validate,
)


def make(root: Path, *relatives: str) -> None:
    for relative in relatives:
        path = root / relative
        if relative.endswith("/"):
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")


def rows(root: Path) -> dict[str, tuple[str, str]]:
    return {item.path.relative_to(root).as_posix(): (item.name, item.why) for item in find_clutter(root)}


def test_pictures_and_videos_beside_music(tmp_path):
    make(tmp_path, "Album/01.flac", "Album/folder.jpg", "Album/cover.JPG", "Album/back.png", "Album/clip.mkv",
         "Album/video.mp4", "Album/notes.txt", "Album/lite/01.flac", "Album/lite/front.jpg")
    assert rows(tmp_path) == {
        "Album/back.png": ("back.png", "picture beside music"),
        "Album/clip.mkv": ("clip.mkv", "video beside music"),
        "Album/cover.JPG": ("cover.JPG", "picture beside music"),  # covers too, any case
        "Album/folder.jpg": ("folder.jpg", "picture beside music"),
        "Album/lite/front.jpg": ("front.jpg", "picture beside music"),  # lite folders hold audio
    }  # .mp4 is audio (SPEC §1), the .txt isn't offered


def test_clutter_folders_are_one_row_the_outermost_only(tmp_path):
    make(tmp_path, "Album/01.flac", "Album/Scans/a.jpg", "Album/Scans/b.png", "Album/Scans/Inner/c.jpg",
         "Album/Scans/Thumbs.db", "Album/Empty/", "Album/Nested/Deeper/", "Album/Video/x.vob",
         "Artist/Artwork/a.jpg", "Artist/Artwork/desktop.ini")
    assert rows(tmp_path) == {
        "Album/Empty": ("Empty (empty folder)", "empty folder"),
        "Album/Nested": ("Nested (empty folder)", "empty folder"),
        "Album/Scans": ("Scans (folder, 3 pictures)", "folder of 3 pictures"),
        "Album/Video": ("Video (folder, 1 video)", "folder of 1 video"),
        "Artist": ("Artist (folder, 1 picture)", "folder of 1 picture"),  # Artwork is inside: only Artist
    }


def test_a_folder_with_any_other_file_is_not_offered(tmp_path):
    make(tmp_path, "Album/01.flac", "Album/Scans/a.jpg", "Album/Scans/info.txt", "Album/Booklet/b.jpg",
         "Album/Booklet/Sub/booklet.nfo", "Album/Rip/image.cue")
    assert rows(tmp_path) == {}  # nor are their pictures: they aren't beside music


def test_the_folder_tidy_runs_on_is_never_offered(tmp_path):
    make(tmp_path, "a.jpg", "Empty/")
    assert rows(tmp_path) == {"Empty": ("Empty (empty folder)", "empty folder")}  # the root itself stays
    assert find_clutter(tmp_path / "Empty") == []


def test_recheck_before_recycling(tmp_path):
    make(tmp_path, "Album/01.flac", "Album/cover.jpg", "Album/Scans/a.jpg")
    picture, scans = sorted(find_clutter(tmp_path), key=lambda item: item.path)
    assert recheck(picture) is None and recheck(scans) is None
    (tmp_path / "Album" / "Scans" / "readme.txt").write_text("x")
    assert recheck(scans) == "it now holds other files" and not is_clutter(scans.path, DEFAULT_LISTS)
    (tmp_path / "Album" / "cover.jpg").unlink()
    assert recheck(picture) == "gone"


# -- Clean up (SPEC §32) --

def junk_rows(items, folder) -> dict[str, str]:
    return {item.path.relative_to(folder).as_posix(): item.kept or item.why for item in items}


def test_the_defaults_find_what_the_old_junk_cleanup_recycled(fixture_copy):
    """The Rename tool's junk cleanup (before 0.8.10) recycled exactly these, and kept these, on this fixture."""
    folder = fixture_copy("cleanup")
    rows = junk_rows(find_items(folder, CleanupOptions()), folder)
    assert rows == {
        "lrc/no_lyrics.lrc": "junk: .lrc", "lrc/with_lyrics.lrc": "junk: .lrc",
        "per_track_cue/album.cue": "junk: .cue", "per_track_cue/missing.cue": "junk: .cue",
        "single_file_album/checksums.sfv": "junk: .sfv",
        "single_file_album/Fixture Album.cue": "kept: needed to play Fixture Album.flac",
        "single_file_album/info.nfo": "junk: .nfo", "single_file_album/notes.txt": "junk: .txt",
    }  # never the music, the playlist, the cover or the ReplayGain log


def test_the_replaygain_log_is_kept_even_in_the_junk_list(fixture_copy):
    folder = fixture_copy("cleanup")
    options = CleanupOptions(junk_extensions=frozenset({".log"}), protected_names=("replaygain.log",))
    assert junk_rows(find_items(folder, options), folder) == {
        "single_file_album/replaygain.log": "kept: the ReplayGain log"}


def test_pictures_videos_and_folders_only_with_their_boxes(tmp_path):
    make(tmp_path, "Album/01.flac", "Album/cover.jpg", "Album/clip.mkv", "Album/Scans/a.jpg", "Album/notes.txt",
         "Album/Sub/02.flac", "Album/Sub/back.png")
    off = CleanupOptions(junk=False)
    assert find_items(tmp_path / "Album", off) == []
    every = CleanupOptions(pictures=True, videos=True, clutter_folders=True)
    assert junk_rows(find_items(tmp_path / "Album", every), tmp_path / "Album") == {
        "notes.txt": "junk: .txt", "Scans": "folder of 1 picture", "clip.mkv": "video beside music",
        "cover.jpg": "picture beside music", "Sub/back.png": "picture beside music"}
    pictures = CleanupOptions(junk=False, pictures=True, recursive=False)
    assert junk_rows(find_items(tmp_path / "Album", pictures), tmp_path / "Album") == {
        "cover.jpg": "picture beside music"}  # not Sub's: subfolders are off


@pytest.mark.parametrize(("options", "message"), [
    (CleanupOptions(junk_extensions=frozenset({"nfo"})), "must be lowercase and start with a dot"),
    (CleanupOptions(junk_extensions=frozenset({".flac"})), "these are music files"),
    (CleanupOptions(junk_extensions=frozenset({".m3u8"})), "these are playlists"),
    (CleanupOptions(junk_extensions=frozenset({".jpg"})), "these are pictures"),
    (CleanupOptions(junk_extensions=frozenset({".mkv"})), "these are videos"),
    (CleanupOptions(video_extensions=frozenset({".mkv", ".mp4"})), ".mp4 can't be a video: these are music files"),
])
def test_validation(options, message):
    assert any(message in problem for problem in validate(options)), validate(options)


def test_defaults_are_valid_and_saved_options_come_back():
    assert validate(CleanupOptions()) == []
    options = CleanupOptions(recursive=False, junk=False, junk_extensions=frozenset({".nfo"}), pictures=True,
                             videos=True, clutter_folders=True)
    assert options_from_json(options_to_json(options)) == options
    assert options_from_json({"junk": "yes", "junk_extensions": [1]}) == CleanupOptions()  # bad values: defaults
