# 17. Porting reference
Source: `tests/reference/music_tools.py` and `tests/reference/seek_lyrics.py`, frozen copies of `music_tools.py`
and `seek_lyrics.py`, made at M1 from the originals at the project root (deleted on 2026-10-15, once the app
replaced them).

**Required refactors:**
- Remove every `os.getcwd()`, `os.chdir` and `contextlib.chdir`. The working directory is process-global, so
  changing it from a worker would break the app. Functions take explicit folder paths, and rsgain gets `cwd=`.
- `print` and `input` → a `Reporter` protocol (info, warn, error, progress). RunLog keeps collecting warnings and
  errors exactly as it does now.
- Config constants → frozen dataclasses `RenameOptions`, `ReplayGainOptions` and `LyricsOptions`, with defaults
  identical to the scripts (except the audio file types and the junk list). `validate_config` → `validate(options)`,
  which adds the cleanup protections.
- The rename step splits into `plan_renames(folder, options) -> RenamePlan` and `apply_plan(plan) -> RenameReport`.
  Dry run means plan only.
- `load_audio`, `get_tag`, `set_tag`, `set_comment` and `find_txxx` (FLAC/MP3 only) → the `core/tags.py` family
  adapters, which cover every format. They keep the same semantics: keys match ignoring case, and ReplayGain keys
  are written in uppercase.
- Rename fields are read through mutagen instead of TinyTag, normalized to TinyTag 2.0.0's values (track, disc and
  totals as int, MP4's missing total as 0; text exactly as stored, **not stripped**, so "  Title  " keeps its
  spaces in the new name as with the script; full dates such as "2024-05-01" kept whole). Blank text is dropped,
  as `read_fields` does. `TaggedFile.rename_fields()` implements this; parity tests run the script's `read_fields`
  on TinyTag for every fixture TinyTag reads (not WavPack, which it can't).
- The lyrics cache moves to SQLite with the same entry shape, and every result is committed (`CACHE_SAVE_EVERY`
  goes away).

**Behaviors to preserve exactly:**

*Rename:*
- mask fields and number formats; the first complete mask wins;
- `sanitize` with FORBIDDEN_CHARACTERS and the replacement character;
- lowercased extension; no-op when the name is unchanged; Windows case-only rename through `samefile`;
- never overwrite; fallback and missing-tag reporting; recursion.

*ReplayGain:*
- rsgain arguments `custom -s i -l <target> <files…>` (track mode), with rsgain's defaults for `-o` and `-I`.
- Clipping:
  - a track clips if `10^(gain/20)·peak > threshold`; its gain becomes `round(20·log10(threshold/peak) − margin, 2)`;
  - a peak of 0 gives a warning.
- Album values: album gain = min track gain, album peak = max track peak.
- LUFS comment: written into the Comment field. For MP3, that is COMM, and the old TXXX "comment" frame is removed.
- Subfolder copy rules: album data goes to SUBFOLDER_KEYWORDS folders. Track data goes to TRACK_DATA keywords
  unless excluded, for files with the same name in the album folder.
- RunLog text format and result states: OK, DONE WITH ERRORS, STOPPED, CRASHED. The "Files:" line keeps the
  script's exact `N (x FLAC, y MP3)` text, and other formats are appended only when present, e.g.
  `12 (8 FLAC, 0 MP3, 4 OPUS)`.
- Discography:
  - album discovery: sorted `os.walk`, keyword subfolders pruned;
  - only albums that are OK or DONE WITH ERRORS and have an album gain get leveled, to the minimum album gain;
  - files without an album gain are skipped;
  - one leveling line per album log, and the summary goes to the root log.

*Lyrics:*
- tag keys for artist, title and album (generalized per family through `core/tags.py`); the file-name fallback;
- duration rounding; cache key; statuses (found, instrumental, not_found, missing_tags, not_looked_up, error);
- matching (±3 s, normalized containment); the LRC timestamp regex and strip;
- embed writes synced lyrics, else plain, into the Lyrics tag.

**Deliberate changes from the scripts:**
- Formats: every format in §1 instead of FLAC/MP3 (music_tools) and FLAC/MP3/WAV (seek_lyrics).
- Lyrics: every track is looked up on LRCLIB, even with a lyrics tag. The tag is no longer a lyrics "source"; the
  Lyrics tab shows LRCLIB only.
- Auto-embed only fills empty lyrics tags.
- ReplayGain: `replaygain.log` holds the last run only (the script appended every run); see §10 B. The parity
  tests compare a single run on fresh copies, where the two agree.
- LRCLIB circuit breaker: pause and auto-retry instead of stopping.
- Junk cleanup:
  - opt-in, and files go to the Recycle Bin;
  - .m3u is gone from the default list;
  - the protections and case-by-case keeps of §10 apply, and kept `.lrc` sidecars follow their audio file's rename.
- No songs.html. Its search behaviour is kept in library search (§6), with these differences:
  - accents are ignored (except in 1–2 character ASCII queries, §6 "As built");
  - every audio file under the roots is searchable;
  - the file name, album artist and genre are searched too;
  - a track with a lyrics tag is also searched by its LRCLIB text (the app looks every track up);
  - folders, and audio files whose tags aren't read yet, are found by name (Hugo, 2026-10-16);
  - the status chips (All, With lyrics, Missing) are dropped (Hugo, 2026-10-16).

  The parity test (`tests/core/test_search_parity.py`) compares the songs whose title, artist and album are the
  same on both sides (the page showed file-name fallbacks), with LRCLIB text only on keys without a lyrics tag.
- Tags (verified by `tests/core/test_tags_parity.py`):
  - Vorbis ReplayGain keys are written in uppercase (`REPLAYGAIN_TRACK_GAIN`); the script writes the lowercase key
    it is given. Vorbis keys are case-insensitive, so readers see no difference.
  - Saving keeps the ID3 version (v2.3 stays v2.3); the script's `save()` upgraded files to v2.4.
  - WMA disc number and total are read from WM/PartOfSet; TinyTag 2.0.0 ignored them.
- `%` stays forbidden in new names, as in the script, even though Windows allows it.
