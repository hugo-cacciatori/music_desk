"""Saving LRCLIB lyrics into tags: the Lyrics tab's "Save to file tags" and the auto-save setting (SPEC §8, §9).
LRCLIB is scripted; files are copies."""

import shutil
from datetime import date

import pytest
from lrclib_fake import ScriptedLrclib, SleepRecorder, http_error, no_jitter

from music_desk.core.index.lyrics_index import embed_candidates, record_lyrics
from music_desk.core.index.roots import add_root
from music_desk.core.index.scanner import Scanner
from music_desk.core.index.store import DbWriter, connect_read, path_of
from music_desk.core.lyrics import FOUND, LrclibClient, cache_key
from music_desk.core.path_locks import PathLocks
from music_desk.core.tags import TaggedFile
from music_desk.services.lyrics_embedder import AutoEmbedder
from music_desk.services.lyrics_service import LyricsService
from music_desk.services.tag_writer import TagWriter
from music_desk.ui.main_window import MainWindow
from music_desk.ui.side_panel import lyrics_tab as tab_module
from music_desk.ui.side_panel.lyrics_tab import LyricsTab

TODAY = date(2026, 10, 15)
ALBUM = "Fixture Artist - Fixture Album"
SYNCED = "[00:01.00]la la"
FOUND_TITLES = {"First", "Second", "Sixth", "Tagged", "Other", "Song"}


def answer(endpoint, params):
    """Found: the titles above ("Sixth" synced); the rest: not found."""
    title = params["track_name"]
    record = {"id": 42, "artistName": params["artist_name"], "trackName": title, "duration": 1,
              "instrumental": False, "plainLyrics": "la la", "syncedLyrics": SYNCED if title == "Sixth" else None}
    if endpoint == "search":  # tracks without an album skip /api/get
        return [record] if title in FOUND_TITLES else []
    if title not in FOUND_TITLES:
        raise http_error(404)
    return record


