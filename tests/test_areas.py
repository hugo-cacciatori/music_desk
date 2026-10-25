"""The feature map (tests/areas.py, SPEC §18 "Test selection"): every file is placed, and a change runs the tests of
its own layer only, unless the change says its callers need them too."""

from areas import (
    AREAS,
    CORE,
    DESKTOP,
    GUARDS,
    NO_TESTS,
    SRC,
    TESTS,
    TOOLING,
    WHOLE_DESKTOP,
    all_source_files,
    all_test_files,
    layer_of_test,
    select,
)


def test_every_source_file_is_placed():
    placed = {s for area in AREAS.values() for s in area.sources} | set(WHOLE_DESKTOP) | set(NO_TESTS)
    assert [s for s in all_source_files() if s not in placed] == []


def test_every_test_file_is_placed():
    placed = {t for area in AREAS.values() for t in area.tests} | {t for g in GUARDS.values() for t in g}
    assert [t for t in all_test_files() if t not in placed and t not in TOOLING] == []


def test_every_mapped_file_exists():
    sources = {s for area in AREAS.values() for s in area.sources} | set(WHOLE_DESKTOP) | set(NO_TESTS)
    tests = {t for area in AREAS.values() for t in area.tests} | {t for g in GUARDS.values() for t in g}
    assert [s for s in sources if not (SRC / s).is_file()] == []
    assert [t for t in tests if not (TESTS / t).is_file()] == []


def layers(selection) -> set[str]:
    return {layer_of_test(t) for t in selection.tests}


def test_a_backend_change_runs_backend_tests_only():
    selection = select(["src/music_desk/core/split.py"])
    assert "core/test_split.py" in selection.tests and "test_types.py" in selection.tests
    assert DESKTOP not in layers(selection) and not selection.everything


def test_with_callers_adds_the_areas_desktop_tests():
    selection = select(["src/music_desk/core/split.py"], with_callers=True)
    assert "ui/test_split_tool.py" in selection.tests and "ui/test_trim_tool.py" not in selection.tests


def test_a_desktop_change_runs_desktop_tests_only():
    selection = select(["src/music_desk/ui/split_page.py"])
    assert "ui/test_split_tool.py" in selection.tests and "ui/test_hover_texts.py" in selection.tests
    assert CORE not in layers(selection)


def test_the_window_runs_every_desktop_test():
    selection = select(["src/music_desk/ui/main_window.py"])
    assert {t for t in all_test_files() if t.startswith("ui/")} <= set(selection.tests)
    assert CORE not in layers(selection)


def test_docs_run_nothing_and_dependencies_everything():
    assert select(["README.md"]).tests == {} and not select(["README.md"]).everything
    assert select(["uv.lock"]).everything


def test_a_helper_runs_the_tests_that_import_it():
    tests = select(["tests/core/rsgain_fake.py"]).tests
    assert "core/test_replaygain.py" in tests and "ui/test_replaygain_tool.py" in tests
    assert "core/test_cue.py" not in tests
