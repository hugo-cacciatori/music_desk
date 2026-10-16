# 18. Testing
- `pytest` runs:
  - core unit tests (no Qt);
  - LRCLIB tests on mocked HTTP (tests never call the real API);
  - pytest-qt tests for the models, navigation, side panel save and revert, and rename validation.
- Fixtures:
  - tiny 1-second files per format, with varied tags: missing title, ID3v2.3, lowercase TXXX ReplayGain,
    lite/bonus/CD2 subfolders, a name clash;
  - cleanup cases: a single-file album with its .cue, a per-track .cue, .lrc sidecars with and without a lyrics
    tag, `replaygain.log`, `folder.jpg`, a `.m3u`;
  - from M9 and M10: preview texts in several encodings (§20), and the cue split cases (§22), whose audio must
    never repeat, so that a cut in the wrong place changes a track's MD5.

  Tests copy them to `tmp_path`.
- Fixture generation:
  - `tests/fixtures/make_fixtures.py` generates the fixtures with ffmpeg (dev-only, found on PATH or through an
    `FFMPEG` environment variable), and the generated files are committed, so running the tests doesn't need
    ffmpeg.
  - `generated/preview/` (M9) holds text in the encodings album folders use: an EAC-style UTF-16 LE log, a UTF-8
    BOM text, a UTF-8 cue, a cp1252 text, a CP437 `.nfo` and a UTF-16 `.lrc`. `make_fixtures.py preview`
    regenerates only these, without ffmpeg.
  - `generated/split/` (M10) holds single-file albums for the cue split: 16-bit chirps that never repeat, an
    audible hidden track, a silent pregap, a WAV image, refusals, and sheets in several encodings with Hugo's EAC
    sheet. `make_fixtures.py split` regenerates only these (it needs ffmpeg).
  - ffmpeg can encode FLAC, Vorbis, Opus, MP3, MP2, WAV, AIFF, AAC and ALAC in M4A, WavPack, TTA and WMA. It can't
    encode APE, TAK, Musepack, OptimFROG or DSF/DFF, and Speex depends on the build. They have no fixture: getting
    sample files was dropped (Hugo, 2026-10-15), so they stay supported through mutagen, untested with a real file.
- Parity: run the reference functions and the ported core on identical copies of FLAC/MP3 fixtures, then compare
  file names, tags and RunLog text with timestamps and durations masked. Other formats get their own expected
  values.
- `slow` marker: the real bundled rsgain on fixtures, for every format rsgain supports, and (from M10) the real
  bundled flac on the cue split fixtures, including a split of one ticked track (§22).
- Cue sheets saved unedited from the Split page must be byte-identical to every fixture sheet (§22, the round
  trip).
- Never in tests: the real Recycle Bin (inject a recorder; §19 adds an injected `can_recycle`), the real network,
  a real dialog, or a real library.
- Performance: a synthetic tree of 100k entries checks index time and UI responsiveness. Debug builds log any
  event-loop stall above 50 ms through a QTimer watchdog (as built in 0.2.1: `ui/stall_watch.py` `StallWatch`, a
  20 ms timer that logs how late it fires, turned on by `app.main` when running from source, not in the
  installed exe).
- Manual checklist per milestone, on a copied album:
  - browse while indexing; edit tags, including multi-file edits and the cover; rename a file and a folder;
  - preview and run each tool, including the cleanup protections, then compare `replaygain.log` with a script run;
  - go through every Lyrics tab state, including offline mode;
  - play a folder through the default app.

**Static types** (M11 task 1, added by Hugo on 2026-10-16; not urgent)
- The code has type hints everywhere (a convention since M0), but nothing checks them. Pyright would catch a
  caller left behind when a signature changes, or a wrong argument type.
- What it wouldn't catch, from M10 task 1: when `read_snapshot` gained the masks, `write_fields` kept calling it
  with the defaults (type-correct, so a test found it). And the tests that broke used a fake swapped in with
  monkeypatch, which a type checker doesn't see. Tests stay the main guard.
- The plan: pyright (basic mode) as a dev dependency, configured in `pyproject.toml` for `src/`, and run by
  the test suite (`tests/test_types.py` runs it and fails on any error), so it can't be forgotten.
- Its first run's findings are fixed in the same task. Third-party packages without stubs (mutagen, watchdog,
  send2trash) are allowed as `Any`.
