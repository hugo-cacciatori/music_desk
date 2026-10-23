"""The feature map behind `pytest --changed` and `pytest --area` (SPEC §18 "Test selection").

Each area lists its source files (relative to src/music_desk/) and its test files (relative to tests/). A source
under core/ is the backend layer; any other source is the desktop app's. A changed source runs its areas' tests of
its own layer only (Hugo, 2026-10-23): a backend change runs backend tests, a desktop change desktop tests. The
tests at the top of tests/ (settings, packaging…) are plain Python and run with their area in either layer.

`--with-callers` is the one exception: a backend change that alters what a function returns or does for its
callers (not only how it does it) also runs its areas' desktop tests. Nothing can see that from the diff, so the
person or tool making the change says so and adds the option.

tests/test_areas.py checks that every source and test file is placed here, so the map can't drift.
"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
SRC = ROOT / "src" / "music_desk"

CORE, DESKTOP, PLAIN = "backend", "desktop", "plain"  # PLAIN: tests at the top of tests/, run with either layer


@dataclass(frozen=True)
class Area:
    sources: tuple[str, ...]
    tests: tuple[str, ...]
    files: tuple[str, ...] = ()  # files outside src/ (repo-relative prefixes) that belong to the area


AREAS: dict[str, Area] = {
    "tags": Area(
        ("core/formats.py", "core/tags.py", "core/metadata.py", "services/tag_io.py", "services/tag_writer.py",
         "ui/side_panel/metadata_tab.py"),
        ("core/test_formats.py", "core/test_tags.py", "core/test_metadata.py", "core/test_tags_parity.py",
         "core/test_tag_encodings.py", "core/test_ape_ignored.py", "ui/test_metadata_tab.py")),
    "side_panel": Area(
        ("ui/side_panel/panel.py", "ui/side_panel/folder_summary.py"),
        ("ui/test_metadata_tab.py", "ui/test_file_preview.py", "ui/test_folder_summary.py",
         "ui/test_lyrics_tab.py")),
    "index": Area(
        ("core/index/schema.py", "core/index/store.py", "core/index/scanner.py", "core/index/roots.py",
         "core/index/queries.py", "services/indexer.py", "services/watcher.py", "services/library.py"),
        ("core/test_index_schema.py", "core/test_index_store.py", "core/test_index_scanner.py",
         "core/test_index_roots.py", "core/test_ape_ignored.py", "ui/test_indexer.py", "ui/test_watcher.py",
         "ui/test_library_roots.py", "ui/test_refresh.py", "ui/test_responsiveness.py")),
    "browser": Area(
        ("core/index/queries.py", "ui/folder_tree.py", "ui/file_view.py", "ui/address_bar.py", "ui/sidebar.py"),
        ("ui/test_browser.py", "ui/test_navigation.py", "ui/test_context_menus.py", "ui/test_folder_summary.py",
         "ui/test_library_roots.py", "ui/test_responsiveness.py")),
    "search": Area(
        ("core/index/search.py", "services/search_service.py", "ui/search_view.py", "ui/controllers/search.py"),
        ("core/test_search.py", "core/test_search_parity.py", "ui/test_library_search.py")),
    "advanced_search": Area(
        ("core/index/album_search.py", "services/album_search_service.py", "ui/advanced_search.py"),
        ("core/test_album_search.py", "ui/test_advanced_search_window.py")),
    "lyrics": Area(
        ("core/lyrics.py", "core/index/lyrics_index.py", "core/index/lyrics_import.py", "services/lyrics_service.py",
         "ui/side_panel/lyrics_tab.py"),
        ("core/test_lyrics.py", "core/test_lyrics_parity.py", "core/test_lyrics_index.py",
         "core/test_lyrics_import.py", "ui/test_lyrics_service.py", "ui/test_lyrics_tab.py",
         "ui/test_lyrics_status.py", "ui/test_folder_summary.py")),
    "rename": Area(
        ("core/rename.py", "core/masks.py", "core/inline_rename.py", "services/rename_job.py",
         "ui/tools/rename_card.py", "ui/tools/rename_preview.py", "ui/tools/names_card.py",
         "ui/controllers/tools.py"),
        ("core/test_rename.py", "core/test_rename_parity.py", "core/test_rename_preview.py", "core/test_masks.py",
         "core/test_inline_rename.py", "ui/test_rename_tool.py", "ui/test_inline_rename_ui.py")),
    "replaygain": Area(
        ("core/replaygain.py", "core/rsgain.py", "services/replaygain_job.py", "ui/tools/replaygain_card.py",
         "ui/tools/rg_report.py", "ui/tools/rg_recap.py", "ui/controllers/tools.py"),
        ("core/test_replaygain.py", "core/test_replaygain_parity.py", "core/test_replaygain_remove.py",
         "core/test_rsgain.py", "core/test_rsgain_keys.py", "core/test_rsgain_scan.py",
         "ui/test_replaygain_tool.py", "ui/test_replaygain_remove_ui.py", "ui/test_jobs_panel.py"),
        ("resources/rsgain/",)),
    "split": Area(
        ("core/cue.py", "core/cue_edit.py", "core/flac.py", "core/split.py", "services/split_job.py",
         "ui/split_page.py", "ui/controllers/split.py", "ui/track_lengths.py"),
        ("core/test_cue.py", "core/test_cue_edit.py", "core/test_cue_new.py", "core/test_split.py",
         "ui/test_split_tool.py"),
        ("resources/flac/",)),
    "trim": Area(
        ("core/trim.py", "core/waveform.py", "core/flac.py", "services/trim_job.py", "ui/trim_page.py",
         "ui/controllers/trim.py"),
        ("core/test_trim.py", "core/test_waveform.py", "ui/test_trim_tool.py"),
        ("resources/flac/",)),
    "tidy": Area(
        ("core/tidy.py", "services/tidy_job.py", "ui/controllers/tidy.py", "ui/tools/tidy_page.py",
         "ui/recycle_window.py", "services/recycle_job.py"),
        ("core/test_tidy.py", "ui/test_tidy_tool.py")),
    "cleanup": Area(
        ("core/cleanup.py", "services/recycle_job.py", "ui/controllers/cleanup.py", "ui/recycle_window.py",
         "ui/tools/cleanup_card.py"),
        ("core/test_cleanup.py", "ui/test_cleanup_tool.py", "ui/test_tidy_tool.py")),
    "delete": Area(
        ("core/fsops.py", "ui/controllers/delete.py"),
        ("core/test_fsops.py", "ui/test_delete.py")),
    "playlists": Area(
        ("core/playlists.py", "core/playlist_builder.py", "ui/playlist_panel.py", "ui/entry_drag.py"),
        ("core/test_playlists.py", "core/test_playlist_builder.py", "ui/test_playlist_panel.py",
         "ui/test_playlist_dnd.py", "ui/test_playlist_export.py", "ui/test_context_menus.py")),
    "preview": Area(
        ("core/preview.py", "ui/side_panel/file_preview.py"),
        ("core/test_preview.py", "ui/test_file_preview.py")),
    "jobs": Area(
        ("core/cancel.py", "core/path_locks.py", "services/jobs.py", "ui/jobs_panel.py"),
        ("core/test_path_locks.py", "ui/test_jobs_panel.py", "ui/test_metadata_tab.py", "ui/test_delete.py")),
    "options": Area(
        ("settings.py", "ui/tools/tool_cards.py", "ui/tools/settings_cards.py", "ui/toolbar_icons.py",
         "ui/controllers/tools.py"),
        ("core/test_tool_options.py", "test_settings.py", "ui/test_toolbar.py")),
    "open_here": Area(
        ("app.py", "here.py", "instance.py", "foreground.py", "logging_setup.py"),
        ("ui/test_open_here.py",)),
    "hover_texts": Area(("ui/hover.py",), ("ui/test_hover_texts.py",)),
    "scroll_wheel": Area(("ui/no_wheel.py",), ("ui/test_no_wheel.py",)),
    "stall_watch": Area(("ui/stall_watch.py",), ("ui/test_stall_watch.py",)),
    "packaging": Area(
        ("__init__.py", "paths.py", "licenses.py", "ui/about_dialog.py"),
        ("test_packaging.py", "test_licenses.py", "test_paths.py", "ui/test_about.py"),
        ("packaging/", "resources/", "LICENSE")),
}

# Sources every desktop test goes through (the window and what all of it uses): a change runs the whole layer.
WHOLE_DESKTOP = ("ui/main_window.py", "ui/qt_util.py", "ui/toast.py", "ui/controllers/__init__.py")
# Package markers and the entry point: nothing to test on their own.
NO_TESTS = ("__main__.py", "core/__init__.py", "core/index/__init__.py", "services/__init__.py", "ui/__init__.py",
            "ui/side_panel/__init__.py", "ui/tools/__init__.py")

# The layer's guards: rules every change of that layer must keep (Ground rules 4 and 7, SPEC §24), and the type
# check, which catches a caller left behind by a changed signature in either layer.
GUARDS = {
    CORE: ("core/test_no_qt.py", "test_types.py"),
    DESKTOP: ("ui/test_hover_texts.py", "ui/test_no_wheel.py", "ui/test_main_window.py", "test_types.py"),
}
# Test-tooling files: the map itself and its check.
TOOLING = ("areas.py", "test_areas.py")

# Files outside src/ and tests/: what a change to them runs. EVERYTHING: the whole suite; NOTHING: no test.
EVERYTHING, NOTHING = "everything", "nothing"
OTHER_FILES = {
    "pyproject.toml": EVERYTHING,  # dependencies, pytest and type-check settings
    "uv.lock": EVERYTHING,
    ".python-version": EVERYTHING,
    "README.md": NOTHING,
    ".gitignore": NOTHING,
    ".gitattributes": NOTHING,
}


def layer_of_source(rel: str) -> str:
    return CORE if rel.startswith("core/") else DESKTOP


def layer_of_test(rel: str) -> str:
    if rel.startswith("core/"):
        return CORE
    if rel.startswith("ui/"):
        return DESKTOP
    return PLAIN


def all_test_files() -> list[str]:
    return sorted(p.relative_to(TESTS).as_posix() for p in TESTS.rglob("test_*.py")
                  if not {"reference", "fixtures"} & set(p.relative_to(TESTS).parts))


def all_source_files() -> list[str]:
    return sorted(p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py"))


def layer_tests(layer: str) -> list[str]:
    return [t for t in all_test_files() if layer_of_test(t) == layer]


def areas_of_source(rel: str) -> list[str]:
    return [name for name, area in AREAS.items() if rel in area.sources]


def area_tests(names, layer: str | None = None) -> set[str]:
    """The areas' tests; with a layer, only that layer's and the plain ones."""
    tests = set()
    for name in names:
        for test in AREAS[name].tests:
            if layer is None or layer_of_test(test) in (layer, PLAIN):
                tests.add(test)
    return tests


@dataclass
class Selection:
    tests: dict[str, list[str]] = field(default_factory=dict)  # test file -> why it runs
    everything: list[str] = field(default_factory=list)  # reasons to run the whole suite

    def add(self, tests, reason: str) -> None:
        for test in tests:
            self.tests.setdefault(test, [])
            if reason not in self.tests[test]:
                self.tests[test].append(reason)


def helper_users(rel: str) -> list[str]:
    """The test files that import a helper module of tests/ (lrclib_fake, browser_helpers…)."""
    module = Path(rel).stem
    users = []
    for test in all_test_files():
        text = (TESTS / test).read_text(encoding="utf-8")
        if f"import {module}" in text or f"from {module} " in text:
            users.append(test)
    return users


def select(changed: list[str], with_callers: bool = False) -> Selection:
    """The tests a set of changed files (repo-relative, forward slashes) needs, each with its reasons."""
    selection = Selection()
    for path in changed:
        if path.startswith("src/music_desk/"):
            rel = path.removeprefix("src/music_desk/")
            if not rel.endswith(".py") or rel in NO_TESTS:
                continue
            layer = layer_of_source(rel)
            selection.add(GUARDS[layer], f"{layer} guard ({rel})")
            if rel in WHOLE_DESKTOP:
                selection.add(layer_tests(DESKTOP), f"{rel}: every desktop test goes through it")
                continue
            names = areas_of_source(rel)
            if not names:
                selection.add(layer_tests(layer), f"{rel} is not in tests/areas.py: its whole layer")
                continue
            selection.add(area_tests(names, layer), f"{rel} ({', '.join(names)}, {layer})")
            if with_callers and layer == CORE:
                selection.add(area_tests(names, DESKTOP), f"{rel}: its desktop callers (--with-callers)")
        elif path.startswith("tests/"):
            rel = path.removeprefix("tests/")
            if rel.startswith(("reference/", "fixtures/")) or rel == "conftest.py":
                selection.everything.append(f"{path}: shared by every test")
            elif rel.endswith("conftest.py"):
                folder = rel.rsplit("/", 1)[0] + "/"
                selection.add([t for t in all_test_files() if t.startswith(folder)], f"{path}")
            elif rel in TOOLING:
                selection.add(["test_areas.py"], f"{path}: the feature map")
            elif Path(rel).name.startswith("test_") and rel.endswith(".py"):
                if (TESTS / rel).exists():
                    selection.add([rel], f"{path} changed")
            elif rel.endswith(".py"):
                selection.add(helper_users(rel), f"{path}: a helper they import")
        else:
            matches = [name for name, area in AREAS.items() if any(path.startswith(p) for p in area.files)]
            if matches:
                selection.add(area_tests(matches), f"{path} ({', '.join(matches)})")
            elif OTHER_FILES.get(path) == NOTHING:
                continue
            else:
                selection.everything.append(f"{path}: {'not in the map' if path not in OTHER_FILES else 'shared'}")
    return selection


def changed_files(base: str = "HEAD") -> list[str]:
    """Files changed since `base`, committed or not, plus untracked ones (git's view of this repo; the docs live
    in the private repo and are ignored here)."""
    def git(*args: str) -> list[str]:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                             check=True).stdout
        return [line.strip() for line in out.splitlines() if line.strip()]

    return sorted(set(git("diff", "--name-only", base)) | set(git("ls-files", "--others", "--exclude-standard")))
