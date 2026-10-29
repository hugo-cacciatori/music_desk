"""core/playlists.py: temporary .m3u8 playlists (SPEC §14)."""

from datetime import datetime
from pathlib import Path

from refrain.core.playlists import (
    PlaylistEntry,
    PreviewSheet,
    PreviewTrack,
    folder_tracks,
    frames_from_samples,
    prune,
    render_preview,
    write_preview,
    write_temp_playlist,
)

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


# -- Preview in player: temporary cue sheets (SPEC §23) --

def preview(tracks, samples=44_100 * 10, title="Album", performer="Artist"):
    return PreviewSheet(Path(r"C:\Music\A\Song.flac"), 44_100, samples, tracks, title, performer)


def test_preview_sheet_file(tmp_path):
    sheet = preview([PreviewTrack(0, "Cut from the start", "Artist"),
                     PreviewTrack(44_100 * 2 + 300, 'Say "Hi"  (kept)', ""),
                     PreviewTrack(44_100 * 9, "Cut from the end")], title="", performer="Ärtist")
    path = write_preview(sheet, tmp_path, now=clock)
    assert path == tmp_path / "preview-20261015-123005.cue"
    data = path.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")
    assert data[3:].decode("utf-8") == (
        'PERFORMER "Ärtist"\r\n'
        'FILE "C:\\Music\\A\\Song.flac" WAVE\r\n'
        '  TRACK 01 AUDIO\r\n    TITLE "Cut from the start"\r\n    PERFORMER "Artist"\r\n    INDEX 01 00:00:00\r\n'
        "  TRACK 02 AUDIO\r\n    TITLE \"Say 'Hi' (kept)\"\r\n    INDEX 01 00:02:01\r\n"  # 300 samples: 0.51 frame
        '  TRACK 03 AUDIO\r\n    TITLE "Cut from the end"\r\n    INDEX 01 00:09:00\r\n')


def test_frames_round_to_the_nearest():
    assert [frames_from_samples(s, 44_100) for s in (0, 293, 294, 587, 588, 44_100)] == [0, 0, 1, 1, 1, 75]
    assert [frames_from_samples(s, 96_000) for s in (639, 640, 1919, 1920)] == [0, 1, 1, 2]  # 1280 a frame


def test_tracks_on_the_same_frame_or_at_the_end_are_left_out():
    text = render_preview(preview([PreviewTrack(0, "a"), PreviewTrack(100, "b"), PreviewTrack(44_100, "c"),
                                   PreviewTrack(44_100 * 10 - 50, "d")]))
    assert [line.strip() for line in text.splitlines() if "TITLE" in line] == ['TITLE "Album"', 'TITLE "b"',
                                                                                'TITLE "c"']


def test_preview_sheets_and_playlists_are_pruned_apart(tmp_path):
    for minute in range(25):
        (tmp_path / f"preview-20261015-12{minute:02d}00.cue").write_text("")
        (tmp_path / f"play-20261015-12{minute:02d}00.m3u8").write_text("")
    (tmp_path / "preview-notes.txt").write_text("not a preview")
    (tmp_path / "mine.cue").write_text("not a preview")
    newest = write_preview(preview([PreviewTrack(0)]), tmp_path, now=lambda: datetime(2026, 10, 15, 13, 0, 0))
    previews = sorted(p.name for p in tmp_path.glob("preview-*.cue"))
    assert len(previews) == 20 and newest.name in previews and previews[0] == "preview-20261015-120600.cue"
    assert len(list(tmp_path.glob("play-*.m3u8"))) == 25
    assert (tmp_path / "preview-notes.txt").exists() and (tmp_path / "mine.cue").exists()
    write_temp_playlist([], tmp_path, now=clock)
    assert len(list(tmp_path.glob("play-*.m3u8"))) == 20 and len(list(tmp_path.glob("preview-*.cue"))) == 20


def test_preview_names_in_the_same_second(tmp_path):
    names = [write_preview(preview([PreviewTrack(0)]), tmp_path, now=clock).name for _ in range(2)]
    assert names == ["preview-20261015-123005.cue", "preview-20261015-123005-2.cue"]
