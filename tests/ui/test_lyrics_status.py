"""The lyrics status bar label and button, and the LRCLIB column's service states (SPEC §5, §9 Controls)."""

import time

from refrain.core.index.store import path_of
from refrain.services.lyrics_service import IDLE, OFFLINE, PAUSED, RUNNING, UNREACHABLE, LyricsProgress
from refrain.ui.file_view import COLUMN_INDEX


def progress(state, pending=12):
    return LyricsProgress(state, done=3, errors=0, in_flight=1, pending=pending)


def test_status_bar_label_and_retry_button(window, service, monkeypatch):
    calls = []
    for name in ("pause", "resume", "retry_now"):
        monkeypatch.setattr(service.lyrics, name, lambda n=name: calls.append(n))
    label, button = window.lyrics_label, window.lyrics_button

    window.show_lyrics_progress(progress(RUNNING, 1234))
    assert label.text() == "Looking up lyrics: 1,234 to look up" and not button.isVisible()  # no Pause
    window.show_lyrics_progress(progress(PAUSED))
    assert label.text() == "Lyrics paused: 12 to look up" and not button.isVisible()  # no Resume
    monkeypatch.setattr(type(service.lyrics), "resume_at", property(lambda _self: time.time() + 600))
    window.show_lyrics_progress(progress(UNREACHABLE))
    assert label.text().startswith("LRCLIB unreachable, retrying at ") and button.text() == "Retry now"
    assert button.isVisible()
    button.click()
    assert calls == ["retry_now"]
    assert button.toolTip() == "Try reaching LRCLIB again now instead of waiting."  # SPEC §24

    window.show_lyrics_progress(progress(OFFLINE))
    assert label.text() == "Lyrics: lookups off" and not button.isVisible()
    window.show_lyrics_progress(progress(RUNNING, None))
    assert label.text() == "Looking up lyrics: starting"
    window.show_lyrics_progress(progress(IDLE, 2))
    assert label.text() == "Lyrics: 2 to retry later" and not button.isVisible()
    window.show_lyrics_progress(progress(IDLE, 0))
    assert not label.isVisible() and not button.isVisible()


def test_the_lrclib_column_shows_the_service_state_for_pending_tracks(qtbot, window, service, library_dir,
                                                                       monkeypatch):
    conn = service.conn
    album = next(i for (i,) in conn.execute("SELECT id FROM entries WHERE is_dir = 1")
                 if path_of(conn, i) == library_dir / "Artist" / "Album")
    window.open_folder(album)
    model = window.file_view.model
    row = next(i for i, r in enumerate(model.rows) if r.name == "Track 2.flac")
    cell = model.index(row, COLUMN_INDEX["lrclib"])
    assert cell.data() == "offline"  # UI test libraries are offline
    monkeypatch.setattr(service.lyrics, "status_for", lambda _key: "error")
    model.lrclib_status = service.lyrics.status_for
    changed = []
    model.dataChanged.connect(lambda top, bottom: changed.append((top.column(), bottom.column())))
    window._lyrics_entry("k", {"status": "error"})
    assert (COLUMN_INDEX["lrclib"], COLUMN_INDEX["lrclib"]) in changed
    assert cell.data() == "error"
    monkeypatch.setattr(service.lyrics, "status_for", lambda _key: None)
    model.lrclib_status = service.lyrics.status_for
    assert cell.data() == "pending"
