"""core/playlists.py: temporary .m3u8 playlists (SPEC §14)."""

from datetime import datetime
from pathlib import Path

from music_desk.core.playlists import PlaylistEntry, entry_for, folder_tracks, prune, write_temp_playlist

NOW = datetime(2026, 10, 15, 12, 30, 5)


def clock() -> datetime:
    return NOW


def test_file_text(tmp_path):
    entries = [
        PlaylistEntry(Path(r"C:\Music\A\01 Song.flac"), 200.6, "Artist", "Song"),
        PlaylistEntry(Path(r"C:\Music\A\02 Ünïcödé.mp3"), None, None, "Ünïcödé"),
        PlaylistEntry(Path(r"C:\Music\A\03 Untagged.opus"), 61.2, "Artist", "  "),
        PlaylistEntry(Path(r"C:\Music\A\folder.jpg")),
        PlaylistEntry(Path(r"C:\Music\A\notes.txt"), 1, "x", "y"),
    ]
    path = write_temp_playlist(entries, tmp_path, now=clock)
    assert path == tmp_path / "play-20261015-123005.m3u8"
    assert path.read_bytes().decode("utf-8") == (
        "#EXTM3U\r\n"
        "#EXTINF:201,Artist - Song\r\n"
        "C:\\Music\\A\\01 Song.flac\r\n"
        "#EXTINF:-1,Ünïcödé\r\n"
        "C:\\Music\\A\\02 Ünïcödé.mp3\r\n"
        "#EXTINF:61,03 Untagged\r\n"
        "C:\\Music\\A\\03 Untagged.opus\r\n"
    )


def test_unique_names_in_the_same_second(tmp_path):
    names = [write_temp_playlist([], tmp_path, now=clock).name for _ in range(3)]
    assert names == ["play-20261015-123005.m3u8", "play-20261015-123005-2.m3u8", "play-20261015-123005-3.m3u8"]


def test_keeps_the_newest_twenty(tmp_path):
    for minute in range(25):
        (tmp_path / f"play-20261015-12{minute:02d}00.m3u8").write_text("#EXTM3U\n")
    (tmp_path / "mine.m3u8").write_text("not a temporary playlist")
    (tmp_path / "play-notes.txt").write_text("not a playlist")
    newest = write_temp_playlist([], tmp_path, now=lambda: datetime(2026, 10, 15, 13, 0, 0))
    left = sorted(p.name for p in tmp_path.glob("play-*.m3u8"))
    assert len(left) == 20
    assert newest.name in left
    assert left[0] == "play-20261015-120600.m3u8"
    assert (tmp_path / "mine.m3u8").exists() and (tmp_path / "play-notes.txt").exists()


def test_prune_orders_counters_naturally(tmp_path):
    for name in ("play-20261015-120000-10.m3u8", "play-20261015-120000-2.m3u8", "play-20261015-120000.m3u8"):
        (tmp_path / name).write_text("")
    prune(tmp_path, keep=1)
    assert [p.name for p in tmp_path.iterdir()] == ["play-20261015-120000-10.m3u8"]


def test_folder_tracks_natural_order(tmp_path):
    for relative in ("Track 10.flac", "Track 2.mp3", "track 1.FLAC", "cover.jpg", "notes.txt",
                     "CD 10/01.flac", "CD 2/01.flac", "CD 2/sub/01.flac", "Scans/front.jpg"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    assert [p.name for p in folder_tracks(tmp_path)] == ["track 1.FLAC", "Track 2.mp3", "Track 10.flac"]
    assert [p.relative_to(tmp_path).as_posix() for p in folder_tracks(tmp_path, recursive=True)] == [
        "track 1.FLAC", "Track 2.mp3", "Track 10.flac", "CD 2/01.flac", "CD 2/sub/01.flac", "CD 10/01.flac"]


def test_entry_for_reads_tags(fixtures_dir):
    entry = entry_for(fixtures_dir / "formats/tone.flac")
    assert (entry.artist, entry.title, entry.label) == ("Fixture Artist", "Tone tone.flac",
                                                        "Fixture Artist - Tone tone.flac")
    assert 0.5 < entry.duration < 1.5


def test_entry_for_unreadable_file(tmp_path):
    path = tmp_path / "broken.flac"
    path.write_bytes(b"not audio")
    assert entry_for(path) == PlaylistEntry(path)
    assert entry_for(path).label == "broken"
