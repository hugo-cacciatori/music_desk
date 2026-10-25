"""The right-click Remove ReplayGain tags with MusicDesk open (SPEC §31, §35): the clicked tracks only, after the
app's question; a report window of its own says what was done and stays until it is closed."""

import shutil

import pytest

from music_desk.context_menu import request as menu
from music_desk.context_menu.remove_entry import RemoveEntry
from music_desk.context_menu.remove_report import RemoveReport
from music_desk.core.replaygain import ALBUM_GAIN, TRACK_GAIN
from music_desk.core.tags import TaggedFile


def tag(path):
    tagged = TaggedFile(path)
    tagged.set_replaygain(TRACK_GAIN, "-3.00 dB")
    tagged.set_replaygain(ALBUM_GAIN, "-3.50 dB")
    tagged.set_comment("mine\nLUFS Target : -15")
    tagged.save()


@pytest.fixture
def library_dir(tmp_path, fixtures_dir):
    lib = tmp_path / "lib"
    album = lib / "Artist" / "Album"
    album.mkdir(parents=True)
    for name in ("01 One.flac", "02 Two.mp3", "03 Three.flac"):
        shutil.copy2(fixtures_dir / "formats" / ("tone.mp3" if name.endswith(".mp3") else "tone.flac"), album / name)
        tag(album / name)
    shutil.copy2(fixtures_dir / "formats" / "tone.flac", album / "04 Plain.flac")  # no ReplayGain at all
    (album / "cover.jpg").write_bytes(b"jpg")
    return lib


@pytest.fixture
def album(library_dir):
    return library_dir / "Artist" / "Album"


@pytest.fixture
def asked(monkeypatch):
    questions = []

    def answer(_entry, count, where, comment):
        questions.append((count, where, comment))
        return answer.reply
    answer.reply = True
    monkeypatch.setattr(RemoveEntry, "ask_remove_replaygain", answer)
    return answer, questions


def has_rg(path) -> bool:
    return TaggedFile(path).get_replaygain(TRACK_GAIN) is not None


def remove(qtbot, open_app, paths) -> RemoveReport:
    open_app.run_entry(menu.REMOVE_REPLAYGAIN, [str(p) for p in paths])
    entry = open_app.entry_windows[-1]
    assert isinstance(entry, RemoveEntry) and entry.report is not None
    report = entry.report
    qtbot.addWidget(report)
    qtbot.waitUntil(lambda: not report.message.text().startswith("Removing"), timeout=10000)
    return report


def test_only_the_clicked_tracks(qtbot, open_app, album, asked):
    _answer, questions = asked
    report = remove(qtbot, open_app, [album / "01 One.flac", album / "02 Two.mp3", album / "04 Plain.flac"])
    assert questions == [(3, str(album), "LUFS Target : {target}")]
    assert report.parent() is None  # its own window, beside the main one (SPEC §35)
    assert report.message.text() == "Removed the ReplayGain tags of 2 tracks. 1 track had none."
    assert not report.details.isVisible()
    assert not has_rg(album / "01 One.flac") and not has_rg(album / "02 Two.mp3")
    assert TaggedFile(album / "01 One.flac").fields().comment == "mine"  # other comment lines stay
    assert has_rg(album / "03 Three.flac")  # not clicked
    report.close_button.click()
    assert not report.isVisible() and open_app.entry_windows == []


def test_cancelling_the_question_changes_nothing(qtbot, open_app, album, asked):
    answer, _questions = asked
    answer.reply = False
    open_app.run_entry(menu.REMOVE_REPLAYGAIN, [str(album / "01 One.flac")])
    assert has_rg(album / "01 One.flac") and open_app.entry_windows == []


def test_a_file_in_use_is_reported_with_why(qtbot, open_app, service, album, asked):
    lock = service.locks.acquire([album / "02 Two.mp3"], "ReplayGain")
    report = remove(qtbot, open_app, [album / "01 One.flac", album / "02 Two.mp3"])
    service.locks.release(lock)
    assert report.message.text() == ("Removed the ReplayGain tags of 1 track. 1 file couldn't be changed: see "
                                     "below.")
    assert report.details.toPlainText() == "02 Two.mp3 is busy: ReplayGain is running on it."
    assert has_rg(album / "02 Two.mp3")


def test_other_files_are_left_out(qtbot, open_app, album, asked):
    _answer, questions = asked
    report = remove(qtbot, open_app, [album / "01 One.flac", album / "cover.jpg"])
    # from the command line: the menu doesn't offer it on such a selection
    assert "1 file left out" in report.message.text() and report.details.toPlainText() == "cover.jpg: left out"
    nothing = remove(qtbot, open_app, [album / "cover.jpg", album])
    assert len(questions) == 1 and nothing.message.text().startswith("Nothing was done")
