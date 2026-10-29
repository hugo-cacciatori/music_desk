"""The Tools cards' options in core: ReplayGain validation, and saving both option sets as JSON (SPEC §10, §12)."""

import dataclasses

import pytest

from refrain.core import rename, replaygain
from refrain.core.replaygain import ReplayGainOptions


def test_default_options_are_valid():
    assert replaygain.validate(replaygain.DEFAULT_OPTIONS) == []
    assert rename.validate(rename.DEFAULT_OPTIONS) == []


@pytest.mark.parametrize(("change", "problem"), [
    ({"target": -31.0}, "The target loudness must be between -30 and -5 LUFS (rsgain's range)"),
    ({"target": -4.5}, "The target loudness must be between -30 and -5 LUFS (rsgain's range)"),
    ({"safety_threshold": 0.0}, "The safety threshold must be above 0 and at most 1 (a peak of 1.0 is full scale)"),
    ({"safety_threshold": 1.2}, "The safety threshold must be above 0 and at most 1 (a peak of 1.0 is full scale)"),
    ({"safety_margin": -0.1}, "The safety margin must be between 0 and 6 dB"),
    ({"subfolder_keywords": ("lite", "")}, "Album-data subfolder keywords: a keyword is empty"),
    ({"track_data_keywords": ("Lite",)},
     "Track-data keywords: 'Lite' must be lowercase (folder names are compared in lowercase)"),
    ({"log_file": "logs/replaygain.log"}, "The log file name isn't a valid file name"),
    ({"log_file": " "}, "The log file name isn't a valid file name"),
    ({"log_file": "replaygain."}, "The log file name isn't a valid file name"),
    ({"comment": "LUFS {level}"}, "The comment text is invalid ('level'); it may only use {target}"),
])
def test_replaygain_validation(change, problem):
    assert replaygain.validate(dataclasses.replace(replaygain.DEFAULT_OPTIONS, **change)) == [problem]


def test_the_boundaries_of_the_target_are_allowed():
    for target in (-30.0, -5.0, -15.5):
        assert replaygain.validate(ReplayGainOptions(target=target)) == []


def test_options_round_trip_through_json():
    custom_rename = rename.RenameOptions(recursive=False, audio_extensions=frozenset({".flac", ".mp3"}))
    assert rename.options_from_json(rename.options_to_json(custom_rename)) == custom_rename
    # The masks are the shared setting now (SPEC §21): not saved with the card, and old saved ones are ignored here.
    assert "masks" not in rename.options_to_json(custom_rename)
    assert rename.options_from_json({"masks": ["{title}"], "replacement": "_"}).masks == rename.DEFAULT_MASKS
    custom_rg = ReplayGainOptions(target=-18.0, safety_margin=0.5, subfolder_keywords=("extra",), log_file="rg.log")
    assert replaygain.options_from_json(replaygain.options_to_json(custom_rg)) == custom_rg


def test_saved_options_fall_back_per_value():
    assert rename.options_from_json(None) == rename.DEFAULT_OPTIONS
    assert replaygain.options_from_json("nonsense") == replaygain.DEFAULT_OPTIONS
    options = rename.options_from_json({"masks": "{title}", "replacement": 3, "recursive": False, "cleanup": True})
    assert options == dataclasses.replace(rename.DEFAULT_OPTIONS, recursive=False)  # "cleanup" is Clean up's now
    rg = replaygain.options_from_json({"target": True, "safety_margin": 1, "track_data_keywords": [1], "comment": "c"})
    assert rg == dataclasses.replace(replaygain.DEFAULT_OPTIONS, safety_margin=1.0, comment="c")


def test_invalid_but_well_typed_values_are_kept_for_validation_to_show():
    options = rename.options_from_json({"audio_extensions": [".flac", "MP3"]})
    assert options.audio_extensions == frozenset({".flac", "MP3"})
    assert rename.validate(options) == ["Music file types: 'MP3' must be lowercase and start with a dot"]
