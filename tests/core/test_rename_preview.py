"""core/rename.py additions for the Rename tool's UI: the preview report, the summary, steps and cancel (SPEC §10)."""

import pytest

from refrain.core.cancel import CancelToken
from refrain.core.rename import (
    PlanCancelled,
    RenameOptions,
    Status,
    apply_plan,
    plan_renames,
    preview_report,
    summary_sections,
)

ALBUM = "album/Fixture Artist - Fixture Album"
LISTS = ("renamed", "fallback", "missing_tags", "name_taken", "errors", "kept", "sidecars_renamed", "warnings")


def test_the_preview_reports_what_apply_then_does(fixture_copy):
    folder = fixture_copy("cleanup")
    plan = plan_renames(folder)
    preview = preview_report(plan)
    applied = apply_plan(plan)
    for name in LISTS:
        assert getattr(preview, name) == getattr(applied, name), name
    assert preview.scanned == applied.scanned and applied.renamed


def test_steps_record_every_rename_in_order(fixture_copy):
    folder = fixture_copy("cleanup")
    report = apply_plan(plan_renames(folder))
    assert report.steps
    for old, new in report.steps:
        assert not old.exists() and new.exists()
    lrc = [(old.name, new.name) for old, new in report.steps if old.suffix == ".lrc"]
    assert ("no_lyrics.lrc", "02 No Lyrics.lrc") in lrc
    audio_first = [old.name for old, _new in report.steps]
    assert audio_first.index("no_lyrics.flac") == audio_first.index("no_lyrics.lrc") - 1  # the sidecar follows


def test_summary_sections_wording(fixture_copy):
    folder = fixture_copy("cleanup")
    titles = [title for title, _entries in summary_sections(preview_report(plan_renames(folder)), dry_run=True)]
    assert titles[0].endswith("files would be renamed")
    assert "1 file kept" in titles  # the single-file album's image
    assert not any("Recycle Bin" in title for title in titles)  # renaming never recycles (SPEC §32)
    assert "2 .lrc files would follow their music file" in titles
    empty = summary_sections(preview_report(plan_renames(folder / "lrc", RenameOptions(masks=("{composer}",)))),
                             dry_run=False)
    assert [title for title, _e in empty][0] == "0/2 files were renamed"


def test_planning_can_be_cancelled(fixture_copy):
    cancel = CancelToken()
    cancel.cancel()
    with pytest.raises(PlanCancelled):
        plan_renames(fixture_copy(ALBUM), cancel=cancel)


def test_a_selection_plans_only_those_files(fixture_copy):
    folder = fixture_copy(ALBUM)
    plan = plan_renames(folder, RenameOptions(recursive=False), selection=[folder / "track2.flac"])
    assert [(i.source.name, i.status) for i in plan.items] == [("track2.flac", Status.RENAME)]