@pytest.fixture
def library(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    album = lib / "Fixture Artist" / ALBUM
    shutil.copytree(fixtures_dir / "album" / ALBUM, album)
    for name, title, lyrics in (("tagged.flac", "Tagged", "la la"), ("other.flac", "Other", "something else")):
        shutil.copy2(album / "01 First.flac", album / name)
        tagged = TaggedFile(album / name)
        tagged.set_fields({"title": title, "lyrics": lyrics})
        tagged.save()
    (lib / "Loose").mkdir()
    shutil.copy2(fixtures_dir / "formats" / "tone.aac", lib / "Loose" / "Some Band - Song.aac")  # no tags at all
    return lib


@pytest.fixture
def writer(tmp_path, library):
    with DbWriter(tmp_path / "db" / "library.db") as writer:
        Scanner(writer).scan_root(add_root(writer, library).root_id)
        yield writer


@pytest.fixture
def read(writer):
    conn = connect_read(writer.path)
    yield conn
    conn.close()


@pytest.fixture
def parts(qtbot, writer, read):
    """The lyrics service, a TagWriter that updates the index after each save (as the app's does), the tab."""
    scanner = Scanner(writer)
    locks = PathLocks()
    tags = TagWriter(locks, on_written=scanner.update_file)
    client = LrclibClient(urlopen=ScriptedLrclib(answer), sleep=SleepRecorder(), jitter=no_jitter)
    service = LyricsService(writer.path, writer, client=client, today=lambda: TODAY)
    tab = LyricsTab(lambda: read, service, tags)
    tab.today = lambda: TODAY
    qtbot.addWidget(tab)
    tab.show()
    embedders = []
    yield service, tags, tab, locks, embedders
    for embedder in embedders:
        embedder.close()
    service.stop()
    tags.close()


def entry(read, library, relative: str) -> int:
    for (i,) in read.execute("SELECT id FROM entries WHERE is_dir = 0"):
        if path_of(read, i) == library / relative:
            return i
    raise KeyError(relative)


def album_file(library, name: str):
    return library / "Fixture Artist" / ALBUM / name


def tag_lyrics(path) -> str | None:
    return TaggedFile(path).fields().lyrics


def show_found(qtbot, service, tab, entry_id):
    service.prioritize_track(entry_id)
    tab.show_track(entry_id)
    qtbot.waitUntil(lambda: tab.state == tab_module.FOUND_STATE, timeout=5000)


# -- Save to file tags --

def test_save_writes_the_synced_lyrics_into_an_empty_tag(qtbot, parts, read, library):
    service, _tags, tab, _locks, _ = parts
    sixth = entry(read, library, f"Fixture Artist/{ALBUM}/track6.FLAC")
    show_found(qtbot, service, tab, sixth)
    assert tab.save_button.isVisible() and tab.save_button.isEnabled()
    tab.save_button.click()
    qtbot.waitUntil(lambda: tab.save_status.text() == "Saved to the file's Lyrics tag.", timeout=5000)
    assert tag_lyrics(album_file(library, "track6.FLAC")) == SYNCED  # synced first, as the script's --embed
    tab.refresh()  # the index caught up through on_written
    assert not tab.save_button.isEnabled()
    assert tab.save_button.toolTip() == "The file's Lyrics tag already holds these lyrics."
    assert tab.note.text() == "This file's lyrics tag holds the same lyrics."


def test_save_is_disabled_when_the_tag_holds_the_same_lyrics(qtbot, parts, read, library):
    service, _tags, tab, _locks, _ = parts
    show_found(qtbot, service, tab, entry(read, library, f"Fixture Artist/{ALBUM}/tagged.flac"))
    assert not tab.save_button.isEnabled()


def test_replacing_different_lyrics_asks_first(qtbot, parts, read, library):
    service, _tags, tab, _locks, _ = parts
    other = album_file(library, "other.flac")
    show_found(qtbot, service, tab, entry(read, library, f"Fixture Artist/{ALBUM}/other.flac"))
    asked = []
    tab.confirm_replace_lyrics = lambda name: asked.append(name) or False
    tab.save()
    assert asked == ["other.flac"]
    qtbot.wait(200)
    assert tag_lyrics(other) == "something else"
    tab.confirm_replace_lyrics = lambda name: True
    tab.save()
    qtbot.waitUntil(lambda: tab.save_status.text() == "Saved to the file's Lyrics tag.", timeout=5000)
    assert tag_lyrics(other) == "la la"


def test_a_refusal_from_the_panel_is_shown_and_nothing_is_written(qtbot, parts, read, library):
    service, _tags, tab, _locks, _ = parts
    show_found(qtbot, service, tab, entry(read, library, f"Fixture Artist/{ALBUM}/01 First.flac"))
    tab.can_write = lambda _path: "This file has unsaved edits in the Metadata tab. Save or revert them first."
    tab.save()
    assert tab.save_status.text().startswith("This file has unsaved edits")
    qtbot.wait(200)
    assert tag_lyrics(album_file(library, "01 First.flac")) is None


def test_a_job_lock_disables_save(qtbot, parts, read, library):
    service, _tags, tab, locks, _ = parts
    show_found(qtbot, service, tab, entry(read, library, f"Fixture Artist/{ALBUM}/01 First.flac"))
    token = locks.acquire([library / "Fixture Artist"], "the Rename tool")
    qtbot.waitUntil(lambda: not tab.save_button.isEnabled())
    assert tab.save_button.toolTip() == "Read-only while the Rename tool is running."
    locks.release(token)
    qtbot.waitUntil(lambda: tab.save_button.isEnabled())


def test_a_format_without_tags_cant_be_saved_into(qtbot, parts, read, library):
    service, _tags, tab, _locks, _ = parts
    show_found(qtbot, service, tab, entry(read, library, "Loose/Some Band - Song.aac"))
    assert not tab.save_button.isEnabled()
    assert "no tags" in tab.save_button.toolTip()


# -- auto-save --

def wait_lookups_done(qtbot, service, read):
    qtbot.waitUntil(lambda: read.execute("SELECT count(*) FROM lyrics").fetchone()[0] >= len(FOUND_TITLES) and
                    service.state == "idle", timeout=10000)


def test_auto_save_fills_empty_tags_as_results_arrive(qtbot, parts, writer, read, library):
    service, tags, _tab, _locks, embedders = parts
    embedder = AutoEmbedder(writer.path, service, tags, enabled=True)
    embedders.append(embedder)
    saved = []
    embedder.embedded.connect(saved.append)
    service.start()
    wait_lookups_done(qtbot, service, read)
    first = [album_file(library, "01 First.flac"), album_file(library, "clash.flac"),
             album_file(library, "lite/01 First.flac")]
    qtbot.waitUntil(lambda: all(tag_lyrics(p) == "la la" for p in first), timeout=10000)
    qtbot.waitUntil(lambda: tag_lyrics(album_file(library, "track6.FLAC")) == SYNCED, timeout=10000)
    assert tag_lyrics(album_file(library, "other.flac")) == "something else"  # never replaced
    assert tag_lyrics(album_file(library, "bonus/01 Bonus.flac")) is None  # not found: nothing to save
    assert sum(saved) >= 5


def test_turning_auto_save_on_fills_tracks_found_earlier(qtbot, parts, writer, read, library):
    service, tags, _tab, _locks, embedders = parts
    embedder = AutoEmbedder(writer.path, service, tags, batch=2)
    embedders.append(embedder)
    service.start()
    wait_lookups_done(qtbot, service, read)
    assert tag_lyrics(album_file(library, "01 First.flac")) is None  # off: nothing written
    expected = len(embed_candidates(read))
    assert expected >= 5  # First ×3, Second ×2, Sixth
    saved = []
    embedder.embedded.connect(saved.append)
    embedder.set_enabled(True)
    qtbot.waitUntil(lambda: sum(saved) == expected, timeout=10000)  # in batches of 2
    assert not embed_candidates(read)  # the index followed (has_lyrics_tag)
    assert tag_lyrics(album_file(library, "lite/02 Second.flac")) == "la la"


def test_auto_save_skips_locked_files(qtbot, parts, writer, read, library):
    service, tags, _tab, locks, embedders = parts
    key = cache_key("Fixture Artist", "First")
    writer.submit(lambda conn: record_lyrics(conn, key, {"status": FOUND, "source": "lrclib", "lrclib_id": 1,
                                                         "plain": "la la", "synced": None,
                                                         "checked": TODAY.isoformat()})).result()
    locks.acquire([album_file(library, "lite")], "the ReplayGain tool")
    embedder = AutoEmbedder(writer.path, service, tags)
    embedders.append(embedder)
    saved = []
    embedder.embedded.connect(saved.append)
    embedder.set_enabled(True)
    qtbot.waitUntil(lambda: sum(saved) == 2, timeout=5000)
    assert tag_lyrics(album_file(library, "lite/01 First.flac")) is None


# -- in the window --

def test_window_lyrics_menu_and_metadata_follow(qtbot, service, library_dir):
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window.offline_action.setChecked(True)
    assert window.settings.lyrics_offline
    window.auto_save_action.setChecked(True)
    assert window.settings.lyrics_auto_save and window.embedder.enabled
    window.auto_save_action.setChecked(False)

    conn = service.conn
    album = next(i for (i,) in conn.execute("SELECT id FROM entries WHERE is_dir = 1")
                 if path_of(conn, i) == library_dir / "Artist" / "Album")
    window.open_folder(album)
    ids = {path_of(conn, i).name: i for (i,) in conn.execute("SELECT id FROM entries WHERE parent_id = ?", (album,))}
    key = conn.execute("SELECT lyrics_key FROM tracks WHERE entry_id = ?", (ids["Track 2.flac"],)).fetchone()[0]
    service.writer.submit(lambda c: record_lyrics(c, key, {"status": FOUND, "source": "lrclib", "lrclib_id": 9,
                                                           "plain": "la la", "synced": None,
                                                           "checked": "2026-10-15"})).result()
    window.file_view.select_ids([ids["Track 2.flac"]])
    lyrics, metadata = window.side_panel.lyrics, window.side_panel.metadata
    qtbot.waitUntil(lambda: lyrics.state == tab_module.FOUND_STATE and metadata.snapshot is not None, timeout=5000)

    metadata.edits["genre"].setText("edited")  # unsaved Metadata edits on this file: the save is refused
    metadata.edits["genre"].textEdited.emit("edited")
    assert metadata.dirty
    lyrics.save()
    assert lyrics.save_status.text().startswith("This file has unsaved edits in the Metadata tab.")
    metadata.revert()

    lyrics.save()
    qtbot.waitUntil(lambda: lyrics.save_status.text() == "Saved to the file's Lyrics tag.", timeout=5000)
    qtbot.waitUntil(lambda: metadata.snapshot is not None and metadata.snapshot.fields.lyrics == "la la",
                    timeout=5000)  # the Metadata tab shows the new tag
    qtbot.waitUntil(lambda: not lyrics.save_button.isEnabled(), timeout=5000)  # the index caught up
