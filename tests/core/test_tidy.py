"""core/tidy.py (SPEC §28): finding the albums of a tree, sorting each folder, the ReplayGain check and the plan's
summary, over trees built from the split and format fixtures."""

import shutil
from pathlib import Path

import pytest

from music_desk.core import rename
from music_desk.core.masks import DEFAULT_MASKS, DEFAULT_REPLACEMENT
from music_desk.core.replaygain import ALBUM_GAIN, ALBUM_PEAK, TRACK_GAIN, TRACK_PEAK, ReplayGainOptions
from music_desk.core.tags import TaggedFile
from music_desk.core.tidy import Kind, find_albums, plan_tidy, plan_tidy_paths, replaygain_needed

RG = ReplayGainOptions()
RENAME = rename.RenameOptions()


def tidy(root, rg=RG):
    return plan_tidy(root, RENAME, rg, DEFAULT_MASKS, DEFAULT_REPLACEMENT)


def album_at(plan, folder):
    return next(album for album in plan.albums if album.folder == folder)


@pytest.fixture
def split_fixtures(fixtures_dir) -> Path:
    return fixtures_dir / "split"


def tagged_track(folder: Path, source: Path, title: str, number: int, *, target: str | None = "-15",
                 values: bool = True) -> Path:
    """A FLAC track named by the default masks, with ReplayGain values and the comment for `target`."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{number:02} {title}.flac"
    shutil.copy2(source, path)
    tagged = TaggedFile(path)
    tagged.set_fields({"title": title, "track": number})
    if values:
        for key, value in ((TRACK_GAIN, "-6.00 dB"), (TRACK_PEAK, "0.9"), (ALBUM_GAIN, "-6.50 dB"),
                           (ALBUM_PEAK, "0.95")):
            tagged.set_replaygain(key, value)
    if target is not None:
        tagged.set_comment(f"LUFS Target : {target}")
    tagged.save()
    return path


@pytest.fixture
def tone(fixtures_dir) -> Path:
    return fixtures_dir / "formats" / "tone.flac"


# -- finding the albums --

def test_albums_and_their_disc_folders(tmp_path, split_fixtures, tone):
    album = tmp_path / "Artist" / "Album"
    tagged_track(album, tone, "One", 1)
    for sub in ("CD2", "lite", "Bonus"):
        shutil.copytree(split_fixtures / "wav_image", album / sub)
    shutil.copytree(split_fixtures / "wav_image", tmp_path / "Artist" / "Rip")
    assert find_albums(tmp_path, RG) == [(album, [album / "CD2"]), (tmp_path / "Artist" / "Rip", [])]


def test_a_folder_without_music_above_discs_makes_each_disc_an_album(tmp_path, split_fixtures):
    for sub in ("CD1", "CD2"):
        shutil.copytree(split_fixtures / "wav_image", tmp_path / "Box" / sub)
    assert [folder.name for folder, _ in find_albums(tmp_path, RG)] == ["CD1", "CD2"]


# -- sorting a folder --

def test_not_split_is_split_then_replaygained(tmp_path, split_fixtures):
    folder = tmp_path / "Rip"
    shutil.copytree(split_fixtures / "wav_image", folder)
    plan = tidy(tmp_path)
    [album] = plan.albums
    assert album.own.kind is Kind.NOT_SPLIT and album.own.split is not None
    assert [t.name for t in album.own.split.written] == ["01 One.flac", "02 Two.flac"]
    assert album.own.offer == [folder / "image.wav", folder / "image.cue"]
    assert album.replaygain == "after the split" and album.replaygain_exclude == {"image.wav"}
    assert plan.has_work and plan.summary() == "1 album: 1 to split, 1 to ReplayGain"


@pytest.mark.parametrize(("fixture", "reason"), [
    ("hidden_track", "hidden track"),
    ("data_track", "data track"),
    ("too_long", ""),
])
def test_sheets_that_need_a_human_are_skipped(tmp_path, split_fixtures, fixture, reason):
    shutil.copytree(split_fixtures / fixture, tmp_path / fixture)
    [album] = tidy(tmp_path).albums
    assert album.own.kind is Kind.SKIPPED and reason in album.own.skip
    assert album.own.skip.endswith("open its .cue to split it by hand")
    assert album.replaygain is None and not album.own.offer  # nothing measured, nothing offered


def test_a_windows_1252_sheet_is_split(tmp_path, split_fixtures):
    """What EAC writes on a Western Windows: no BOM, not UTF-8. Read as Windows-1252, not a reason to stop."""
    folder = tmp_path / "Rip"
    shutil.copytree(split_fixtures / "wav_image", folder)
    sheet = (folder / "image.cue").read_text(encoding="utf-8").replace('TITLE "One"', 'TITLE "Café"')
    (folder / "image.cue").write_bytes(sheet.encode("cp1252"))
    [album] = tidy(tmp_path).albums
    assert album.own.kind is Kind.NOT_SPLIT and album.own.split is not None
    assert album.own.split.written[0].name == "01 Café.flac"


def test_a_sheet_that_cant_be_windows_1252_is_skipped(tmp_path, split_fixtures):
    folder = tmp_path / "Rip"
    shutil.copytree(split_fixtures / "wav_image", folder)
    sheet = (folder / "image.cue").read_bytes().replace(b'TITLE "One"', 'TITLE "さくら、花"'.encode("shift_jis"))
    assert b"\x81" in sheet  # a byte Windows-1252 has no character for
    (folder / "image.cue").write_bytes(sheet)
    [album] = tidy(tmp_path).albums
    assert album.own.kind is Kind.SKIPPED and "pick its encoding" in album.own.skip


def test_two_sheets_for_one_image_are_skipped(tmp_path, split_fixtures):
    folder = tmp_path / "Rip"
    shutil.copytree(split_fixtures / "wav_image", folder)
    shutil.copy2(folder / "image.cue", folder / "image (2).cue")
    [album] = tidy(tmp_path).albums
    assert album.own.kind is Kind.SKIPPED and album.own.skip.startswith("2 sheets name image.wav")


def eac_rip(folder: Path, split_fixtures: Path) -> Path:
    """What EAC leaves: a FLAC image, its sheet and a Monkey's Audio copy (a header is enough: .ape isn't audio)."""
    folder.mkdir(parents=True)
    for name in ("image.flac", "image.cue"):
        shutil.copy2(split_fixtures / "name_clash" / name, folder / name)
    (folder / "image.ape").write_bytes(b"MAC " + bytes(60))
    return folder


def test_an_eac_folder_with_a_ape_is_split(tmp_path, split_fixtures):
    """SPEC §28 "A .ape is ignored": before 0.8.6 the .ape was a second audio file, so the image was renamed and
    ReplayGained as a track instead of being split."""
    folder = eac_rip(tmp_path / "Rip", split_fixtures)
    [album] = tidy(tmp_path).albums
    assert album.own.kind is Kind.NOT_SPLIT and album.own.split is not None
    assert [t.name for t in album.own.split.written] == ["01 One.flac", "02 Two.flac"]
    assert album.own.offer == [folder / "image.flac", folder / "image.cue"]  # never the .ape
    assert album.replaygain == "after the split" and album.replaygain_exclude == {"image.flac"}


@pytest.mark.parametrize("named", ["image.ape", "image.wav"])
def test_a_sheet_naming_another_file_than_the_image_is_skipped(tmp_path, split_fixtures, named):
    folder = eac_rip(tmp_path / "Rip", split_fixtures)
    sheet = folder / "image.cue"
    sheet.write_text(sheet.read_text(encoding="utf-8").replace('"image.flac"', f'"{named}"'), encoding="utf-8")
    [album] = tidy(tmp_path).albums
    assert album.own.kind is Kind.SKIPPED
    assert album.own.skip == f"image.cue names {named}, not image.flac: open its .cue to split it by hand"
    assert album.own.renames is None and album.own.split is None  # nothing renamed
    assert album.replaygain is None and not album.own.offer  # nothing measured, nothing offered


def test_a_sheet_naming_an_image_the_split_cant_read_is_skipped(tmp_path, split_fixtures, fixtures_dir):
    folder = tmp_path / "Rip"
    folder.mkdir()
    shutil.copy2(fixtures_dir / "formats" / "tone.wv", folder / "image.wv")
    (folder / "image.cue").write_text((split_fixtures / "name_clash" / "image.cue").read_text(encoding="utf-8")
                                      .replace('"image.flac"', '"image.wv"'), encoding="utf-8")
    [album] = tidy(tmp_path).albums
    assert album.own.kind is Kind.SKIPPED
    assert album.own.skip == "WV isn't supported: the split reads FLAC, WAV and AIFF: open its .cue to split it by hand"
    assert album.own.renames is None and album.replaygain is None and not album.own.offer


def test_half_split_offers_the_image(tmp_path, split_fixtures, tone):
    folder = tmp_path / "Rip"
    shutil.copytree(split_fixtures / "wav_image", folder)
    tagged_track(folder, tone, "One", 1)
    tagged_track(folder, tone, "Two", 2)
    [album] = tidy(tmp_path).albums
    assert album.own.kind is Kind.HALF_SPLIT and album.own.rename_count == 0
    assert album.own.offer == [folder / "image.wav", folder / "image.cue"]
    assert album.replaygain is None  # the tracks are tagged; the image isn't measured
    assert album.tidy


def test_split_tracks_named_otherwise_are_renamed(tmp_path, tone):
    folder = tmp_path / "Album"
    path = tagged_track(folder, tone, "One", 1)
    path.rename(folder / "track one.flac")
    plan = tidy(tmp_path)
    [album] = plan.albums
    assert album.own.kind is Kind.SPLIT and album.own.rename_count == 1
    [item] = album.own.renames.with_status(rename.Status.RENAME)
    assert (item.source.name, item.new_path.name) == ("track one.flac", "01 One.flac")
    assert album.replaygain is None and not album.tidy
    assert plan.summary() == "1 album: 1 to rename"


def test_a_tidy_album(tmp_path, tone):
    tagged_track(tmp_path / "Album", tone, "One", 1)
    plan = tidy(tmp_path)
    assert plan.albums[0].tidy and not plan.has_work
    assert plan.summary() == "1 album: 1 already tidy"


def test_lite_and_bonus_are_left_to_the_parent(tmp_path, split_fixtures, tone):
    album = tmp_path / "Album"
    tagged_track(album, tone, "One", 1)
    shutil.copytree(split_fixtures / "wav_image", album / "lite")
    plan = tidy(tmp_path)
    assert [plan_.folder for plan_ in plan.folders()] == [album]  # lite isn't split or renamed


def test_a_disc_image_is_split_and_the_parent_replaygained(tmp_path, split_fixtures, tone):
    album = tmp_path / "Album"
    tagged_track(album, tone, "One", 1)
    shutil.copytree(split_fixtures / "wav_image", album / "CD2")
    [entry] = tidy(tmp_path).albums
    assert [(p.folder.name, p.kind) for p in entry.folders] == [("Album", Kind.SPLIT), ("CD2", Kind.NOT_SPLIT)]
    assert entry.replaygain == "after the split"


# -- the ReplayGain check --

def test_replaygain_check(tmp_path, tone):
    folder = tmp_path / "Album"
    tagged_track(folder, tone, "One", 1)
    assert replaygain_needed(folder, [], RG) is None
    assert replaygain_needed(folder, [], ReplayGainOptions(target=-18.0)) == "target -15 LUFS, now -18"
    tagged_track(folder, tone, "Two", 2, values=False)
    assert replaygain_needed(folder, [], RG) == "missing tags"


def test_replaygain_check_needs_the_comment(tmp_path, tone):
    folder = tmp_path / "Album"
    tagged_track(folder, tone, "One", 1, target=None)
    assert replaygain_needed(folder, [], RG) == "no target in the comment"


def test_replaygain_check_reads_disc_folders(tmp_path, tone):
    folder = tmp_path / "Album"
    tagged_track(folder, tone, "One", 1)
    tagged_track(folder / "CD2", tone, "Two", 1, values=False, target=None)
    assert replaygain_needed(folder, [folder / "CD2"], RG) == "missing tags in CD2"


# -- clutter (SPEC §28, 2026-10-22) --

def test_an_album_with_only_clutter_has_work(tmp_path, tone):
    album = tmp_path / "Album"
    tagged_track(album, tone, "One", 1)
    (album / "cover.jpg").write_bytes(b"x")
    (album / "Scans").mkdir()
    (album / "Scans" / "a.png").write_bytes(b"x")
    plan = tidy(tmp_path)
    [own] = plan.albums
    assert not own.tidy and own.replaygain is None
    assert [(item.path.name, item.why) for item in own.clutter] == [("Scans", "folder of 1 picture"),
                                                                    ("cover.jpg", "picture beside music")]
    assert plan.has_work and plan.summary() == "1 album: 1 with files to offer"


def test_clutter_outside_any_album_is_loose(tmp_path, tone):
    tagged_track(tmp_path / "Artist" / "Album", tone, "One", 1)
    (tmp_path / "Artist" / "Artwork").mkdir()
    (tmp_path / "Artist" / "Artwork" / "a.jpg").write_bytes(b"x")
    (tmp_path / "Artist" / "Album" / "CD2" / "Empty").mkdir(parents=True)  # under a keyword folder: the album's
    plan = tidy(tmp_path)
    assert [item.path.name for item in plan.loose] == ["Artwork"]
    assert [item.path.name for item in plan.albums[0].clutter] == ["CD2"]  # CD2 holds only an empty folder
    assert plan.summary() == "1 album: 1 with files to offer; 1 folder to offer outside the albums"


def test_several_folders_make_one_plan(tmp_path, split_fixtures, fixtures_dir):
    """The right-click Tidy on several folders (SPEC §31): each planned as Tidy on it, one plan for them all."""
    eac, other = tmp_path / "Rips" / "EAC", tmp_path / "Rips" / "Album B"
    shutil.copytree(split_fixtures / "wav_image", eac)
    shutil.copytree(fixtures_dir / "replaygain" / "Fixture Artist" / "Album B", other)
    plan = plan_tidy_paths([eac, other], RENAME, RG, DEFAULT_MASKS, DEFAULT_REPLACEMENT)
    assert plan.root == tmp_path / "Rips" and plan.trees == [eac, other]
    assert [album.folder for album in plan.albums] == [eac, other]
    assert album_at(plan, eac).own.kind is Kind.NOT_SPLIT
    one = plan_tidy_paths([eac], RENAME, RG, DEFAULT_MASKS, DEFAULT_REPLACEMENT)
    assert one.root == eac and one.roots == [] and one.trees == [eac]  # one folder: Tidy's own plan
