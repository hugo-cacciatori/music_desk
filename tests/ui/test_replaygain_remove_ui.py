"""Removing ReplayGain tags from the window (SPEC §10 B, Hugo 2026-10-16): a folder (subfolders included) or a
selection, after a confirmation, through the TagWriter; the index and the Metadata tab follow; locks refuse."""

import shutil

import pytest
from browser_helpers import column, entry

from music_desk.core.replaygain import ALBUM_GAIN, TRACK_GAIN
from music_desk.core.tags import TaggedFile


def tag(path, comment="LUFS Target : -15"):
    tagged = TaggedFile(path)
    tagged.set_replaygain(TRACK_GAIN, "-3.00 dB")
    tagged.set_replaygain("replaygain_track_peak", "0.900000")
    tagged.set_replaygain(ALBUM_GAIN, "-3.50 dB")
    tagged.set_replaygain("replaygain_album_peak", "0.950000")
    tagged.set_comment(comment)
    tagged.save()


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    album = lib / "Artist" / "Album"
    (album / "CD2").mkdir(parents=True)
    for name in ("01 One.flac", "02 Two.flac"):
        shutil.copy2(fixtures_dir / "formats" / "tone.flac", album / name)
        tag(album / name)
    shutil.copy2(fixtures_dir / "formats" / "tone.mp3", album / "CD2" / "01 Three.mp3")
    tag(album / "CD2" / "01 Three.mp3", comment="mine\nLUFS Target : -15")
    shutil.copy2(fixtures_dir / "formats" / "tone.mp3", album / "untouched.mp3")  # no ReplayGain at all
    return lib


@pytest.fixture
def asked(window):
    questions = []

    def answer(count, where, comment):
        questions.append((count, where, comment))
        return answer.reply
    answer.reply = True
    window.tools.ask_remove_replaygain = answer
    return answer, questions


def has_rg(path) -> bool:
    return TaggedFile(path).get_replaygain(TRACK_GAIN) is not None


def test_a_folder_and_its_subfolders(qtbot, window, service, library_dir, asked):
    answer, questions = asked
    album = library_dir / "Artist" / "Album"
    window.open_folder(entry(service, "Artist", "Album"))
    window.tools.remove_replaygain([entry(service, "Artist", "Album")])
    assert questions == [(4, f"{album} and its subfolders", "LUFS Target : {target}")]
    done = "Removed the ReplayGain tags of 3 files. 1 had none."
    qtbot.waitUntil(lambda: done in window.statusBar().currentMessage(), timeout=10000)
    for path in (album / "01 One.flac", album / "02 Two.flac", album / "CD2" / "01 Three.mp3"):
        assert not has_rg(path) and TaggedFile(path).get_replaygain(ALBUM_GAIN) is None
    assert TaggedFile(album / "01 One.flac").fields().comment is None
    assert TaggedFile(album / "CD2" / "01 Three.mp3").fields().comment == "mine"  # the user's line stays
    # the index follows: the RG column shows none
    qtbot.waitUntil(lambda: set(column(window, "rg")[3:]) == {"–"}, timeout=10000)


def test_cancel_changes_nothing(qtbot, window, service, library_dir, asked):
    answer, _questions = asked
    answer.reply = False
    window.tools.remove_replaygain([entry(service, "Artist", "Album")])
    qtbot.wait(300)
    assert has_rg(library_dir / "Artist" / "Album" / "01 One.flac")


def test_a_selection_of_tracks(qtbot, window, service, library_dir, asked):
    _answer, questions = asked
    album = library_dir / "Artist" / "Album"
    window.open_folder(entry(service, "Artist", "Album"))
    one = entry(service, "Artist", "Album", "01 One.flac")
    window.tools.remove_replaygain([one])
    assert questions[0][:2] == (1, "the selection")
    qtbot.waitUntil(lambda: not has_rg(album / "01 One.flac"), timeout=10000)
    assert has_rg(album / "02 Two.flac")  # only the selection


def test_a_job_lock_refuses_and_says_so(qtbot, window, service, library_dir, asked):
    album = library_dir / "Artist" / "Album"
    token = service.locks.acquire([album / "02 Two.flac"], "the ReplayGain tool")
    window.tools.remove_replaygain([entry(service, "Artist", "Album")])
    qtbot.waitUntil(lambda: "is in use by the ReplayGain tool" in window.toast.label.text(), timeout=10000)
    service.locks.release(token)
    assert has_rg(album / "02 Two.flac") and not has_rg(album / "01 One.flac")


def test_the_metadata_tab_follows(qtbot, window, service, library_dir, asked):
    window.open_folder(entry(service, "Artist", "Album"))
    one = entry(service, "Artist", "Album", "01 One.flac")
    window.file_view.select_ids([one])
    tab = window.side_panel.metadata
    qtbot.waitUntil(lambda: tab.snapshot is not None and bool(tab.snapshot.replaygain.get(TRACK_GAIN)),
                    timeout=10000)
    window.tools.remove_replaygain([one])
    qtbot.waitUntil(lambda: tab.snapshot is not None and not tab.snapshot.replaygain.get(TRACK_GAIN), timeout=10000)
