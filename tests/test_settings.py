"""settings.py: typed JSON settings (SPEC §12)."""

import json

from music_desk.settings import Settings


def test_defaults_when_missing(tmp_path):
    settings = Settings.load(tmp_path / "settings.json")
    assert settings == Settings()
    assert settings.theme == "system" and not settings.lyrics_offline and settings.expanded_folders == []


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
    path.write_text(json.dumps({"hide_folders_without_music": "yes", "theme": "dark", "lyrics_offline": 1,
                                "expanded_folders": "C:/x", "unknown": 3}))
    settings = Settings.load(path)
    assert (settings.hide_folders_without_music, settings.theme, settings.lyrics_offline,
            settings.expanded_folders) == (False, "dark", False, [])


def test_removed_settings_are_ignored(tmp_path):
    """Settings files from before the Settings dialog was dropped still load (their two extra keys are unknown)."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"watch_for_changes": False, "tag_workers": 8, "lyrics_auto_save": True}))
    settings = Settings.load(path)
    assert settings.lyrics_auto_save and not hasattr(settings, "tag_workers")


def test_corrupt_file_gives_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json")
    assert Settings.load(path) == Settings()
    path.write_text("[1, 2]")
    assert Settings.load(path) == Settings()


def test_unsaved_settings(tmp_path):
    Settings().save()  # no path: nothing is written
    assert list(tmp_path.iterdir()) == []
