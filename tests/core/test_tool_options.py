"""The Tools cards' options in core: ReplayGain validation, and saving both option sets as JSON (SPEC §10, §12)."""

import dataclasses

import pytest

from music_desk.core import rename, replaygain
from music_desk.core.replaygain import ReplayGainOptions


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
    custom_rename = rename.RenameOptions(masks=("{disc}-{track:02} {title}",), replacement="_", recursive=False,
                                         cleanup=True, junk_extensions=frozenset({".nfo"}),
                                         audio_extensions=frozenset({".flac", ".mp3"}))
    assert rename.options_from_json(rename.options_to_json(custom_rename)) == custom_rename
    custom_rg = ReplayGainOptions(target=-18.0, safety_margin=0.5, subfolder_keywords=("extra",), log_file="rg.log")
    assert replaygain.options_from_json(replaygain.options_to_json(custom_rg)) == custom_rg


def test_saved_options_fall_back_per_value():
    assert rename.options_from_json(None) == rename.DEFAULT_OPTIONS
    assert replaygain.options_from_json("nonsense") == replaygain.DEFAULT_OPTIONS
    options = rename.options_from_json({"masks": "{title}", "replacement": 3, "cleanup": True, "recursive": "yes"})
    assert options == dataclasses.replace(rename.DEFAULT_OPTIONS, cleanup=True)
    rg = replaygain.options_from_json({"target": True, "safety_margin": 1, "track_data_keywords": [1], "comment": "c"})
    assert rg == dataclasses.replace(replaygain.DEFAULT_OPTIONS, safety_margin=1.0, comment="c")


def test_invalid_but_well_typed_values_are_kept_for_validation_to_show():
    options = rename.options_from_json({"junk_extensions": [".mp3"]})
    assert options.junk_extensions == frozenset({".mp3"})
    assert rename.validate(options) == [".mp3 can't be junk: these are music files"]
