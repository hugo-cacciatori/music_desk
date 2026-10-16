# 7. Side panel and Metadata tab
The side panel sits on the right; it is resizable and collapsible. Tabs: **Metadata** | **Lyrics** (§8). With a
folder selected, the panel shows a folder summary instead: track count, duration, formats, ReplayGain and lyrics
coverage, cover, a Play button and tool shortcuts. With one text or image file selected (a log, a cue sheet, a
cover scan…), it shows a read-only preview of it instead (§20, from M9).

**Metadata tab:**
- Header: the cover (the embedded front cover, else `folder.jpg`, `cover.jpg` or `front.jpg` in the folder, else a
  placeholder), the file name and a format line (e.g. "FLAC 16-bit/44.1 kHz · 4:44 · 32 MB").
- Editable fields: Title, Artist, Album artist, Album, Track # / total, Disc # / total, Year, Genre, Composer,
  Comment, and **Lyrics** (multi-line, LRC timestamps kept as typed). The tag keys per format are in §1.
- Lyrics field: if it is empty and a same-stem `.lrc` file sits next to the track, an "Import from <name>.lrc" link
  fills the field. Saving is the usual Save, and the `.lrc` file itself isn't touched.
- Cover editing: Replace… (file picker, or drop a jpg/png onto the cover) and Remove. It writes the embedded front
  cover, with a warning for images over 1 MB.
- Read-only: path, size, codec details, duration, bitrate, sample rate, bit depth, channels, and a Loudness block
  (track gain and peak, album gain and peak, LUFS comment) managed by the ReplayGain tool.
- **Multi-selection:**
  - Shared fields (Artist, Album artist, Album, Track total, Disc # / total, Year, Genre, Composer, Comment, cover)
    show their value when it is the same on every file. When it differs, they show "(multiple values)" and stay
    untouched unless edited.
  - A clear button per field tells "clear on all" apart from "leave alone".
  - Per-track fields (Title, Track #, Lyrics) are edited one file at a time and are disabled in multi-selection.
  - A cover change applies to every selected file.
- Saving: Ctrl+S saves and Esc reverts, and the tab shows a dirty marker. Changing the selection with unsaved
  edits asks Save, Discard or Cancel. Writes go through TagWriter in the background. On failure, an error is shown
  and the fields stay dirty. Files locked by a running job are read-only, with a tooltip naming the job.
- Preserve the ID3 version on save (v2.3 stays v2.3) and leave all other frames and fields untouched.
- After a save, the index row updates. If the name no longer matches the rename masks, a hint offers
  "Rename to '03 New Title.flac'?". From M10 the hint uses the shared masks of the File names card (§21), not
  the defaults.
- The ReplayGain tool writes `LUFS Target : <x>` into Comment. Users may edit it, and the next tool run
  overwrites it.
- As built (M3 task 1):
  - Reads and saves run on one worker thread. Only the changed fields are written, and an empty field clears
    its tag. Lyrics that differ only by CRLF line endings count as unchanged.
  - The format line shows the bit depth for lossless audio and the bitrate for lossy, e.g. "FLAC 16-bit/44.1 kHz
    · 4:44 · 32 MB" or "MP3 320 kbps/44.1 kHz · 3:12 · 7.4 MB". The header says where the cover comes from
    (embedded, or the folder image's name).
  - Raw .aac (no tags) is shown read-only, with a note.
  - Cancel in the unsaved-edits prompt keeps the selection, the open folder, and the window open.
  - Every read that isn't followed by a save (the indexer, the side panel, folder images) opens the file with
    FILE_SHARE_DELETE, so the app never makes a delete or rename of that file fail while it reads.
  - Windows still refuses to rename a folder while any file under it is open, whatever the share mode
    (measured 2026-10-16: a FILE_SHARE_DELETE open of a file two levels down blocks it with "Access is denied";
    a scandir of the folder itself or a watchdog watch on the root doesn't). A tag read lasts a few
    milliseconds, so renaming a folder whose new files are being indexed can fail at that moment; retrying
    succeeds.
- As built (M3 task 2):
  - Every tag save goes through TagWriter, one at a time on its own thread. It holds a short lock on the file
    while writing.
  - Path locks are one shared registry. A job locks folder trees and a save locks its file; paths compare
    ignoring case. A lock that overlaps another is refused at once, naming the owner. A save under a job's lock
    fails with "<file> is in use by <job>. Try again when it has finished."
  - A file under a job's lock is read-only in the Metadata tab, with "Read-only while <job> is running." as its
    tooltip and note.
  - After a save, only that file is re-read into the index. Its folder totals, up to the root, are adjusted by
    the difference, so no folder rescan is needed.
- As built (M3 task 3):
  - With several files, only the shared fields that were edited or cleared are written, to every file that can
    be tagged.
  - Emptying a "(multiple values)" field by hand leaves it alone. The clear button clears it on all files (the
    placeholder then says "(cleared on all files)"). Emptying a common value clears it, as with one file.
  - Several selected items edit their audio files together. Files without tags, unreadable files and non-audio
    items are left out, with a note.
  - A failure on some files shows "Saved x of n files. Not saved: …" with the reasons, and keeps the edits for a
    retry.
  - When any of the files is under a job's lock, the whole multi-edit is read-only.
- As built (M3 task 4):
  - Replace… takes a JPEG or PNG from a file picker, or one dropped onto the cover (a drop into the app, which is
    allowed). The picture is checked by its signature and decoded on the worker thread. Over 1 MB, a
    confirmation explains that every file carries its own copy.
  - A new or removed cover is pending until Save ("New cover (not saved yet)" or "Cover removed"), and Revert
    drops it.
  - Only the embedded front cover is written or removed; other pictures stay. A folder image is shown when there
    is no embedded cover, never written, and Remove is then disabled.
  - Several files: the header shows their common embedded cover, "(multiple covers)", or "No cover". Replace or
    Remove applies to every file that can be tagged.
  - A field counts as edited only when its text differs from what was loaded, so blank text stored in a tag
    (e.g. "  ") isn't mistaken for a change.
- As built (M3 task 5), the folder summary:
  - It shows for one selected folder, and for the open folder when nothing is selected.
  - Tracks, duration, formats, ReplayGain coverage and lyrics-tag coverage count the whole subtree. "In this
    folder" counts its direct subfolders and files.
  - The cover is the folder image, else the first track's embedded front cover (the first audio file in natural
    order, searched level by level). It is read and decoded on the worker thread.
  - Play folder (enabled when the folder has tracks of its own) and Play with subfolders.
  - An LRCLIB coverage row, and a **Tools…** button (§10).
  - It refreshes when the indexer updates the folder.
- As built (M3 task 6), the .lrc import:
  - The sidecar is the same-stem `.lrc` next to the file, matched ignoring case.
  - Its text is decoded as UTF-8 (a BOM is dropped), else Windows-1252; undecodable bytes become U+FFFD instead of
    failing.
  - The link shows for one editable, unlocked file while its Lyrics field is empty, so emptying the field offers
    it again.
  - The import normalizes line endings and trims leading and trailing blank lines. The .lrc file is only read.
