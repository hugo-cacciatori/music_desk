"""settings.py: typed JSON settings (SPEC §12)."""

import json

from music_desk.settings import Settings


def test_defaults_when_missing(tmp_path):
    settings = Settings.load(tmp_path / "settings.json")
    assert settings == Settings()
    assert settings.watch_for_changes and settings.tag_workers == 4 and settings.expanded_folders == []


def test_round_trip(tmp_path):
    path = tmp_path / "sub" / "settings.json"
    settings = Settings.load(path)
    settings.hide_folders_without_music = True
    settings.expanded_folders = [r"C:\Music\Björk"]
    settings.file_list_header = "AAAA"
    settings.save()
    assert Settings.load(path) == settings
    assert not (tmp_path / "sub" / "settings.json.tmp").exists()


def test_bad_values_fall_back(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"tag_workers": "eight", "theme": "dark", "lyrics_offline": 1,
                                "expanded_folders": "C:/x", "unknown": 3, "watch_for_changes": False}))
    settings = Settings.load(path)
    assert (settings.tag_workers, settings.theme, settings.lyrics_offline, settings.expanded_folders,
            settings.watch_for_changes) == (4, "dark", False, [], False)


def test_corrupt_file_gives_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json")
    assert Settings.load(path) == Settings()
    path.write_text("[1, 2]")
    assert Settings.load(path) == Settings()


def test_unsaved_settings(tmp_path):
    Settings().save()  # no path: nothing is written
    assert list(tmp_path.iterdir()) == []
