"""settings.py: typed JSON settings (SPEC §12)."""

import json

from music_desk.settings import Settings


def test_defaults_when_missing(tmp_path):
    settings = Settings.load(tmp_path / "settings.json")
    assert settings == Settings()
    assert not settings.hide_folders_without_music and settings.expanded_folders == []


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
    path.write_text(json.dumps({"hide_folders_without_music": "yes", "last_folder": "C:/Music", "trim_subfolder": 1,
                                "expanded_folders": "C:/x", "unknown": 3}))
    settings = Settings.load(path)
    assert (settings.hide_folders_without_music, settings.last_folder, settings.trim_subfolder,
            settings.expanded_folders) == (False, "C:/Music", "lite", [])


def test_removed_settings_are_ignored(tmp_path):
    """Settings files from before the Settings dialog was dropped, or before M12 retired the lyrics settings, still
    load (their extra keys are unknown) and lose those keys when saved again."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"watch_for_changes": False, "tag_workers": 8, "theme": "dark",
                                "lyrics_auto_save": True, "lyrics_offline": True, "last_folder": "C:/Music"}))
    settings = Settings.load(path)
    assert settings.last_folder == "C:/Music" and not hasattr(settings, "tag_workers")
    assert not hasattr(settings, "lyrics_offline") and not hasattr(settings, "lyrics_auto_save")
    settings.save()
    assert "lyrics_offline" not in json.loads(path.read_text(encoding="utf-8"))


def test_corrupt_file_gives_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json")
    assert Settings.load(path) == Settings()
    path.write_text("[1, 2]")
    assert Settings.load(path) == Settings()


def test_unsaved_settings(tmp_path):
    Settings().save()  # no path: nothing is written
    assert list(tmp_path.iterdir()) == []


def test_old_rename_masks_migrate_to_the_shared_setting(tmp_path):
    """Before 0.2.7 the masks were a Rename card option (SPEC §21): an existing file keeps the user's masks."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"rename_options": {"masks": ["{disc}-{track:02} {title}"], "replacement": "_",
                                                   "cleanup": True}}))
    settings = Settings.load(path)
    assert settings.name_masks == ["{disc}-{track:02} {title}"] and settings.name_replacement == "_"
    path.write_text(json.dumps({"name_masks": ["{title}"], "rename_options": {"masks": ["{album}"]}}))
    assert Settings.load(path).name_masks == ["{title}"]  # the shared setting wins once it exists


def test_mistyped_masks_fall_back_to_the_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"name_masks": ["{title}", 3], "name_replacement": 5}))
    settings = Settings.load(path)
    assert settings.name_masks == ["{track:02} {title}", "{title}"] and settings.name_replacement == "-"
