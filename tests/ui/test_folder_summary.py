"""The folder summary in the side panel (SPEC §7): numbers from the index, the cover, Play, and refreshes."""

import shutil

from browser_helpers import entry

from music_desk.core.index import queries
from music_desk.core.tags import TaggedFile


def stats(service, folder_id):
    return service.conn.execute(
        "SELECT n_tracks, duration, formats, n_with_rg, n_with_lyrics_tag FROM folder_stats WHERE entry_id = ?",
        (folder_id,)).fetchone()


# -- the query --

def test_summary_matches_folder_stats(service, library_dir):
    album = entry(service, "Artist", "Album")
    summary = queries.folder_summary(service.conn, album)
    n_tracks, duration, formats, n_rg, n_lyrics = stats(service, album)
    assert (summary.n_tracks, summary.formats, summary.n_with_rg, summary.n_with_lyrics_tag) == (
        n_tracks, formats, n_rg, n_lyrics)
    assert summary.n_tracks == 4 and summary.formats == "FLAC, MP3" and abs(summary.duration - duration) < 1e-6
    assert (summary.n_subfolders, summary.n_files) == (2, 5)
    assert summary.first_track == library_dir / "Artist" / "Album" / "track 1.flac"
    assert summary.path == library_dir / "Artist" / "Album"


def test_first_track_walks_down_level_by_level(service, library_dir):
    artist = queries.folder_summary(service.conn, entry(service, "Artist"))
    assert artist.n_tracks == 4 and (artist.n_subfolders, artist.n_files) == (2, 0)
    assert artist.first_track == library_dir / "Artist" / "Album" / "track 1.flac"
    scans = queries.folder_summary(service.conn, entry(service, "Artist", "Scans"))
    assert scans.n_tracks == 0 and scans.first_track is None and scans.formats is None
    assert queries.folder_summary(service.conn, entry(service, "Artist", "Album", "cover.jpg")) is None  # a file


# -- the panel --

def test_open_folder_shows_its_summary(qtbot, window, service, library_dir):
    window.open_folder(entry(service, "Artist", "Album"))
    panel = window.side_panel
    assert panel.showing_folder == entry(service, "Artist", "Album")
    view = panel.summary
    assert view.name_label.text() == "Album" and view.path_label.text() == str(library_dir / "Artist" / "Album")
    assert view.values["tracks"].text() == "4" and view.values["formats"].text() == "FLAC, MP3"
    assert view.values["rg"].text() == "0 of 4 tracks" and view.values["lyrics"].text() == "0 of 4 tracks"
    assert view.values["contents"].text() == "2 subfolders, 5 files"
    assert view.play_button.isEnabled() and view.play_all_button.isEnabled()
    qtbot.wait(300)  # the test library's cover.jpg isn't a real picture: no cover is shown
    assert view.cover.text() == "No cover" and view.cover_source.text() == ""


def test_folder_image_as_cover(qtbot, window, service, library_dir, fixtures_dir):
    shutil.copy2(fixtures_dir / "cover.jpg", library_dir / "Artist" / "Album" / "cover.jpg")
    window.open_folder(entry(service, "Artist", "Album"))
    view = window.side_panel.summary
    qtbot.waitUntil(lambda: view.cover_source.text() == "Cover from cover.jpg", timeout=5000)
    assert not view.cover.pixmap().isNull()


def test_selecting_a_folder_row_and_back(qtbot, window, service):
    window.open_folder(entry(service, "Artist"))
    window.file_view.select_ids([entry(service, "Artist", "Scans")])
    view = window.side_panel.summary
    assert window.side_panel.showing_folder == entry(service, "Artist", "Scans")
    assert view.values["tracks"].text() == "No tracks" and view.values["contents"].text() == "1 file"
    assert not view.play_button.isEnabled() and not view.play_all_button.isEnabled()
    window.file_view.select_ids([])  # nothing selected: the open folder again
    assert window.side_panel.showing_folder == entry(service, "Artist")
    assert not window.side_panel.summary.play_button.isEnabled()  # no tracks of its own
    assert window.side_panel.summary.play_all_button.isEnabled()


def test_a_file_selection_shows_the_tabs(qtbot, window, service):
    window.open_folder(entry(service, "Artist", "Album"))
    window.file_view.select_ids([entry(service, "Artist", "Album", "Track 2.flac")])
    assert window.side_panel.showing_folder is None
    assert window.side_panel.stack.currentWidget() is window.side_panel.tabs


def test_cover_from_the_first_track(qtbot, window, service, library_dir, fixtures_dir):
    folder = library_dir / "Other" / "With cover"
    folder.mkdir()
    shutil.copy2(fixtures_dir / "edge" / "edge.flac", folder / "01 edge.flac")
    service.indexer.scan_path(str(library_dir / "Other"))
    qtbot.waitUntil(lambda: service.conn.execute("SELECT count(*) FROM entries WHERE name = '01 edge.flac'"
                                                 ).fetchone()[0] == 1, timeout=10000)
    window.open_folder(entry(service, "Other", "With cover"))
    qtbot.waitUntil(lambda: window.side_panel.summary.cover_source.text() == "Cover from 01 edge.flac",
                    timeout=5000)
    assert not window.side_panel.summary.cover.pixmap().isNull()


def test_play_buttons(qtbot, window, service, tmp_path):
    played = []
    window.launch = played.append
    window.playlist_dir = tmp_path / "playlists"
    window.open_folder(entry(service, "Artist"))
    window.side_panel.summary.play_all_button.click()
    qtbot.waitUntil(lambda: len(played) == 1, timeout=3000)
    lines = open(played[0], encoding="utf-8").read().splitlines()
    assert len(lines[1::2]) == 4  # the four tracks of the subtree


def test_summary_follows_the_index(qtbot, window, service, library_dir):
    album = entry(service, "Artist", "Album")
    window.open_folder(album)
    track = library_dir / "Artist" / "Album" / "Track 2.flac"
    tagged = TaggedFile(track)
    tagged.set_fields({"lyrics": "words"})
    tagged.save()
    service.indexer.update_file(str(track))
    qtbot.waitUntil(lambda: window.side_panel.summary.values["lyrics"].text() == "1 of 4 tracks", timeout=10000)
