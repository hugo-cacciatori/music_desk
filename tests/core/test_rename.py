"""core/rename.py: validation, the plan (dry run) and apply, on the album/ and cleanup/ fixtures (SPEC §10)."""

import functools
import os
import shutil

import pytest

from music_desk.core.cancel import CancelToken
from music_desk.core.fsops import send_to_recycle_bin
from music_desk.core.rename import RenameOptions, Status, apply_plan, plan_renames, sanitize, validate

ALBUM = "album/Fixture Artist - Fixture Album"


class Recorder:
    """Stands in for the Recycle Bin: tests never send anything to Hugo's real one."""

    def __init__(self):
        self.paths = []

    def __call__(self, path):
        self.paths.append(path)


def statuses(plan) -> dict[str, tuple[Status, str | None]]:
    return {item.source.relative_to(plan.folder).as_posix(): (item.status, item.target.name if item.target else None)
            for item in plan.items}


def names(folder) -> set[str]:
    return {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

def test_defaults_are_valid():
    assert validate(RenameOptions()) == []


@pytest.mark.parametrize(("options", "message"), [
    (RenameOptions(masks=()), "No rename mask"),
    (RenameOptions(masks=("{bpm}",)), "unknown field(s) bpm"),
    (RenameOptions(masks=("fixed",)), "uses no field"),
    (RenameOptions(masks=("{track} / {title}",)), "not allowed in file names"),
    (RenameOptions(masks=("{title:d}",)), "invalid format"),
    (RenameOptions(masks=("{title",)), "mask '{title'"),
    (RenameOptions(junk_extensions=frozenset({"nfo"})), "must be lowercase and start with a dot"),
    (RenameOptions(junk_extensions=frozenset({".flac"})), "these are music files"),
    (RenameOptions(junk_extensions=frozenset({".m3u8"})), "these are playlists"),
    (RenameOptions(junk_extensions=frozenset({".jpg"})), "these are cover images"),
    (RenameOptions(replacement=":"), "replacement contains a character"),
])
def test_validate_reports(options, message):
    assert any(message in problem for problem in validate(options)), validate(options)


def test_sanitize_matches_the_script():
    assert sanitize('a/b\\c?d%e*f:g|h"i<j>k\x07l\x7f') == "a-b-c-d-e-f-g-h-i-j-k-l-"
    assert sanitize("Five: A/B?", "_") == "Five_ A_B_"


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

def test_album_plan_statuses(fixture_copy):
    folder = fixture_copy(ALBUM)
    before = names(folder)
    plan = plan_renames(folder)
    assert names(folder) == before  # the plan touches nothing
    assert statuses(plan) == {
        "01 First.flac": (Status.UNCHANGED, None),
        "bad_chars.flac": (Status.RENAME, "05 Five- A-B-.flac"),
        "bonus/01 Bonus.flac": (Status.UNCHANGED, None),
        "CD2/01 Disc Two.flac": (Status.UNCHANGED, None),
        "clash.flac": (Status.NAME_TAKEN, "01 First.flac"),
        "lite/01 First.flac": (Status.UNCHANGED, None),
        "lite/02 Second.flac": (Status.UNCHANGED, None),
        "lowercase_rg.mp3": (Status.RENAME, "04 Fourth.mp3"),
        "missing_title.flac": (Status.MISSING_TAGS, None),
        "no_track.mp3": (Status.RENAME, "Loose.mp3"),
        "track2.flac": (Status.RENAME, "02 Second.flac"),
        "track6.FLAC": (Status.RENAME, "06 Sixth.flac"),
    }
    by_name = {item.source.name: item for item in plan.items}
    assert by_name["missing_title.flac"].detail == "title"
    assert by_name["no_track.mp3"].fallback_missing == "track"


def test_not_recursive(fixture_copy):
    plan = plan_renames(fixture_copy(ALBUM), RenameOptions(recursive=False))
    assert not any("/" in name for name in statuses(plan))


def test_case_only_rename_is_not_a_clash(fixture_copy):
    folder = fixture_copy(ALBUM)
    (folder / "track2.flac").rename(folder / "02 second.flac")
    plan = plan_renames(folder, RenameOptions(recursive=False))
    assert statuses(plan)["02 second.flac"] == (Status.RENAME, "02 Second.flac")
    report = apply_plan(plan, recycle=Recorder())
    assert "02 Second.flac" in report.renamed
    assert "02 Second.flac" in os.listdir(folder)


def test_first_file_in_walk_order_wins_the_name(fixture_copy):
    """The script renamed as it walked: of two files with the same target, the first one met gets it."""
    folder = fixture_copy(ALBUM)
    shutil.copy2(folder / "track2.flac", folder / "aaa.flac")  # same tags, walked before track2.flac
    plan = plan_renames(folder, RenameOptions(recursive=False))
    result = statuses(plan)
    assert result["aaa.flac"] == (Status.RENAME, "02 Second.flac")
    assert result["track2.flac"] == (Status.NAME_TAKEN, "02 Second.flac")


def test_cleanup_plan(fixture_copy):
    folder = fixture_copy("cleanup")
    plan = plan_renames(folder, RenameOptions(cleanup=True))
    result = statuses(plan)
    assert result["single_file_album/Fixture Album.flac"] == (Status.KEPT, None)
    assert result["single_file_album/Fixture Album.cue"] == (Status.KEPT, None)
    for junk in ("per_track_cue/album.cue", "per_track_cue/missing.cue", "lrc/with_lyrics.lrc",
                 "single_file_album/info.nfo", "single_file_album/notes.txt", "single_file_album/checksums.sfv"):
        assert result[junk] == (Status.JUNK, None), junk
    for never in ("single_file_album/replaygain.log", "single_file_album/folder.jpg",
                  "single_file_album/playlist.m3u", "lrc/no_lyrics.lrc"):
        assert never not in result, never  # not junk (no_lyrics.lrc moves as a sidecar)

    by_name = {item.source.name: item for item in plan.items}
    assert by_name["Fixture Album.flac"].detail == "used by Fixture Album.cue"
    assert by_name["Fixture Album.cue"].detail == "needed to play Fixture Album.flac"
    sidecar = by_name["no_lyrics.flac"].sidecar
    assert (sidecar.source.name, sidecar.target.name, sidecar.status) == ("no_lyrics.lrc", "02 No Lyrics.lrc",
                                                                         Status.RENAME)
    assert sidecar.detail == "lyrics not in the tags yet"
    assert by_name["with_lyrics.flac"].sidecar is None  # its .lrc is junk


def test_protected_log_even_if_log_is_junk(fixture_copy):
    folder = fixture_copy("cleanup")
    plan = plan_renames(folder, RenameOptions(cleanup=True, junk_extensions=frozenset({".log", ".nfo"})))
    assert "single_file_album/replaygain.log" not in statuses(plan)
    assert statuses(plan)["single_file_album/info.nfo"] == (Status.JUNK, None)


def test_without_cleanup_sidecars_follow_and_nothing_is_junk(fixture_copy):
    plan = plan_renames(fixture_copy("cleanup"))
    assert not plan.with_status(Status.JUNK)
    by_name = {item.source.name: item for item in plan.items}
    assert by_name["with_lyrics.flac"].sidecar.target.name == "01 With Lyrics.lrc"
    assert by_name["Fixture Album.flac"].status is Status.KEPT  # still left out of the rename


def test_cue_warning_when_its_files_get_renamed(fixture_copy):
    folder = fixture_copy("cleanup") / "per_track_cue"
    (folder / "01 One.flac").rename(folder / "one.flac")
    cue = folder / "album.cue"
    cue.write_bytes(cue.read_bytes().replace(b'"01 One.flac"', b'"one.flac"'))
    plan = plan_renames(folder)
    assert plan.warnings == ["album.cue lists files that will be renamed: it won't match them anymore"]


def test_sidecar_target_taken(fixture_copy):
    folder = fixture_copy("cleanup") / "lrc"
    (folder / "02 No Lyrics.lrc").write_text("other")
    plan = plan_renames(folder)
    item = next(i for i in plan.items if i.source.name == "no_lyrics.flac")
    assert item.status is Status.RENAME
    assert item.sidecar.status is Status.NAME_TAKEN
    assert plan.warnings == ["no_lyrics.lrc stays: 02 No Lyrics.lrc already exists"]


def test_selection_plans_only_selected_files_and_no_junk(fixture_copy):
    folder = fixture_copy("cleanup") / "lrc"
    plan = plan_renames(folder, RenameOptions(cleanup=True), selection=[folder / "no_lyrics.flac"])
    assert statuses(plan) == {"no_lyrics.flac": (Status.RENAME, "02 No Lyrics.flac")}
    assert plan.items[0].sidecar.target.name == "02 No Lyrics.lrc"


def test_unreadable_file(fixture_copy):
    folder = fixture_copy(ALBUM)
    (folder / "broken.flac").write_bytes(b"not a flac")
    item = next(i for i in plan_renames(folder, RenameOptions(recursive=False)).items
                if i.source.name == "broken.flac")
    assert item.status is Status.UNREADABLE
    assert item.detail.startswith("could not read the tags")


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

def test_apply_album(fixture_copy):
    folder = fixture_copy(ALBUM)
    report = apply_plan(plan_renames(folder), recycle=Recorder())
    assert sorted(report.renamed) == ["02 Second.flac", "04 Fourth.mp3", "05 Five- A-B-.flac", "06 Sixth.flac",
                                      "Loose.mp3"]
    assert report.fallback == ["Loose.mp3 (missing: track)"]
    assert report.missing_tags == ["missing_title.flac (missing: title)"]
    assert report.name_taken == ["clash.flac -> 01 First.flac"]
    assert report.scanned == 12
    assert {"02 Second.flac", "04 Fourth.mp3", "05 Five- A-B-.flac", "06 Sixth.flac", "Loose.mp3",
            "clash.flac", "missing_title.flac"} <= set(os.listdir(folder))


def test_apply_cleanup_recycles_only_junk(fixture_copy):
    folder = fixture_copy("cleanup")
    recorder = Recorder()
    report = apply_plan(plan_renames(folder, RenameOptions(cleanup=True)), recycle=recorder)
    recycled = sorted(p.relative_to(folder).as_posix() for p in recorder.paths)
    assert recycled == ["lrc/with_lyrics.lrc", "per_track_cue/album.cue", "per_track_cue/missing.cue",
                        "single_file_album/checksums.sfv", "single_file_album/info.nfo",
                        "single_file_album/notes.txt"]
    assert sorted(report.junk) == sorted(p.rsplit("/", 1)[1] for p in recycled)
    assert report.sidecars_renamed == ["no_lyrics.lrc -> 02 No Lyrics.lrc"]
    assert (folder / "lrc" / "02 No Lyrics.lrc").exists()
    assert (folder / "lrc" / "with_lyrics.lrc").exists()  # the recorder didn't really recycle it
    assert any("used by Fixture Album.cue" in kept for kept in report.kept)


def test_apply_skips_files_changed_since_the_preview(fixture_copy):
    folder = fixture_copy(ALBUM)
    plan = plan_renames(folder, RenameOptions(recursive=False))
    with open(folder / "track2.flac", "ab") as f:
        f.write(b"\0")
    report = apply_plan(plan, recycle=Recorder())
    assert "track2.flac: changed since the preview, not renamed" in report.skipped
    assert (folder / "track2.flac").exists()


def test_apply_rechecks_the_target(fixture_copy):
    folder = fixture_copy(ALBUM)
    plan = plan_renames(folder, RenameOptions(recursive=False))
    (folder / "06 Sixth.flac").write_bytes(b"appeared after the preview")
    report = apply_plan(plan, recycle=Recorder())
    assert "track6.FLAC -> 06 Sixth.flac" in report.name_taken
    assert (folder / "track6.FLAC").exists()


def test_cancel_stops_between_items(fixture_copy):
    folder = fixture_copy(ALBUM)
    token = CancelToken()
    token.cancel()
    report = apply_plan(plan_renames(folder), recycle=Recorder(), cancel=token)
    assert report.cancelled and report.renamed == []
    assert (folder / "track2.flac").exists()


def test_junk_that_cant_be_recycled_is_kept_and_reported(fixture_copy):
    """Where Windows has no Recycle Bin (a network share, a removable drive), junk stays (SPEC §19)."""
    folder = fixture_copy("cleanup")
    sent = []
    recycle = functools.partial(send_to_recycle_bin, check=lambda _p: "X: is a network location", send=sent.append)
    report = apply_plan(plan_renames(folder, RenameOptions(cleanup=True)), recycle=recycle)
    assert sent == [] and report.junk == [] and report.recycled == []
    kept = [k for k in report.kept if "can't be sent to the Recycle Bin: X: is a network location" in k]
    assert sorted(k.split(" (kept:")[0] for k in kept) == ["album.cue", "checksums.sfv", "info.nfo", "missing.cue",
                                                           "notes.txt", "with_lyrics.lrc"]
    assert (folder / "single_file_album" / "notes.txt").exists() and report.errors == []
